"""Series and issue-list endpoints."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from pullbox.deps import ComicVineClientDep, DbDep
from pullbox.models import Issue, Series
from pullbox.schemas import (
    AddSeriesRequest,
    IssueListItem,
    MarkAllWantedResponse,
    PaginatedSeriesResponse,
    SeriesDetailResponse,
    SeriesResponse,
    SeriesSearchResult,
    SyncIssuesResponse,
    UpdateSeriesRequest,
)
from pullbox.services.arcs import enrich_issue_arcs
from pullbox.services.library_import import normalize_issue_number
from pullbox.services.queue import enqueue_issue, run_job_now

router = APIRouter(prefix="/api/series", tags=["series"])

# Application log — series/issue lifecycle events land in pullbox.log.
app_log = logging.getLogger("pullbox")


def _parse_date(value: str | None) -> date | None:
    """Parse a YYYY-MM-DD string (or None) returned by the ComicVine API."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


async def _get_series_or_404(series_id: int, db) -> Series:
    row = await db.get(Series, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Series not found")
    return row


# ── Step 4.2 — Search ComicVine ───────────────────────────────────────────────


@router.get("/search", response_model=list[SeriesSearchResult])
async def search_series(
    cv: ComicVineClientDep,
    db: DbDep,
    q: str = Query(min_length=2),
):
    results = await cv.search_series(q)

    in_library_ids: set[str] = set()
    if results:
        cv_ids = [r["comicvine_id"] for r in results]
        stmt = select(Series.comicvine_id).where(Series.comicvine_id.in_(cv_ids))
        rows = (await db.execute(stmt)).scalars().all()
        in_library_ids = set(rows)

    return [
        SeriesSearchResult(**r, in_library=r["comicvine_id"] in in_library_ids)
        for r in results
    ]


# ── Step 4.3 — Add series ─────────────────────────────────────────────────────


@router.post("/", response_model=SeriesDetailResponse, status_code=201)
async def add_series(
    body: AddSeriesRequest,
    cv: ComicVineClientDep,
    db: DbDep,
):
    # Check duplicate
    existing = (
        await db.execute(
            select(Series).where(Series.comicvine_id == body.comicvine_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Series already in library")

    volume = await cv.get_volume(body.comicvine_id)

    series = Series(
        comicvine_id=volume["comicvine_id"],
        title=volume["title"],
        publisher=volume.get("publisher"),
        start_year=volume.get("start_year"),
        cover_url=volume.get("cover_url"),
        description=volume.get("description"),
        subscribed=body.subscribed,
        auto_download=body.auto_download,
    )
    db.add(series)
    await db.flush()
    await db.refresh(series)
    app_log.info(
        "Added series to library: %r (ComicVine %s, subscribed=%s, auto_download=%s)",
        series.title,
        series.comicvine_id,
        series.subscribed,
        series.auto_download,
    )
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.4 — List and get series ───────────────────────────────────────────


@router.get("/", response_model=PaginatedSeriesResponse)
async def list_series(
    db: DbDep,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    subscribed: bool | None = Query(default=None),
    all_: bool = Query(default=False, alias="all"),
):
    # Alphabetical (case-insensitive) so pagination and the frontend's A–Z jump
    # picker share one stable ordering.
    stmt_base = select(Series).order_by(func.lower(Series.title))
    if subscribed is not None:
        stmt_base = stmt_base.where(Series.subscribed == subscribed)

    total_result = await db.execute(select(func.count()).select_from(stmt_base.subquery()))
    total = total_result.scalar_one()

    if all_:
        # Load the entire (filtered) set in one shot — used by the Series page,
        # which reveals rows client-side and needs every letter available to jump.
        rows = (await db.execute(stmt_base)).scalars().all()
        return PaginatedSeriesResponse(
            total=total,
            page=1,
            per_page=total,
            items=[SeriesResponse.model_validate(s) for s in rows],
        )

    offset = (page - 1) * per_page
    rows = (
        await db.execute(stmt_base.offset(offset).limit(per_page))
    ).scalars().all()

    return PaginatedSeriesResponse(
        total=total,
        page=page,
        per_page=per_page,
        items=[SeriesResponse.model_validate(s) for s in rows],
    )


@router.get("/{series_id}", response_model=SeriesDetailResponse)
async def get_series(series_id: int, db: DbDep):
    series = await _get_series_or_404(series_id, db)
    return SeriesDetailResponse.model_validate(series)


# ── Enrich series from ComicVine ─────────────────────────────────────────────


@router.post("/{series_id}/enrich", response_model=SeriesDetailResponse)
async def enrich_series(series_id: int, cv: ComicVineClientDep, db: DbDep):
    """Fetch full metadata from ComicVine and update the series record.

    Safe to call repeatedly — only overwrites metadata fields, never touches
    subscribed/auto_download/status or any issue rows.
    """
    series = await _get_series_or_404(series_id, db)
    volume = await cv.get_volume(series.comicvine_id)
    series.title = volume["title"]
    series.publisher = volume.get("publisher")
    series.start_year = volume.get("start_year")
    series.cover_url = volume.get("cover_url")
    series.description = volume.get("description")
    await db.flush()
    await db.refresh(series)
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.5 — Update series ─────────────────────────────────────────────────


@router.patch("/{series_id}", response_model=SeriesDetailResponse)
async def update_series(series_id: int, body: UpdateSeriesRequest, db: DbDep):
    series = await _get_series_or_404(series_id, db)

    if body.subscribed is not None:
        series.subscribed = body.subscribed
    if body.auto_download is not None:
        series.auto_download = body.auto_download

    await db.flush()
    await db.refresh(series)
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.6 — Sync issues from ComicVine ────────────────────────────────────


@router.post("/{series_id}/sync-issues", response_model=SyncIssuesResponse)
async def sync_issues(series_id: int, cv: ComicVineClientDep, db: DbDep):
    series = await _get_series_or_404(series_id, db)

    remote_issues = await cv.get_issues(series.comicvine_id)

    # Load all existing issues for this series indexed by comicvine_id
    existing_rows = (
        await db.execute(select(Issue).where(Issue.series_id == series_id))
    ).scalars().all()
    existing_by_cv_id: dict[str, Issue] = {
        i.comicvine_id: i for i in existing_rows if i.comicvine_id is not None
    }
    # Import-origin issues not yet backfilled (comicvine_id NULL): match these by
    # normalized issue number so a sync enriches them in place instead of creating
    # a duplicate row.
    unbackfilled_by_number: dict[str, Issue] = {}
    for i in existing_rows:
        if i.comicvine_id is None:
            unbackfilled_by_number.setdefault(normalize_issue_number(i.issue_number), i)

    added = 0
    updated = 0
    new_issues: list[Issue] = []

    for remote in remote_issues:
        cv_id = remote["comicvine_id"]
        cover_date = _parse_date(remote.get("cover_date"))
        store_date = _parse_date(remote.get("store_date"))

        issue = existing_by_cv_id.get(cv_id)
        if issue is None:
            issue = unbackfilled_by_number.pop(
                normalize_issue_number(remote.get("issue_number", "")), None
            )
            if issue is not None:
                issue.comicvine_id = cv_id  # adopt the un-backfilled local issue

        if issue is not None:
            # Refresh metadata only — never touch status or file_path
            issue.issue_number = remote.get("issue_number", issue.issue_number)
            issue.title = remote.get("title", issue.title)
            issue.cover_date = cover_date
            issue.store_date = store_date
            issue.cover_url = remote.get("cover_url", issue.cover_url)
            issue.description = remote.get("description", issue.description)
            existing_by_cv_id[cv_id] = issue
            updated += 1
        else:
            issue = Issue(
                series_id=series_id,
                comicvine_id=cv_id,
                issue_number=remote.get("issue_number", ""),
                title=remote.get("title"),
                cover_date=cover_date,
                store_date=store_date,
                cover_url=remote.get("cover_url"),
                description=remote.get("description"),
            )
            db.add(issue)
            new_issues.append(issue)
            added += 1

    await db.flush()

    if added:
        app_log.info(
            "Added %d new issue(s) to watch for series %r (%d updated)",
            added,
            series.title,
            updated,
        )

    # Enrich story arc membership for every issue not yet enriched (all issues on
    # first sync, only new issues thereafter). Failures are non-fatal and retried
    # on the next sync. See services/arcs.py.
    all_issues = list(existing_by_cv_id.values()) + new_issues
    await enrich_issue_arcs(db, cv, all_issues)

    if series.auto_download and new_issues:
        new_job_ids: list[int] = []
        for issue in new_issues:
            try:
                job, created = await enqueue_issue(issue.id, db)
                if created:
                    new_job_ids.append(job.id)
            except ValueError:
                pass
        if new_job_ids:
            await db.commit()
            for job_id in new_job_ids:
                asyncio.create_task(run_job_now(job_id))

    return SyncIssuesResponse(added=added, updated=updated, total=added + updated)


# ── Mark all issues as wanted ────────────────────────────────────────────────


@router.post("/{series_id}/mark-all-wanted", response_model=MarkAllWantedResponse)
async def mark_all_wanted(series_id: int, db: DbDep):
    await _get_series_or_404(series_id, db)

    rows = (
        await db.execute(
            select(Issue).where(
                Issue.series_id == series_id,
                Issue.status.in_(["unknown", "failed"]),
            )
        )
    ).scalars().all()

    for issue in rows:
        issue.status = "wanted"

    await db.flush()

    new_job_ids: list[int] = []
    for issue in rows:
        try:
            job, created = await enqueue_issue(issue.id, db)
            if created:
                new_job_ids.append(job.id)
        except ValueError:
            pass

    if new_job_ids:
        await db.commit()
        for job_id in new_job_ids:
            asyncio.create_task(run_job_now(job_id))

    return MarkAllWantedResponse(marked=len(rows))


# ── Step 4.7 — Issue list ────────────────────────────────────────────────────


@router.get("/{series_id}/issues", response_model=list[IssueListItem])
async def list_issues(
    series_id: int,
    db: DbDep,
    status: str | None = Query(default=None),
):
    await _get_series_or_404(series_id, db)

    stmt = (
        select(Issue)
        .where(Issue.series_id == series_id)
        .options(selectinload(Issue.arcs))
    )
    if status is not None:
        stmt = stmt.where(Issue.status == status)
    stmt = stmt.order_by(Issue.issue_number)

    rows = (await db.execute(stmt)).scalars().all()
    return [IssueListItem.model_validate(i) for i in rows]
