"""Dashboard API — aggregated glance at download activity, queue health,
library stats, sync status, and the pull list. Read-only aggregations over the
existing tables; the frontend polls these on the landing page.

Three endpoints with different refresh cadences:
  * GET /api/dashboard/activity — live download activity + queue counts (poll ~15s)
  * GET /api/dashboard/overview — library stats, sync status, stuck series (poll ~60s)
  * GET /api/dashboard/pull     — this week + upcoming subscribed releases
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep, MetadataProviderDep, SettingsDep
from pullbox.models import DownloadJob, ImportFile, Issue, Series, WeeklyRelease
from pullbox.schemas import (
    DashboardActivityResponse,
    DashboardIssueRef,
    DashboardJob,
    DashboardLibraryItem,
    DashboardOverviewResponse,
    DashboardPullResponse,
    DashboardRelease,
    DashboardSyncStatus,
    LibraryStats,
    QueueHealth,
    StuckSeries,
    SyncInfo,
)
from pullbox.services import sync_status as sync_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# An issue whose download job has retried this many times without success is
# considered "stuck" and surfaced for manual intervention.
STUCK_ATTEMPTS = 3

_ACTIVE_STATUSES = ("searching", "pending", "downloading")


def _job_ref(job: DownloadJob) -> DashboardJob:
    issue_ref = None
    if job.issue is not None and job.issue.series is not None:
        issue_ref = DashboardIssueRef(
            id=job.issue.id,
            issue_number=job.issue.issue_number,
            title=job.issue.title,
            cover_url=job.issue.cover_url,
            status=job.issue.status,
            series_id=job.issue.series.id,
            series_title=job.issue.series.title,
        )
    return DashboardJob(
        id=job.id,
        status=job.status,
        attempts=job.attempts,
        source_type=job.source_type,
        result_title=job.result_title,
        download_client_type=job.download_client_type,
        last_attempt_at=job.last_attempt_at,
        next_attempt_at=job.next_attempt_at,
        updated_at=job.updated_at,
        issue=issue_ref,
    )


async def _jobs_by_status(db, statuses, *, limit: int | None = None) -> list[DownloadJob]:
    q = (
        select(DownloadJob)
        .where(DownloadJob.status.in_(statuses))
        .options(selectinload(DownloadJob.issue).selectinload(Issue.series))
        .order_by(DownloadJob.updated_at.desc())
    )
    if limit is not None:
        q = q.limit(limit)
    return list((await db.execute(q)).scalars().all())


@router.get("/activity", response_model=DashboardActivityResponse)
async def activity(db: DbDep):
    """Live download activity and queue-health counts."""
    # Counts grouped by status in a single pass.
    counts_rows = (
        await db.execute(
            select(DownloadJob.status, func.count()).group_by(DownloadJob.status)
        )
    ).all()
    counts = {status: n for status, n in counts_rows}
    health = QueueHealth(
        queued=counts.get("queued", 0),
        searching=counts.get("searching", 0),
        pending=counts.get("pending", 0),
        downloading=counts.get("downloading", 0),
        failed=counts.get("failed", 0),
    )

    active = await _jobs_by_status(db, _ACTIVE_STATUSES, limit=25)
    completed = await _jobs_by_status(db, ("completed",), limit=10)
    failed = await _jobs_by_status(db, ("failed",), limit=10)

    return DashboardActivityResponse(
        queue_health=health,
        active_downloads=[_job_ref(j) for j in active],
        recent_completed=[_job_ref(j) for j in completed],
        recent_failed=[_job_ref(j) for j in failed],
    )


def _sum_file_sizes(paths: list[str]) -> int:
    """Best-effort sum of on-disk sizes. Files are stat'd; directories (a
    completed-download folder when post-processing is off) are walked one level.
    Missing/inaccessible paths are skipped."""
    total = 0
    for p in paths:
        try:
            if os.path.isfile(p):
                total += os.path.getsize(p)
            elif os.path.isdir(p):
                with os.scandir(p) as it:
                    for entry in it:
                        try:
                            if entry.is_file():
                                total += entry.stat().st_size
                        except OSError:
                            continue
        except OSError:
            continue
    return total


@router.get("/overview", response_model=DashboardOverviewResponse)
async def overview(db: DbDep, settings: SettingsDep):
    """Slower-moving library stats, sync status, and stuck subscribed series."""
    total_series = (await db.execute(select(func.count()).select_from(Series))).scalar_one()
    total_issues = (await db.execute(select(func.count()).select_from(Issue))).scalar_one()
    downloaded_issues = (
        await db.execute(
            select(func.count()).select_from(Issue).where(Issue.status == "downloaded")
        )
    ).scalar_one()

    paths = list(
        (
            await db.execute(select(Issue.file_path).where(Issue.file_path.isnot(None)))
        ).scalars().all()
    )
    storage_bytes = await asyncio.to_thread(_sum_file_sizes, paths)

    library_stats = LibraryStats(
        total_series=total_series,
        total_issues=total_issues,
        downloaded_issues=downloaded_issues,
        storage_bytes=storage_bytes,
    )

    # ── Sync status ──────────────────────────────────────────────────────────
    calendar_row = await sync_svc.get_sync(db, sync_svc.CALENDAR)
    backfill_row = await sync_svc.get_sync(db, sync_svc.IMPORT_BACKFILL)
    interval = timedelta(minutes=settings.import_sync_interval_minutes)
    next_backfill_at: datetime | None = None
    pending_files = (
        await db.execute(
            select(func.count()).select_from(ImportFile).where(ImportFile.status == "pending")
        )
    ).scalar_one()
    if pending_files > 0:
        base = backfill_row.last_run_at if backfill_row else None
        next_backfill_at = (base or datetime.now(tz=timezone.utc)) + interval

    sync_status = DashboardSyncStatus(
        calendar=SyncInfo(
            last_run_at=calendar_row.last_run_at if calendar_row else None,
            success=calendar_row.success if calendar_row else None,
            message=calendar_row.message if calendar_row else None,
        ),
        backfill=SyncInfo(
            last_run_at=backfill_row.last_run_at if backfill_row else None,
            success=backfill_row.success if backfill_row else None,
            message=backfill_row.message if backfill_row else None,
        ),
        next_backfill_at=next_backfill_at,
        import_pending=pending_files,
    )

    # ── Recently added to library ────────────────────────────────────────────
    recent_rows = (
        await db.execute(
            select(Issue)
            .where(Issue.status == "downloaded")
            .options(selectinload(Issue.series))
            .order_by(Issue.updated_at.desc())
            .limit(10)
        )
    ).scalars().all()
    recent_library = [
        DashboardLibraryItem(
            id=i.id,
            issue_number=i.issue_number,
            title=i.title,
            cover_url=i.cover_url,
            series_id=i.series_id,
            series_title=i.series.title if i.series else "",
            updated_at=i.updated_at,
        )
        for i in recent_rows
    ]

    # ── Stuck subscribed series ──────────────────────────────────────────────
    stuck_rows = (
        await db.execute(
            select(
                Series.id,
                Series.title,
                Series.publisher,
                func.count(func.distinct(Issue.id)),
                func.max(DownloadJob.attempts),
            )
            .join(Issue, Issue.series_id == Series.id)
            .join(DownloadJob, DownloadJob.issue_id == Issue.id)
            .where(
                Series.subscribed.is_(True),
                Issue.status == "wanted",
                DownloadJob.attempts >= STUCK_ATTEMPTS,
            )
            .group_by(Series.id)
            .order_by(func.max(DownloadJob.attempts).desc())
            .limit(20)
        )
    ).all()
    stuck_series = [
        StuckSeries(
            series_id=sid,
            series_title=title,
            publisher=publisher,
            wanted_count=wanted,
            max_attempts=attempts or 0,
        )
        for sid, title, publisher, wanted, attempts in stuck_rows
    ]

    return DashboardOverviewResponse(
        library_stats=library_stats,
        sync_status=sync_status,
        recent_library=recent_library,
        stuck_series=stuck_series,
    )


def _iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-{iso.week:02d}"


async def _releases_for_range(
    db, start: date, end: date, *, subscribed_only: bool
) -> list[DashboardRelease]:
    q = (
        select(WeeklyRelease)
        .join(Issue, WeeklyRelease.issue_id == Issue.id)
        .join(Series, Issue.series_id == Series.id)
        .where(WeeklyRelease.release_date >= start, WeeklyRelease.release_date <= end)
        .options(selectinload(WeeklyRelease.issue).selectinload(Issue.series))
        .order_by(WeeklyRelease.release_date.asc(), Series.title.asc())
    )
    if subscribed_only:
        q = q.where(Series.subscribed.is_(True))
    rows = (await db.execute(q)).scalars().all()
    return [
        DashboardRelease(
            issue_id=r.issue.id,
            issue_number=r.issue.issue_number,
            title=r.issue.title,
            cover_url=r.issue.cover_url,
            status=r.issue.status,
            release_date=r.release_date,
            series_id=r.issue.series.id,
            series_title=r.issue.series.title,
            publisher=r.issue.series.publisher,
            subscribed=r.issue.series.subscribed,
        )
        for r in rows
    ]


@router.get("/pull", response_model=DashboardPullResponse)
async def pull(db: DbDep, provider: MetadataProviderDep, settings: SettingsDep):
    """This week's releases (all) + upcoming subscribed releases.

    Refreshes only the *current* week from the metadata provider (one API call,
    guarded by the shared rate limiter) so the landing page stays fresh without a
    burst of calls; upcoming weeks are read from whatever the pull list / nightly
    refresh has already cached.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    if settings.metadata_configured:
        from pullbox.routers.releases import _refresh_week  # noqa: PLC0415

        try:
            await _refresh_week(db, provider, monday, sunday)
            await sync_svc.record_sync(
                db, sync_svc.CALENDAR, success=True, message="Synced current week"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard pull: current-week refresh failed", exc_info=True)
            await sync_svc.record_sync(
                db,
                sync_svc.CALENDAR,
                success=False,
                message=str(exc) or "provider fetch failed",
            )

    this_week = await _releases_for_range(db, monday, sunday, subscribed_only=False)

    look = max(settings.pull_list_lookahead_weeks, 1)
    up_start = sunday + timedelta(days=1)
    up_end = up_start + timedelta(weeks=look) - timedelta(days=1)
    upcoming = await _releases_for_range(db, up_start, up_end, subscribed_only=True)

    return DashboardPullResponse(
        week=_iso_week(today), this_week=this_week, upcoming=upcoming
    )
