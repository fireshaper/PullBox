"""Story arc enrichment and detail assembly.

`story_arc_credits` is only available on ComicVine's single-issue detail endpoint,
so arc membership must be fetched one issue at a time. `enrich_issue_arcs` does
this with bounded concurrency and stores the result so the issue list can render
badges cheaply. `get_issue_arc_detail` assembles the expandable-panel payload by
fetching each arc's full cross-series member list live and matching members
against the local library.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pullbox.clients.comicvine import ComicVineClient, ComicVineError
from pullbox.models import Issue, StoryArc
from pullbox.schemas import ArcMemberIssue, StoryArcDetail

logger = logging.getLogger(__name__)

_ENRICH_CONCURRENCY = 5


async def _get_or_create_arc(
    db: AsyncSession,
    cache: dict[str, StoryArc],
    comicvine_id: str,
    name: str,
) -> StoryArc:
    if comicvine_id in cache:
        return cache[comicvine_id]
    arc = (
        await db.execute(select(StoryArc).where(StoryArc.comicvine_id == comicvine_id))
    ).scalar_one_or_none()
    if arc is None:
        arc = StoryArc(comicvine_id=comicvine_id, name=name or "")
        db.add(arc)
        await db.flush()
    cache[comicvine_id] = arc
    return arc


async def enrich_issue_arcs(
    db: AsyncSession,
    cv: ComicVineClient,
    issues: list[Issue],
) -> int:
    """Fetch story arc membership for any of `issues` not yet enriched.

    HTTP fetches run concurrently (bounded); DB writes are applied sequentially
    on the single session afterwards. Issues whose detail fetch fails keep
    `arcs_synced_at = NULL` so a later sync retries them. Returns the count of
    issues successfully enriched.
    """
    target_ids = [i.id for i in issues if i.arcs_synced_at is None]
    if not target_ids:
        return 0

    # Reload targets with the arcs relationship eagerly loaded so appending to
    # `issue.arcs` never triggers a lazy load in async context.
    targets = (
        await db.execute(
            select(Issue)
            .where(Issue.id.in_(target_ids))
            .options(selectinload(Issue.arcs))
        )
    ).scalars().all()

    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def fetch(issue: Issue) -> tuple[Issue, dict | None]:
        async with sem:
            try:
                return issue, await cv.get_issue(issue.comicvine_id)
            except ComicVineError as exc:
                logger.warning("Arc enrich failed for issue %s: %s", issue.comicvine_id, exc)
                return issue, None

    results = await asyncio.gather(*(fetch(i) for i in targets))

    now = datetime.utcnow()
    arc_cache: dict[str, StoryArc] = {}
    enriched = 0
    for issue, detail in results:
        if detail is None:
            continue
        existing_ids = {a.comicvine_id for a in issue.arcs}
        for arc_data in detail["story_arcs"]:
            if arc_data["comicvine_id"] in existing_ids:
                continue
            arc = await _get_or_create_arc(
                db, arc_cache, arc_data["comicvine_id"], arc_data["name"]
            )
            issue.arcs.append(arc)
        issue.arcs_synced_at = now
        enriched += 1

    await db.flush()
    return enriched


async def _build_arc_detail(
    db: AsyncSession,
    cv: ComicVineClient,
    arc: StoryArc,
) -> StoryArcDetail:
    """Fetch an arc's live member list and match members to the local library."""
    try:
        data = await cv.get_story_arc(arc.comicvine_id)
    except ComicVineError as exc:
        logger.warning("Story arc detail failed for %s: %s", arc.comicvine_id, exc)
        # Fall back to whatever we already know; panel still shows the arc.
        return StoryArcDetail(
            id=arc.id,
            comicvine_id=arc.comicvine_id,
            name=arc.name,
            publisher=arc.publisher,
            cover_url=arc.cover_url,
            description=arc.description,
            count_of_issue_appearances=arc.count_of_issue_appearances,
            issues=[],
        )

    # Cache the freshly-fetched metadata on the arc row.
    arc.name = data["name"] or arc.name
    arc.publisher = data["publisher"]
    arc.cover_url = data["cover_url"]
    arc.description = data["description"]
    arc.count_of_issue_appearances = data["count_of_issue_appearances"]
    arc.detail_synced_at = datetime.utcnow()

    member_ids = [m["comicvine_id"] for m in data["issues"]]
    local: dict[str, Issue] = {}
    if member_ids:
        rows = (
            await db.execute(
                select(Issue)
                .where(Issue.comicvine_id.in_(member_ids))
                .options(selectinload(Issue.series))
            )
        ).scalars().all()
        local = {i.comicvine_id: i for i in rows}

    members: list[ArcMemberIssue] = []
    for m in data["issues"]:
        li = local.get(m["comicvine_id"])
        members.append(
            ArcMemberIssue(
                comicvine_id=m["comicvine_id"],
                name=m["name"],
                site_detail_url=m["site_detail_url"],
                in_library=li is not None,
                local_issue_id=li.id if li else None,
                local_series_id=li.series_id if li else None,
                local_series_title=li.series.title if li else None,
                local_issue_number=li.issue_number if li else None,
                local_status=li.status if li else None,
            )
        )

    return StoryArcDetail(
        id=arc.id,
        comicvine_id=arc.comicvine_id,
        name=arc.name,
        publisher=arc.publisher,
        cover_url=arc.cover_url,
        description=arc.description,
        count_of_issue_appearances=arc.count_of_issue_appearances,
        issues=members,
    )


async def get_issue_arc_detail(
    db: AsyncSession,
    cv: ComicVineClient,
    issue: Issue,
) -> list[StoryArcDetail]:
    """Return every arc `issue` belongs to, each with its full member list.

    Enriches the issue on demand first, so the panel works even before a full
    series sync has populated arc membership.
    """
    await enrich_issue_arcs(db, cv, [issue])

    arcs = (
        await db.execute(
            select(StoryArc)
            .join(StoryArc.issues)
            .where(Issue.id == issue.id)
            .order_by(StoryArc.name)
        )
    ).scalars().all()

    return [await _build_arc_detail(db, cv, arc) for arc in arcs]
