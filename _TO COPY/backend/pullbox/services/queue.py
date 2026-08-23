"""Download queue service: enqueue_issue and process_job."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.clients.nzbget import NZBGetClient
from pullbox.clients.sabnzbd import SABnzbdClient
from pullbox.config import Settings
from pullbox.models import DownloadClient, DownloadJob, Indexer, Issue, Series
from pullbox.search import fan_out_search, score_results

logger = logging.getLogger(__name__)


def _sanitize_name(series_title: str, issue_number: str) -> str:
    """Build a filesystem-safe name for the NZBGet job display label."""
    raw = f"{series_title} {issue_number}"
    return re.sub(r"[^\w\s\-]", "", raw).strip()

ACTIVE_STATUSES = frozenset({"queued", "searching", "pending", "downloading"})


async def enqueue_issue(issue_id: int, db: AsyncSession) -> tuple[DownloadJob, bool]:
    """Enqueue an issue for download.

    Returns (job, created). created=False means an active job already existed;
    the caller should treat this as a conflict (HTTP 409).
    Raises ValueError if the issue does not exist or is in a terminal status (downloaded/skipped).
    Issues with status 'unknown' are automatically promoted to 'wanted'.
    """
    issue = await db.get(Issue, issue_id)
    if issue is None:
        raise ValueError(f"Issue {issue_id} not found")
    if issue.status in ("downloaded", "downloading", "skipped"):
        raise ValueError(f"Issue {issue_id} has status '{issue.status}'; cannot re-enqueue")
    if issue.status == "unknown":
        issue.status = "wanted"

    result = await db.execute(
        select(DownloadJob).where(
            DownloadJob.issue_id == issue_id,
            DownloadJob.status.in_(ACTIVE_STATUSES),
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing, False

    job = DownloadJob(
        issue_id=issue_id,
        source_type="usenet",
        status="queued",
        attempts=0,
        next_attempt_at=datetime.now(tz=timezone.utc),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job, True


async def enqueue_orphaned_wanted(db: AsyncSession) -> list[int]:
    """Create jobs for 'wanted' issues that have no job working on their behalf.

    Marking an issue wanted and enqueuing it are separate steps, and several paths
    set 'wanted' without ever enqueuing: arc sync creates new issues that way, and a
    library rescan flips an issue back to wanted when its file goes missing. Nothing
    else reconciles those — the sweep only walks existing DownloadJob rows — so they
    sit wanted forever. This closes that gap.

    An issue is only re-enqueued when every job it has is 'completed' (or it has
    none). Any 'failed' job means the queue is still deliberately handling it: with a
    next_attempt_at it is mid-backoff, and with a NULL one it exhausted max_retries
    and is meant to stay stopped. Enqueuing in either case would duplicate the job or
    silently defeat the retry cap.

    Returns the ids of newly created jobs.
    """
    blocking = ACTIVE_STATUSES | {"failed"}
    rows = (
        await db.execute(
            select(Issue.id).where(
                Issue.status == "wanted",
                ~select(DownloadJob.id)
                .where(
                    DownloadJob.issue_id == Issue.id,
                    DownloadJob.status.in_(blocking),
                )
                .exists(),
            )
        )
    ).scalars().all()

    created_ids: list[int] = []
    for issue_id in rows:
        try:
            job, created = await enqueue_issue(issue_id, db)
        except ValueError:
            logger.warning(
                "enqueue_orphaned_wanted: could not enqueue issue %d", issue_id, exc_info=True
            )
            continue
        if created:
            created_ids.append(job.id)

    if created_ids:
        await db.flush()
        logger.info(
            "enqueue_orphaned_wanted: enqueued %d wanted issue(s) that had no active job",
            len(created_ids),
        )
    return created_ids


async def process_job(job_id: int, db: AsyncSession, settings: Settings) -> None:
    """Run one processing cycle for a queued or failed job.

    Searches all enabled indexers. On success, sets status to 'pending' with
    top-scored result. On failure, sets exponential backoff next_attempt_at.
    Permanently fails after settings.max_retries attempts.
    """
    job = await db.get(DownloadJob, job_id)
    if job is None:
        logger.warning("process_job: job %d not found", job_id)
        return

    issue = await db.get(Issue, job.issue_id)
    if issue is None:
        logger.warning("process_job: issue %d not found for job %d", job.issue_id, job_id)
        return

    series = await db.get(Series, issue.series_id)
    if series is None:
        logger.warning("process_job: series not found for issue %d", issue.id)
        return

    job.status = "searching"
    job.attempts += 1
    await db.flush()

    indexer_result = await db.execute(select(Indexer).where(Indexer.enabled == True))  # noqa: E712
    indexers = list(indexer_result.scalars().all())

    results = await fan_out_search(issue, series, indexers)

    now = datetime.now(tz=timezone.utc)
    job.last_attempt_at = now

    if not results:
        job.status = "failed"
        if job.attempts >= settings.max_retries:
            job.next_attempt_at = None
        else:
            days = min(2 ** (job.attempts - 1), 7)
            job.next_attempt_at = now + timedelta(days=days)
    else:
        scored = score_results(results, series.title, issue.issue_number)
        top = scored[0]
        job.status = "pending"
        job.result_guid = top.guid
        job.result_title = top.title
        job.indexer_id = top.indexer_id
        job.source_type = top.source_type

        dc_result = await db.execute(
            select(DownloadClient)
            .where(DownloadClient.enabled == True)  # noqa: E712
            .order_by(DownloadClient.id)
            .limit(1)
        )
        dc = dc_result.scalar_one_or_none()

        if dc:
            if dc.type == "nzbget":
                dl_client = NZBGetClient(
                    host=dc.host,
                    port=dc.port,
                    username=dc.username or "nzbget",
                    password=dc.password or "",
                )
            elif dc.type == "sabnzbd":
                dl_client = SABnzbdClient(
                    host=dc.host,
                    port=dc.port,
                    api_key=dc.api_key or "",
                )
            else:
                logger.warning(
                    "process_job: unsupported download client type %r; job %d left at 'pending'",
                    dc.type,
                    job_id,
                )
                dl_client = None

            if dl_client is not None:
                try:
                    sanitized = _sanitize_name(series.title, issue.issue_number)
                    client_job_id = await dl_client.send_nzb(top.download_url, sanitized, dc.category)
                    job.client_job_id = client_job_id
                    job.download_client_type = dc.type
                    job.status = "downloading"
                    issue.status = "downloading"
                except Exception:
                    logger.warning(
                        "process_job: %s dispatch failed for job %d", dc.type, job_id, exc_info=True
                    )
        else:
            logger.warning(
                "process_job: no download client configured; job %d left at 'pending'", job_id
            )

    await db.flush()


async def run_job_now(job_id: int) -> None:
    """Open a fresh session and process job immediately; safe to fire-and-forget."""
    import pullbox.database as db_module
    import pullbox.deps as deps_module

    logger.info("run_job_now: starting for job %d", job_id)
    if db_module.AsyncSessionLocal is None:
        logger.warning("run_job_now: database not initialized, aborting")
        return
    settings = deps_module.get_settings()
    async with db_module.AsyncSessionLocal() as db:
        try:
            await process_job(job_id, db, settings)
            await db.commit()
            logger.info("run_job_now: finished job %d", job_id)
        except Exception:
            logger.exception("run_job_now: error processing job %d", job_id)
            await db.rollback()
