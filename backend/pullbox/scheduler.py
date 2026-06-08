"""APScheduler 4.x setup and scheduled job implementations."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler import AsyncScheduler, ConflictPolicy
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pullbox.config import Settings

logger = logging.getLogger(__name__)


def build_scheduler(engine) -> AsyncScheduler:
    """Create an AsyncScheduler backed by the app's SQLAlchemy engine."""
    data_store = SQLAlchemyDataStore(engine)
    return AsyncScheduler(data_store=data_store)


async def register_schedules(scheduler: AsyncScheduler, settings: Settings) -> None:
    """Add recurring job schedules. ConflictPolicy.do_nothing prevents duplicates on restart."""
    retry_hour, retry_minute = _parse_time(settings.retry_time)

    await scheduler.add_schedule(
        daily_queue_sweep,
        CronTrigger(hour=retry_hour, minute=retry_minute),
        id="daily_queue_sweep",
        conflict_policy=ConflictPolicy.do_nothing,
    )
    await scheduler.add_schedule(
        poll_download_clients,
        IntervalTrigger(minutes=5),
        id="poll_download_clients",
        conflict_policy=ConflictPolicy.do_nothing,
    )
    logger.info("Scheduler schedules registered: daily_queue_sweep, poll_download_clients")


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string to (hour, minute) ints."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


async def daily_queue_sweep() -> None:
    """Process all queued/failed jobs whose next_attempt_at is now due.

    Called by APScheduler at the time configured in settings.retry_time.
    Opens its own database session — does not use FastAPI dependency injection.
    """
    from sqlalchemy import select

    import pullbox.database as db_module
    import pullbox.deps as deps_module
    from pullbox.models import DownloadJob  # noqa: PLC0415
    from pullbox.services.queue import process_job  # noqa: PLC0415

    if db_module.AsyncSessionLocal is None:
        logger.warning("daily_queue_sweep: database not initialized, skipping run")
        return

    settings = deps_module.get_settings()
    now = datetime.now(tz=timezone.utc)

    async with db_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.status.in_(["queued", "failed"]),
                DownloadJob.next_attempt_at <= now,
            )
        )
        job_ids = [j.id for j in result.scalars().all()]

    processed = 0
    failures = 0
    for job_id in job_ids:
        async with db_module.AsyncSessionLocal() as db:
            try:
                await process_job(job_id, db, settings)
                await db.commit()
                processed += 1
            except Exception:
                logger.exception("daily_queue_sweep: error processing job %d", job_id)
                await db.rollback()
                failures += 1

    logger.info("daily_queue_sweep complete: processed=%d failures=%d", processed, failures)


