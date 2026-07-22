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
    # Remove the superseded 2-min series-group backlog job if it lingers in the
    # persisted datastore (do_nothing won't update an existing schedule's trigger).
    try:
        await scheduler.remove_schedule("sync_import_backlog")
    except Exception:  # noqa: BLE001 — not present is fine
        pass
    await scheduler.add_schedule(
        sync_imported_issues,
        IntervalTrigger(minutes=settings.import_sync_interval_minutes),
        id="sync_imported_issues",
        conflict_policy=ConflictPolicy.do_nothing,
    )
    logger.info(
        "Scheduler schedules registered: daily_queue_sweep, poll_download_clients, "
        "sync_imported_issues"
    )


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
    """Refresh weekly release calendar from the metadata provider.

    Fetches releases for the current ISO week plus pull_list_lookahead_weeks weeks ahead.
    For each release: find-or-create Series and Issue rows, then upsert WeeklyRelease.
    Never overwrites Issue.status on existing issues.
    """
    from sqlalchemy import or_, select

    import pullbox.database as db_module
    import pullbox.deps as deps_module
    from pullbox.clients.metadata import PROVIDER_ERRORS, ids_for  # noqa: PLC0415
    from pullbox.models import Issue, Series, WeeklyRelease  # noqa: PLC0415

    logger.info("nightly_calendar_refresh: starting")

    if db_module.AsyncSessionLocal is None:
        logger.warning("nightly_calendar_refresh: database not initialized, skipping run")
        return {}

    settings = deps_module.get_settings()

    if not settings.metadata_configured:
        logger.warning("nightly_calendar_refresh: no metadata source configured, skipping run")
        return {}

    today = date.today()
    weeks: list[tuple[date, date]] = []
    for i in range(settings.pull_list_lookahead_weeks + 1):
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=i)
        weeks.append((monday, monday + timedelta(days=6)))

    new_series_count = 0
    new_issues_count = 0
    releases_upserted = 0
    # Series (by local id) already attempted for publisher enrichment this run, so we
    # do at most one lookup per series across all weeks.
    enriched_attempted: set[int] = set()

    client = deps_module.build_metadata_provider(settings)
    try:
        for week_monday, week_sunday in weeks:
            start_str = week_monday.isoformat()
            end_str = week_sunday.isoformat()

            try:
                releases = await client.get_weekly_releases(start_str, end_str)
            except Exception:
                logger.warning(
                    "nightly_calendar_refresh: provider fetch failed for %s–%s",
                    start_str,
                    end_str,
                    exc_info=True,
                )
                continue

            for release_data in releases:
                async with db_module.AsyncSessionLocal() as db:
                    try:
                        from pullbox.services.queue import (  # noqa: PLC0415
                            enqueue_issue,
                            run_job_now,
                        )

                        series_ids = ids_for(release_data["series"])

                        # Find or create Series (match on either metadata id)
                        series_clauses = []
                        if series_ids.get("metron_id"):
                            series_clauses.append(Series.metron_id == series_ids["metron_id"])
                        if series_ids.get("comicvine_id"):
                            series_clauses.append(
                                Series.comicvine_id == series_ids["comicvine_id"]
                            )
                        series = (
                            (await db.execute(select(Series).where(or_(*series_clauses))))
                            .scalar_one_or_none()
                            if series_clauses
                            else None
                        )
                        if series is None:
                            series = Series(
                                metron_id=series_ids.get("metron_id"),
                                comicvine_id=series_ids.get("comicvine_id"),
                                title=release_data["series"].get("title", "Unknown Series"),
                            )
                            db.add(series)
                            await db.flush()
                            new_series_count += 1

                        # The weekly-issues endpoint carries no publisher, so fill it
                        # from a per-series lookup — once per series per run. Failures
                        # are non-fatal; the page-load path retries later.
                        if series.publisher is None and series.id not in enriched_attempted:
                            enriched_attempted.add(series.id)
                            try:
                                volume = await client.get_volume(**ids_for(series))
                            except PROVIDER_ERRORS:
                                logger.debug(
                                    "nightly_calendar_refresh: publisher enrich "
                                    "failed for series %s",
                                    series.id,
                                    exc_info=True,
                                )
                            else:
                                series.publisher = volume.get("publisher")
                                if (
                                    series.start_year is None
                                    and volume.get("start_year") is not None
                                ):
                                    series.start_year = volume["start_year"]

                        issue_ids = ids_for(release_data)

                        # Find or create Issue (never overwrite status)
                        issue_clauses = []
                        if issue_ids.get("metron_id"):
                            issue_clauses.append(Issue.metron_id == issue_ids["metron_id"])
                        if issue_ids.get("comicvine_id"):
                            issue_clauses.append(Issue.comicvine_id == issue_ids["comicvine_id"])
                        issue = (
                            (await db.execute(select(Issue).where(or_(*issue_clauses))))
                            .scalar_one_or_none()
                            if issue_clauses
                            else None
                        )
                        new_issue = False
                        if issue is None:
                            store_date = _parse_date_str(release_data.get("store_date"))
                            issue = Issue(
                                series_id=series.id,
                                metron_id=issue_ids.get("metron_id"),
                                comicvine_id=issue_ids.get("comicvine_id"),
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
                        source = "metron" if issue_ids.get("metron_id") else "comicvine"

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
                                    source=source,
                                )
                            )
                            releases_upserted += 1

                        await db.commit()
                        if auto_job_id is not None:
                            asyncio.create_task(run_job_now(auto_job_id))
                    except Exception:
                        logger.warning(
                            "nightly_calendar_refresh: error processing release %s",
                            release_data.get("metron_id") or release_data.get("comicvine_id"),
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


async def _run_post_processing(db, issue, completed_path, pp_cfg, library_root, job_id) -> None:
    """Move/rename a completed download's comic file and record its final path.

    Non-fatal: any failure is logged and the issue stays 'downloaded' with
    file_path pointing at the client's completed directory (if known), so the
    polling loop is never interrupted.
    """
    if pp_cfg is None or not pp_cfg.enabled:
        return

    if not completed_path:
        logger.warning(
            "poll_download_clients: no completed path for job %d, skipping post-processing",
            job_id,
        )
        return

    from types import SimpleNamespace  # noqa: PLC0415

    from pullbox.models import Series  # noqa: PLC0415
    from pullbox.services.postprocess import apply_post_processing  # noqa: PLC0415

    try:
        series = await db.get(Series, issue.series_id)
        # Detach the values needed for rendering so the file work can run in a
        # worker thread without touching the async session.
        issue_data = SimpleNamespace(
            id=issue.id, issue_number=issue.issue_number, title=issue.title
        )
        series_data = SimpleNamespace(
            title=series.title, publisher=series.publisher, start_year=series.start_year
        )
        final_path = await asyncio.to_thread(
            apply_post_processing,
            issue_data,
            series_data,
            completed_path,
            pp_cfg,
            library_root,
        )
        issue.file_path = final_path or completed_path
    except Exception:
        logger.warning(
            "poll_download_clients: post-processing failed for job %d", job_id, exc_info=True
        )
        if not issue.file_path:
            issue.file_path = completed_path


async def poll_download_clients() -> None:
    """Check completion status of all active downloads. Runs every 5 minutes.

    Queries DownloadJob rows with status='downloading', calls the appropriate
    download client to check progress, and updates job/issue status on completion
    or failure.
    """
    from types import SimpleNamespace

    from sqlalchemy import select

    import pullbox.database as db_module
    import pullbox.deps as deps_module
    from pullbox.clients.nzbget import NZBGetClient  # noqa: PLC0415
    from pullbox.clients.sabnzbd import SABnzbdClient  # noqa: PLC0415
    from pullbox.models import (  # noqa: PLC0415
        DownloadClient,
        DownloadJob,
        Issue,
        PostProcessingSettings,
    )

    if db_module.AsyncSessionLocal is None:
        logger.warning("poll_download_clients: database not initialized, skipping run")
        return

    # Load active downloading jobs, enabled clients, and post-processing config in one pass
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

        # Snapshot post-processing config into a detached holder (session closes below).
        pp_row = (
            await db.execute(select(PostProcessingSettings).limit(1))
        ).scalar_one_or_none()
        pp_cfg = (
            SimpleNamespace(
                enabled=pp_row.enabled,
                operation=pp_row.operation,
                destination_root=pp_row.destination_root,
                folder_pattern=pp_row.folder_pattern,
                file_pattern=pp_row.file_pattern,
            )
            if pp_row is not None
            else None
        )

    if not jobs_snapshot:
        return

    library_root = deps_module.get_settings().library_path

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

        # On completion, fetch the client's output path (network call) outside any
        # DB session so we don't hold a connection across it.
        completed_path: str | None = None
        if status == "completed" and pp_cfg is not None and pp_cfg.enabled:
            try:
                completed_path = await client.get_completed_path(client_job_id)
            except Exception:
                logger.warning(
                    "poll_download_clients: get_completed_path failed for job %d",
                    job_id,
                    exc_info=True,
                )

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
                await _run_post_processing(
                    db, issue, completed_path, pp_cfg, library_root, job_id
                )
                logger.info(
                    "Issue id=%d #%s status → downloaded (job %d complete)",
                    issue.id,
                    issue.issue_number,
                    job_id,
                )
            else:
                job_obj.status = "failed"
                issue.status = "wanted"
                days = min(2 ** (job_obj.attempts - 1), 7)
                job_obj.next_attempt_at = now + timedelta(days=days)
                logger.info(
                    "Issue id=%d #%s status downloading → wanted (job %d failed, retry in %d days)",
                    issue.id,
                    issue.issue_number,
                    job_id,
                    days,
                )

            await db.commit()


async def sync_imported_issues() -> None:
    """Backfill metadata for imported issues, in throttled batches.

    Runs on an interval. Each run samples up to ``import_sync_batch_size`` pending
    tracking rows (oldest first), then fully syncs every series those rows belong
    to — one series fetch each — so large series aren't re-fetched every run. Stops
    early if the provider throttles us; the shared client rate limiters keep us under
    each cap and the job resumes on the next interval.
    """
    from sqlalchemy import select

    import pullbox.database as db_module
    import pullbox.deps as deps_module
    from pullbox.clients.metadata import RATE_LIMIT_ERRORS  # noqa: PLC0415
    from pullbox.models import ImportFile, Series  # noqa: PLC0415
    from pullbox.services import sync_status as sync_svc  # noqa: PLC0415
    from pullbox.services.import_sync import resolve_series_for_import  # noqa: PLC0415

    async def _record(success: bool, message: str) -> None:
        try:
            async with db_module.AsyncSessionLocal() as db:
                await sync_svc.record_sync(
                    db, sync_svc.IMPORT_BACKFILL, success=success, message=message
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — status recording must never break the job
            logger.warning("sync_imported_issues: failed to record sync status", exc_info=True)

    if db_module.AsyncSessionLocal is None:
        logger.warning("sync_imported_issues: database not initialized, skipping run")
        return

    settings = deps_module.get_settings()
    if not settings.metadata_configured:
        return  # without a source the issues stay pending until one is configured

    # Sample a batch of pending tracking rows (oldest first) and take their series.
    async with db_module.AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ImportFile.series_id)
                .where(ImportFile.status == "pending")
                .order_by(ImportFile.id)
                .limit(settings.import_sync_batch_size)
            )
        ).scalars().all()
    series_ids: list[int] = list(dict.fromkeys(rows))  # distinct, preserve order

    if not series_ids:
        return

    client = deps_module.build_metadata_provider(settings)
    total_synced = 0
    try:
        for series_id in series_ids:
            async with db_module.AsyncSessionLocal() as db:
                try:
                    series = (
                        await db.execute(select(Series).where(Series.id == series_id))
                    ).scalar_one_or_none()
                    if series is None:
                        continue
                    tracking_rows = (
                        await db.execute(
                            select(ImportFile).where(
                                ImportFile.series_id == series_id,
                                ImportFile.status == "pending",
                            )
                        )
                    ).scalars().all()
                    synced, _unmatched, _no_match = await resolve_series_for_import(
                        db, client, series, tracking_rows
                    )
                    await db.commit()
                    total_synced += synced
                except RATE_LIMIT_ERRORS as exc:
                    await db.rollback()
                    logger.warning(
                        "sync_imported_issues: rate limited, pausing until next run (%s)", exc
                    )
                    await _record(False, f"Rate limited by metadata provider ({exc})")
                    return
                except Exception:
                    await db.rollback()
                    logger.warning(
                        "sync_imported_issues: failed to backfill series id=%s",
                        series_id,
                        exc_info=True,
                    )
    finally:
        await client.close()

    logger.info("sync_imported_issues run complete: %d issue(s) synced", total_synced)
    await _record(True, f"{total_synced} issue(s) synced")
