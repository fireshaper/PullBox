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
from pullbox.services.dedupe import (
    adopt_provider_ids,
    find_issue_for_release,
    find_series_for_release,
)
from pullbox.services.queue import enqueue_issue, run_job_now

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


# Series currently being looked up, so two quick page loads of the same week
# don't both pay for the same searches. Ids are removed when the task finishes.
_resolving: set[int] = set()


async def _resolve_links(settings, series_ids: list[int]) -> None:
    """Search ComicVine for the ids of unlinked series on the week just served.

    Metron's weekly feed carries no ``cv_id`` and most Metron series have none at
    all, so without this the pull list stays unlinked until the background sweep
    works its way round — which for the week the user is looking at right now is
    the wrong time to wait. ``only_unattempted`` keeps this cheap: each series is
    searched once ever from here, so browsing weeks costs nothing after the
    first visit.
    """
    import pullbox.database as db_module  # noqa: PLC0415
    import pullbox.deps as deps_module  # noqa: PLC0415
    from pullbox.services.cv_link import resolve_series_cv_ids  # noqa: PLC0415

    if not settings.comicvine_api_key or db_module.AsyncSessionLocal is None:
        return

    provider = deps_module.build_metadata_provider(settings)
    try:
        await resolve_series_cv_ids(
            db_module.AsyncSessionLocal,
            provider.comicvine_source,
            series_ids=series_ids,
            limit=len(series_ids),
            only_unattempted=True,
        )
    finally:
        await provider.close()
        _resolving.difference_update(series_ids)


def _dispatch_link_resolution(settings, releases) -> None:
    """Kick off ComicVine-id recovery for the unlinked series in ``releases``.

    Detached rather than awaited: a search per series would add seconds to the
    page load, and the links are for the *next* render either way (the row is
    already on screen by the time an id lands).
    """
    if not settings.comicvine_api_key:
        return
    unlinked = {
        r.issue.series.id
        for r in releases
        if r.issue.series.comicvine_id is None and r.issue.series.id not in _resolving
    }
    if not unlinked:
        return
    _resolving.update(unlinked)
    task = asyncio.create_task(_resolve_links(settings, sorted(unlinked)))
    _background_tasks.add(task)
    task.add_done_callback(_log_task_exception)


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


async def _refresh_week(db, provider, monday: date, sunday: date) -> list[int]:
    """Fetch this week's releases from the provider and upsert into
    Series/Issue/WeeklyRelease.

    Uses the caller's DB session. Commits the upserts before the per-series
    publisher lookups (network) so no write lock is held across HTTP; the
    enrichment writes are left for the caller to commit.
    Never overwrites Issue.status on existing rows.

    Returns the ids of DownloadJobs created for newly-discovered issues on
    auto_download series; the caller must commit before acting on them.

    Those job ids are created for newly-discovered issues on
    auto_download series. This is the path that actually runs on every calendar
    and pull-list page load; the equivalent enqueue in nightly_calendar_refresh
    only fires from the manual Refresh button, so without this an auto_download
    series would quietly accumulate 'unknown' issues that nothing ever queues.

    ComicVine-id recovery is not driven from here — ``weekly_releases`` resolves
    every unlinked series on the week it is about to return, which covers rows
    this refresh created *and* rows an earlier one left unlinked.
    """
    auto_job_ids: list[int] = []
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

            # Matches Series.auto_download's documented contract — "enqueue each
            # newly-created row" — and the two other creation paths
            # (series sync-issues, nightly_calendar_refresh). Scoped to issues
            # created just now, so an existing back catalogue is never swept in.
            if series.auto_download:
                try:
                    job, created = await enqueue_issue(issue.id, db)
                    if created:
                        auto_job_ids.append(job.id)
                except ValueError:
                    logger.warning(
                        "_refresh_week: could not auto-enqueue new issue %d (%s #%s)",
                        issue.id,
                        series.title,
                        issue.issue_number,
                        exc_info=True,
                    )

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

    # The flushes above hold SQLite's single write lock until commit, and the
    # enrichment below is one rate-limited provider call per series — minutes on
    # a week full of new series. Holding the lock across that starved every other
    # writer past busy_timeout ("database is locked"), APScheduler's own
    # bookkeeping included, which crashed the scheduler. Commit first so the
    # lookups run with no write pending; their results land in a short second
    # transaction (the caller's commit).
    await db.commit()
    await _enrich_publishers(db, provider, needs_publisher)

    await db.flush()

    if auto_job_ids:
        logger.info(
            "_refresh_week: auto-enqueued %d new issue(s) on auto_download series",
            len(auto_job_ids),
        )
    return auto_job_ids


async def _enrich_publishers(db, provider, series_by_id: dict[int, Series]) -> None:
    """Fill in Series.publisher (and start_year) via per-series provider lookups.

    Runs at most ``_ENRICH_CONCURRENCY`` lookups in parallel. Failures — including
    rate-limit exhaustion — are swallowed per series so a missing publisher never
    aborts the refresh; those series simply group under "Unknown Publisher" and get
    retried on the next refresh (this fetch only targets series still missing one).

    The detail record also carries the cross-source id the weekly feed lacks, so
    it is adopted here too — that is what makes a Metron-only series linkable to
    its ComicVine page. Only the HTTP fan-out is concurrent; the session is not
    safe to share across tasks, so the writes are applied in a second, serial pass.
    """
    if not series_by_id:
        return

    semaphore = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def fetch(series: Series) -> dict | None:
        async with semaphore:
            try:
                return await provider.get_volume(**ids_for(series))
            except PROVIDER_ERRORS:
                logger.debug("Publisher enrichment failed for series %s", series.id, exc_info=True)
                return None

    volumes = await asyncio.gather(*(fetch(s) for s in series_by_id.values()))

    for series, volume in zip(series_by_id.values(), volumes, strict=True):
        if volume is None:
            continue
        series.publisher = volume.get("publisher")
        if series.start_year is None and volume.get("start_year") is not None:
            series.start_year = volume["start_year"]
        await adopt_provider_ids(db, series, volume)


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
    cached: bool = False,
):
    """Fetch releases from the metadata provider for the given ISO week, upsert into
    DB, and return results.

    Falls back to cached DB data if no provider is configured or it is unreachable.

    ``cached=true`` skips the provider entirely and returns only what the DB
    already holds. The pull list page requests both at once: the cached read
    renders in milliseconds while the live refresh (seconds of provider HTTP)
    replaces it when it lands, so the page never blocks on the provider.
    """
    if week is None:
        week = _current_week_str()

    try:
        monday, sunday = _week_date_range(week)
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="Invalid week format — expected YYYY-WW")

    if settings.metadata_configured and not cached:
        from pullbox.services import sync_status as sync_svc  # noqa: PLC0415

        try:
            auto_job_ids = await _refresh_week(db, provider, monday, sunday)
            await sync_svc.record_sync(
                db, sync_svc.CALENDAR, success=True, message=f"Synced week {week}"
            )
            if auto_job_ids:
                # run_job_now opens its own session, so the rows must be committed
                # before it looks for them — otherwise it finds nothing and the jobs
                # wait for the next queue sweep. get_db's own commit later is a no-op.
                await db.commit()
                for job_id in auto_job_ids:
                    task = asyncio.create_task(run_job_now(job_id))
                    _background_tasks.add(task)
                    task.add_done_callback(_log_task_exception)
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

    _dispatch_link_resolution(settings, releases)

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