def _parse_date_str(value: object) -> date | None:
    """Parse a date string like '2025-05-01' or '2025-05-01T00:00:00'. Returns None on failure."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


async def nightly_calendar_refresh() -> dict:
    """Refresh weekly release calendar from ComicVine.

    Fetches releases for the current ISO week plus pull_list_lookahead_weeks weeks ahead.
    For each release: find-or-create Series and Issue rows, then upsert WeeklyRelease.
    Never overwrites Issue.status on existing issues.
    """
    from sqlalchemy import select

    import pullbox.database as db_module
    import pullbox.deps as deps_module
    from pullbox.clients.comicvine import ComicVineClient  # noqa: PLC0415
    from pullbox.models import Issue, Series, WeeklyRelease  # noqa: PLC0415

    logger.info("nightly_calendar_refresh: starting")

    if db_module.AsyncSessionLocal is None:
        logger.warning("nightly_calendar_refresh: database not initialized, skipping run")
        return {}

    settings = deps_module.get_settings()

    if not settings.comicvine_api_key:
        logger.warning("nightly_calendar_refresh: no ComicVine API key configured, skipping run")
        return {}

    today = date.today()
    weeks: list[tuple[date, date]] = []
    for i in range(settings.pull_list_lookahead_weeks + 1):
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=i)
        weeks.append((monday, monday + timedelta(days=6)))

    new_series_count = 0
    new_issues_count = 0
    releases_upserted = 0

    client = ComicVineClient(api_key=settings.comicvine_api_key)
    try:
        for week_monday, week_sunday in weeks:
            start_str = week_monday.isoformat()
            end_str = week_sunday.isoformat()

            try:
                releases = await client.get_weekly_releases(start_str, end_str)
            except Exception:
                logger.warning(
                    "nightly_calendar_refresh: ComicVine fetch failed for %s–%s",
                    start_str,
                    end_str,
                    exc_info=True,
                )
                continue

            for release_data in releases:
                async with db_module.AsyncSessionLocal() as db:
                    try:
                        from pullbox.services.queue import enqueue_issue, run_job_now  # noqa: PLC0415

                        series_cv_id = str(release_data["series"]["comicvine_id"])

                        # Find or create Series
                        result = await db.execute(
                            select(Series).where(Series.comicvine_id == series_cv_id)
                        )
                        series = result.scalar_one_or_none()
                        if series is None:
                            series = Series(
                                comicvine_id=series_cv_id,
                                title=release_data["series"].get("title", "Unknown Series"),
                            )
                            db.add(series)
                            await db.flush()
                            new_series_count += 1

                        issue_cv_id = str(release_data["comicvine_id"])

                        # Find or create Issue (never overwrite status)
                        result = await db.execute(
                            select(Issue).where(Issue.comicvine_id == issue_cv_id)
                        )
                        issue = result.scalar_one_or_none()
                        new_issue = False
                        if issue is None:
                            store_date = _parse_date_str(release_data.get("store_date"))
                            issue = Issue(
                                series_id=series.id,
                                comicvine_id=issue_cv_id,
                                issue_number=str(release_data.get("issue_number", "")),
                                title=release_data.get("title"),
                                store_date=store_date,
                                cover_url=release_data.get("cover_url"),
                                status="unknown",
                            )
                            db.add(issue)
                            await db.flush()
                            new_issues_count += 1
                            new_issue = True

                        auto_job_id: int | None = None
                        if new_issue and series.auto_download:
                            try:
                                job, created = await enqueue_issue(issue.id, db)
                                if created:
                                    auto_job_id = job.id
                            except ValueError:
                                pass

                        # Determine release date: prefer issue's store_date, fall back to week start
                        release_date = issue.store_date or week_monday

                        # Upsert WeeklyRelease
                        result = await db.execute(
                            select(WeeklyRelease).where(
                                WeeklyRelease.issue_id == issue.id,
                                WeeklyRelease.release_date == release_date,
                            )
                        )
                        wr = result.scalar_one_or_none()
                        if wr is None:
                            db.add(
                                WeeklyRelease(
                                    issue_id=issue.id,
                                    release_date=release_date,
                                    source="comicvine",
                                )
                            )
                            releases_upserted += 1

                        await db.commit()
                        if auto_job_id is not None:
                            asyncio.create_task(run_job_now(auto_job_id))
                    except Exception:
                        logger.warning(
                            "nightly_calendar_refresh: error processing release %s",
                            release_data.get("comicvine_id"),
                            exc_info=True,
                        )
                        await db.rollback()
    finally:
        await client.close()

    result = {"new_series": new_series_count, "new_issues": new_issues_count, "releases_upserted": releases_upserted}
    logger.info(
        "nightly_calendar_refresh complete: new_series=%d new_issues=%d releases_upserted=%d",
        new_series_count,
        new_issues_count,
        releases_upserted,
    )
    return result


async def poll_download_clients() -> None:
    """Check completion status of all active downloads. Runs every 5 minutes.

    Queries DownloadJob rows with status='downloading', calls the appropriate
    download client to check progress, and updates job/issue status on completion
    or failure.
    """
    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.clients.nzbget import NZBGetClient  # noqa: PLC0415
    from pullbox.clients.sabnzbd import SABnzbdClient  # noqa: PLC0415
    from pullbox.models import DownloadClient, DownloadJob, Issue  # noqa: PLC0415

    if db_module.AsyncSessionLocal is None:
        logger.warning("poll_download_clients: database not initialized, skipping run")
        return

    # Load active downloading jobs and enabled download clients in one pass
    async with db_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.status == "downloading",
                DownloadJob.client_job_id.isnot(None),
            )
        )
        jobs_snapshot = [
            (j.id, j.client_job_id, j.download_client_type, j.attempts)
            for j in result.scalars().all()
        ]

        # Index first enabled client per type for credential lookup
        dc_result = await db.execute(
            select(DownloadClient).where(DownloadClient.enabled == True)  # noqa: E712
        )
        clients_by_type: dict[str, DownloadClient] = {}
        for dc in dc_result.scalars().all():
            if dc.type not in clients_by_type:
                clients_by_type[dc.type] = dc

    if not jobs_snapshot:
        return

    for job_id, client_job_id, client_type, _attempts in jobs_snapshot:
        if client_type not in clients_by_type:
            continue

        dc = clients_by_type[client_type]

        if client_type == "nzbget":
            client = NZBGetClient(
                host=dc.host,
                port=dc.port,
                username=dc.username or "nzbget",
                password=dc.password or "",
            )
        elif client_type == "sabnzbd":
            client = SABnzbdClient(
                host=dc.host,
                port=dc.port,
                api_key=dc.api_key or "",
            )
        else:
            continue

        try:
            status = await client.get_job_status(client_job_id)
        except Exception:
            logger.warning(
                "poll_download_clients: error polling job %d", job_id, exc_info=True
            )
            continue

        if status not in ("completed", "failed"):
            continue  # still downloading — nothing to update

        async with db_module.AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            if job_obj is None:
                continue
            issue = await db.get(Issue, job_obj.issue_id)
            now = datetime.now(tz=timezone.utc)

            if status == "completed":
                job_obj.status = "completed"
                issue.status = "downloaded"
                issue.updated_at = now
                logger.info("poll_download_clients: job %d completed", job_id)
            else:
                job_obj.status = "failed"
                issue.status = "wanted"
                days = min(2 ** (job_obj.attempts - 1), 7)
                job_obj.next_attempt_at = now + timedelta(days=days)
                logger.info("poll_download_clients: job %d failed, retry in %d days", job_id, days)

            await db.commit()
