"""Weekly releases API: GET /api/releases/weekly."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pullbox.clients.metadata import PROVIDER_ERRORS, ids_for
from pullbox.deps import DbDep, MetadataProviderDep, SettingsDep
from pullbox.models import Issue, Series, WeeklyRelease
from pullbox.schemas import ReleaseIssueSummary, ReleaseSeriesSummary, WeeklyReleaseResponse
from pullbox.services.dedupe import find_issue_for_release, find_series_for_release

# Bound concurrent volume lookups during publisher enrichment so a busy week
# doesn't fan out dozens of simultaneous ComicVine requests.
_ENRICH_CONCURRENCY = 5

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/releases", tags=["releases"])

# Holds strong references to fire-and-forget tasks so they aren't GC'd mid-run.
_background_tasks: set = set()


def _log_task_exception(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()):
        logger.error("Background refresh task failed", exc_info=exc)


def _current_week_str() -> str:
    """Return today's ISO week as 'YYYY-WW'."""
    today = date.today()
    iso = today.isocalendar()
    return f"{iso.year}-{iso.week:02d}"


def _week_date_range(week_str: str) -> tuple[date, date]:
    """Parse 'YYYY-WW' and return (monday, sunday) of that ISO week.

    Raises ValueError for malformed input or out-of-range week numbers.
    """
    year_str, week_str_part = week_str.split("-", 1)
    year = int(year_str)
    week = int(week_str_part)
    monday = date.fromisocalendar(year, week, 1)
    sunday = date.fromisocalendar(year, week, 7)
    return monday, sunday


def _parse_date_str(value: object) -> date | None:
    """Parse '2025-05-01' or '2025-05-01T00:00:00' to a date. Returns None on failure."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


# Series/Issue matching lives in services/dedupe.py because the scheduler's
# calendar refresh needs the identical logic; two divergent copies is how the two
# id spaces drifted apart in the first place.


async def _refresh_week(db, provider, monday: date, sunday: date) -> None:
    """Fetch this week's releases from the provider and upsert into
    Series/Issue/WeeklyRelease.

    Uses the caller's DB session so all writes land in the same transaction.
    Never overwrites Issue.status on existing rows.
    """
    releases = await provider.get_weekly_releases(monday.isoformat(), sunday.isoformat())

    # Series that still lack a publisher after the upsert loop, keyed by local id so
    # each series is enriched at most once per refresh.
    needs_publisher: dict[int, Series] = {}

    for release_data in releases:
        series_ids = ids_for(release_data["series"])
        series = await find_series_for_release(
            db, series_ids, release_data["series"].get("title")
        )
        if series is None:
            series = Series(
                metron_id=series_ids.get("metron_id"),
                comicvine_id=series_ids.get("comicvine_id"),
                title=release_data["series"].get("title", "Unknown Series"),
            )
            db.add(series)
            await db.flush()

        # The weekly-issues endpoint returns only a reduced series object (id + name),
        # so publisher must come from a per-series lookup. Collect the ones missing it.
        if series.publisher is None:
            needs_publisher[series.id] = series

        issue = await find_issue_for_release(
            db,
            ids_for(release_data),
            series.id,
            str(release_data.get("issue_number", "")),
        )
        if issue is None:
            issue = Issue(
                series_id=series.id,
                metron_id=release_data.get("metron_id"),
                comicvine_id=release_data.get("comicvine_id"),
                issue_number=str(release_data.get("issue_number", "")),
                title=release_data.get("title"),
                store_date=_parse_date_str(release_data.get("store_date")),
                cover_url=release_data.get("cover_url"),
                status="unknown",
            )
            db.add(issue)
            await db.flush()

        source = "metron" if release_data.get("metron_id") else "comicvine"
        release_date = issue.store_date or monday
        result = await db.execute(
            select(WeeklyRelease).where(
                WeeklyRelease.issue_id == issue.id,
                WeeklyRelease.release_date == release_date,
            )
        )
        if result.scalar_one_or_none() is None:
            db.add(WeeklyRelease(issue_id=issue.id, release_date=release_date, source=source))

    await _enrich_publishers(provider, needs_publisher)

    await db.flush()


async def _enrich_publishers(provider, series_by_id: dict[int, Series]) -> None:
    """Fill in Series.publisher (and start_year) via per-series provider lookups.

    Runs at most ``_ENRICH_CONCURRENCY`` lookups in parallel. Failures — including
    rate-limit exhaustion — are swallowed per series so a missing publisher never
    aborts the refresh; those series simply group under "Unknown Publisher" and get
    retried on the next refresh (this fetch only targets series still missing one).
    """
    if not series_by_id:
        return

    semaphore = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def enrich(series: Series) -> None:
        async with semaphore:
            try:
                volume = await provider.get_volume(**ids_for(series))
            except PROVIDER_ERRORS:
                logger.debug("Publisher enrichment failed for series %s", series.id, exc_info=True)
                return
        series.publisher = volume.get("publisher")
        if series.start_year is None and volume.get("start_year") is not None:
            series.start_year = volume["start_year"]

    await asyncio.gather(*(enrich(s) for s in series_by_id.values()))


@router.post("/refresh", status_code=202)
async def trigger_refresh():
    """Manually trigger a full multi-week calendar refresh (dev/debug helper)."""
    from pullbox.scheduler import nightly_calendar_refresh  # noqa: PLC0415

    task = asyncio.create_task(nightly_calendar_refresh())
    _background_tasks.add(task)
    task.add_done_callback(_log_task_exception)
    return {"detail": "Calendar refresh started in background"}


@router.get("/weekly", response_model=list[WeeklyReleaseResponse])
async def weekly_releases(
    db: DbDep,
    provider: MetadataProviderDep,
    settings: SettingsDep,
    week: str | None = None,
):
    """Fetch releases from the metadata provider for the given ISO week, upsert into
    DB, and return results.

    Falls back to cached DB data if no provider is configured or it is unreachable.
    """
    if week is None:
        week = _current_week_str()

    try:
        monday, sunday = _week_date_range(week)
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="Invalid week format — expected YYYY-WW")

    if settings.metadata_configured:
        from pullbox.services import sync_status as sync_svc  # noqa: PLC0415

        try:
            await _refresh_week(db, provider, monday, sunday)
            await sync_svc.record_sync(
                db, sync_svc.CALENDAR, success=True, message=f"Synced week {week}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weekly_releases: provider fetch failed for %s, returning cached data",
                week,
                exc_info=True,
            )
            await sync_svc.record_sync(
                db,
                sync_svc.CALENDAR,
                success=False,
                message=str(exc) or "ComicVine fetch failed",
            )

    result = await db.execute(
        select(WeeklyRelease)
        .join(Issue, WeeklyRelease.issue_id == Issue.id)
        .join(Series, Issue.series_id == Series.id)
        .where(
            WeeklyRelease.release_date >= monday,
            WeeklyRelease.release_date <= sunday,
        )
        .options(selectinload(WeeklyRelease.issue).selectinload(Issue.series))
        .order_by(Series.title.asc(), Issue.issue_number.asc())
    )
    releases = result.scalars().all()

    return [
        WeeklyReleaseResponse(
            id=r.id,
            release_date=r.release_date,
            pulled=r.pulled,
            issue=ReleaseIssueSummary.model_validate(r.issue),
            series=ReleaseSeriesSummary.model_validate(r.issue.series),
        )
        for r in releases
    ]
