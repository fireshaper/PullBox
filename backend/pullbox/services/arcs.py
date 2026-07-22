"""Story arc enrichment and detail assembly.

Arc membership is fetched from the metadata provider's single-issue detail endpoint
(Metron returns ``arcs`` inline; ComicVine returns ``story_arc_credits``), one issue
at a time. `enrich_issue_arcs` does this with bounded concurrency and stores the
result so the issue list can render badges cheaply. `get_issue_arc_detail` assembles
the expandable-panel payload by fetching each arc's full cross-series member list live
and matching members against the local library.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pullbox.clients.metadata import PROVIDER_ERRORS, MetadataProvider, ids_for
from pullbox.models import Issue, StoryArc
from pullbox.schemas import ArcMemberIssue, StoryArcDetail

logger = logging.getLogger(__name__)

_ENRICH_CONCURRENCY = 5


def _arc_key(arc_data: dict) -> str:
    """A stable cache/identity key for an arc record, preferring the Metron id."""
    return arc_data.get("metron_id") or arc_data.get("comicvine_id") or ""


async def _get_or_create_arc(
    db: AsyncSession,
    cache: dict[str, StoryArc],
    arc_data: dict,
) -> StoryArc:
    key = _arc_key(arc_data)
    if key in cache:
        return cache[key]
    clauses = []
    if arc_data.get("metron_id"):
        clauses.append(StoryArc.metron_id == arc_data["metron_id"])
    if arc_data.get("comicvine_id"):
        clauses.append(StoryArc.comicvine_id == arc_data["comicvine_id"])
    arc = (
        (await db.execute(select(StoryArc).where(or_(*clauses)))).scalar_one_or_none()
        if clauses
        else None
    )
    if arc is None:
        arc = StoryArc(
            metron_id=arc_data.get("metron_id"),
            comicvine_id=arc_data.get("comicvine_id"),
            name=arc_data.get("name") or "",
        )
        db.add(arc)
        await db.flush()
    cache[key] = arc
    return arc


async def enrich_issue_arcs(
    db: AsyncSession,
    provider: MetadataProvider,
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
                return issue, await provider.get_issue(**ids_for(issue))
            except PROVIDER_ERRORS as exc:
                logger.warning("Arc enrich failed for issue %s: %s", issue.id, exc)
                return issue, None

    results = await asyncio.gather(*(fetch(i) for i in targets))

    now = datetime.utcnow()
    arc_cache: dict[str, StoryArc] = {}
    enriched = 0
    for issue, detail in results:
        if detail is None:
            continue
        existing_keys = {a.metron_id or a.comicvine_id for a in issue.arcs}
        for arc_data in detail["story_arcs"]:
            if _arc_key(arc_data) in existing_keys:
                continue
            arc = await _get_or_create_arc(db, arc_cache, arc_data)
            issue.arcs.append(arc)
        issue.arcs_synced_at = now
        enriched += 1

    await db.flush()
    return enriched


async def _build_arc_detail(
    db: AsyncSession,
    provider: MetadataProvider,
    arc: StoryArc,
) -> StoryArcDetail:
    """Fetch an arc's live member list and match members to the local library."""
    try:
        data = await provider.get_story_arc(**ids_for(arc))
    except PROVIDER_ERRORS as exc:
        logger.warning("Story arc detail failed for arc %s: %s", arc.id, exc)
        # Fall back to whatever we already know; panel still shows the arc.
        return StoryArcDetail(
            id=arc.id,
            metron_id=arc.metron_id,
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

    # Match members to local issues by whichever id each member carries.
    member_metron = [m["metron_id"] for m in data["issues"] if m.get("metron_id")]
    member_cv = [m["comicvine_id"] for m in data["issues"] if m.get("comicvine_id")]
    local_by_metron: dict[str, Issue] = {}
    local_by_cv: dict[str, Issue] = {}
    if member_metron or member_cv:
        clauses = []
        if member_metron:
            clauses.append(Issue.metron_id.in_(member_metron))
        if member_cv:
            clauses.append(Issue.comicvine_id.in_(member_cv))
        rows = (
            await db.execute(
                select(Issue).where(or_(*clauses)).options(selectinload(Issue.series))
            )
        ).scalars().all()
        local_by_metron = {i.metron_id: i for i in rows if i.metron_id}
        local_by_cv = {i.comicvine_id: i for i in rows if i.comicvine_id}

    members: list[ArcMemberIssue] = []
    for m in data["issues"]:
        li = local_by_metron.get(m.get("metron_id")) or local_by_cv.get(m.get("comicvine_id"))
        members.append(
            ArcMemberIssue(
                metron_id=m.get("metron_id"),
                comicvine_id=m.get("comicvine_id"),
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
        metron_id=arc.metron_id,
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
    provider: MetadataProvider,
    issue: Issue,
) -> list[StoryArcDetail]:
    """Return every arc `issue` belongs to, each with its full member list.

    Enriches the issue on demand first, so the panel works even before a full
    series sync has populated arc membership.
    """
    await enrich_issue_arcs(db, provider, [issue])

    arcs = (
        await db.execute(
            select(StoryArc)
            .join(StoryArc.issues)
            .where(Issue.id == issue.id)
            .order_by(StoryArc.name)
        )
    ).scalars().all()

    return [await _build_arc_detail(db, provider, arc) for arc in arcs]
