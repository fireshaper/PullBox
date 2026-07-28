"""Story Arcs API — browse arcs, subscribe to them, and fill in their gaps.

Drives the Story Arcs page. The read endpoints are served **entirely from cached
DB rows**: an arc's members are whatever PullBox has linked locally, so browsing
never spends the shared metadata budget no matter how many arcs a library has
accumulated. Reaching out to the provider is always an explicit action —
``POST /{id}/sync`` (or subscribing, which schedules one) — because that is the
call that costs one lookup per member the library doesn't own.

The write side lives in ``services/arc_sync.py``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep, MetadataProviderDep
from pullbox.models import Issue, StoryArc, issue_story_arcs
from pullbox.schemas import (
    ArcDownloadResponse,
    ArcIssueRow,
    ArcSyncResponse,
    StoryArcListItem,
    StoryArcPageDetail,
    StoryArcUpdate,
)
from pullbox.services.arc_sync import (
    DEFAULT_BUDGET,
    enqueue_arc_missing,
    resolve_arc_members,
)
from pullbox.services.queue import run_job_now

logger = logging.getLogger(__name__)
app_log = logging.getLogger("pullbox")

router = APIRouter(prefix="/api/arcs", tags=["arcs"])

async def _get_arc_or_404(arc_id: int, db) -> StoryArc:
    arc = await db.get(StoryArc, arc_id)
    if arc is None:
        raise HTTPException(status_code=404, detail="Story arc not found")
    return arc


async def _counts_by_arc(db, arc_ids: list[int]) -> dict[int, dict[str, int]]:
    """Per-arc member tallies in one grouped query (avoids an N+1 over arcs)."""
    if not arc_ids:
        return {}
    rows = (
        await db.execute(
            select(
                issue_story_arcs.c.story_arc_id,
                func.count(Issue.id),
                func.sum(case((Issue.status == "downloaded", 1), else_=0)),
                func.sum(case((Issue.status == "wanted", 1), else_=0)),
                func.count(func.distinct(Issue.series_id)),
            )
            .select_from(issue_story_arcs)
            .join(Issue, Issue.id == issue_story_arcs.c.issue_id)
            .where(issue_story_arcs.c.story_arc_id.in_(arc_ids))
            .group_by(issue_story_arcs.c.story_arc_id)
        )
    ).all()
    return {
        arc_id: {
            "owned": owned or 0,
            "downloaded": downloaded or 0,
            "wanted": wanted or 0,
            "series_count": series_count or 0,
        }
        for arc_id, owned, downloaded, wanted, series_count in rows
    }


def _list_item(arc: StoryArc, counts: dict[str, int]) -> StoryArcListItem:
    return StoryArcListItem(
        id=arc.id,
        metron_id=arc.metron_id,
        comicvine_id=arc.comicvine_id,
        name=arc.name,
        publisher=arc.publisher,
        cover_url=arc.cover_url,
        subscribed=arc.subscribed,
        auto_download=arc.auto_download,
        total=arc.count_of_issue_appearances,
        owned=counts.get("owned", 0),
        downloaded=counts.get("downloaded", 0),
        wanted=counts.get("wanted", 0),
        series_count=counts.get("series_count", 0),
        detail_synced_at=arc.detail_synced_at,
    )


# Registered as "" rather than "/" so GET /api/arcs matches exactly and never
# triggers the project-wide 307-redirect gotcha (see architecture.md).
@router.get("", response_model=list[StoryArcListItem])
async def list_arcs(
    db: DbDep,
    q: str | None = Query(None, description="Case-insensitive name filter"),
    subscribed: bool | None = Query(None, description="Filter by subscription state"),
) -> list[StoryArcListItem]:
    """Every arc PullBox knows about, with how much of each the library holds.

    Arcs come from issue enrichment (a series sync records the arcs its issues
    belong to), so this list grows as the library is synced — there is no separate
    arc discovery step.
    """
    stmt = select(StoryArc).order_by(func.lower(StoryArc.name))
    if q:
        stmt = stmt.where(StoryArc.name.ilike(f"%{q.strip()}%"))
    if subscribed is not None:
        stmt = stmt.where(StoryArc.subscribed.is_(subscribed))

    arcs = (await db.execute(stmt)).scalars().all()
    counts = await _counts_by_arc(db, [a.id for a in arcs])
    return [_list_item(arc, counts.get(arc.id, {})) for arc in arcs]


@router.get("/{arc_id}", response_model=StoryArcPageDetail)
async def get_arc(arc_id: int, db: DbDep) -> StoryArcPageDetail:
    """One arc plus every issue PullBox tracks for it, in reading order.

    Ordering is by store date then series then issue number: an arc is a reading
    sequence across titles, so publication order is the only ordering that reads
    correctly. Issues without a date sort last rather than jumbling the front.
    """
    arc = await _get_arc_or_404(arc_id, db)

    rows = (
        (
            await db.execute(
                select(Issue)
                .join(Issue.arcs)
                .where(StoryArc.id == arc_id)
                .options(selectinload(Issue.series))
                .order_by(
                    Issue.store_date.is_(None),
                    Issue.store_date,
                    Issue.cover_date,
                    Issue.series_id,
                    Issue.issue_number,
                )
            )
        )
        .scalars()
        .all()
    )
    counts = await _counts_by_arc(db, [arc_id])
    base = _list_item(arc, counts.get(arc_id, {}))

    return StoryArcPageDetail(
        **base.model_dump(),
        description=arc.description,
        issues=[
            ArcIssueRow(
                id=i.id,
                series_id=i.series_id,
                series_title=i.series.title,
                issue_number=i.issue_number,
                title=i.title,
                cover_url=i.cover_url,
                cover_date=i.cover_date,
                store_date=i.store_date,
                status=i.status,
                has_file=bool(i.file_path),
            )
            for i in rows
        ],
    )


@router.patch("/{arc_id}", response_model=StoryArcListItem)
async def update_arc(arc_id: int, body: StoryArcUpdate, db: DbDep) -> StoryArcListItem:
    """Toggle an arc's subscription flags.

    Subscribing does not sync inline — the caller shouldn't wait on a member list
    that may cost dozens of provider calls. The background arc sync picks the arc
    up on its next run; the page's Sync button is there for an immediate one.
    """
    arc = await _get_arc_or_404(arc_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(arc, field, value)
    await db.flush()
    app_log.info(
        "Story arc %r: subscribed=%s auto_download=%s",
        arc.name,
        arc.subscribed,
        arc.auto_download,
    )
    counts = await _counts_by_arc(db, [arc_id])
    return _list_item(arc, counts.get(arc_id, {}))


@router.post("/{arc_id}/sync", response_model=ArcSyncResponse)
async def sync_arc(
    arc_id: int,
    provider: MetadataProviderDep,
    db: DbDep,
    download: bool = Query(
        False, description="Also enqueue every newly-discovered issue for download"
    ),
    budget: int = Query(
        DEFAULT_BUDGET,
        ge=1,
        le=200,
        description="Max provider lookups (one per member not already held)",
    ),
) -> ArcSyncResponse:
    """Fetch the arc's member list and create local rows for what's missing.

    Runs inline — the user pressed the button and is watching. ``budget`` caps the
    provider spend; anything left over is reported in ``remaining`` and picked up
    by the next sync (manual or scheduled).
    """
    arc = await _get_arc_or_404(arc_id, db)
    result = await resolve_arc_members(
        db, provider, arc, budget=budget, download=download
    )

    # Commit before dispatching: run_job_now opens its own session and would
    # otherwise race rows this request hasn't written yet.
    await db.commit()
    for job_id in result.job_ids:
        asyncio.create_task(run_job_now(job_id))

    return ArcSyncResponse(
        members=result.members,
        in_library=result.in_library,
        added=result.added,
        enqueued=result.enqueued,
        failed=result.failed,
        remaining=result.remaining,
        rate_limited=result.rate_limited,
        message=_sync_message(result),
    )


def _sync_message(result) -> str:
    if result.rate_limited and result.added == 0:
        return "Metadata provider rate limit reached — nothing was added. Try again later."
    parts = [f"{result.members} issues in this arc", f"{result.in_library} already tracked"]
    if result.added:
        parts.append(f"{result.added} added as wanted")
    if result.enqueued:
        parts.append(f"{result.enqueued} queued for download")
    if result.failed:
        parts.append(f"{result.failed} could not be resolved")
    if result.remaining:
        parts.append(f"{result.remaining} left for the next sync")
    if result.rate_limited:
        parts.append("stopped early on a provider rate limit")
    return ", ".join(parts) + "."


@router.post("/{arc_id}/download-missing", response_model=ArcDownloadResponse)
async def download_missing(arc_id: int, db: DbDep) -> ArcDownloadResponse:
    """Queue every tracked issue in the arc that isn't already downloaded.

    No provider call: this acts on members already linked to the arc. Run a sync
    first if the arc's member list has never been resolved.
    """
    arc = await _get_arc_or_404(arc_id, db)
    job_ids = await enqueue_arc_missing(db, arc)
    await db.commit()
    for job_id in job_ids:
        asyncio.create_task(run_job_now(job_id))

    if not job_ids:
        return ArcDownloadResponse(
            enqueued=0, message="Nothing to download — every tracked issue is already in hand."
        )
    return ArcDownloadResponse(
        enqueued=len(job_ids),
        message=f"Queued {len(job_ids)} issue{'s' if len(job_ids) != 1 else ''} for download.",
    )
