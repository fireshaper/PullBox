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
from pullbox.services import webhooks
from pullbox.services.general import resolve_library_path

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


async def enqueue_unqueued_issues(db: AsyncSession) -> list[int]:
    """Create jobs for issues that should be downloading but have nothing working on them.

    Two populations qualify:

    * ``wanted`` issues with no job. Marking an issue wanted and enqueuing it are
      separate steps, and several paths set 'wanted' without ever enqueuing: arc
      sync creates new issues that way, and a library rescan flips an issue back to
      wanted when its file goes missing.
    * ``unknown`` issues on an ``auto_download`` series. Series.auto_download means
      "enqueue each newly-created row", but that only ever fires at creation time —
      an issue created before the flag was set, or during a window where the
      creating path forgot to enqueue, is otherwise stranded forever.

    Nothing else reconciles either group: the queue sweep only walks existing
    DownloadJob rows, so an issue with no job is invisible to it. ``enqueue_issue``
    promotes 'unknown' to 'wanted', so a queued issue also stops rendering as
    "Not queued" in the calendar.

    An issue is only enqueued when every job it has is 'completed' (or it has none).
    Any 'failed' job means the queue is still deliberately handling it: with a
    next_attempt_at it is mid-backoff, and with a NULL one it exhausted max_retries
    and is meant to stay stopped. Enqueuing in either case would duplicate the job or
    silently defeat the retry cap.

    Returns the ids of newly created jobs.
    """
    blocking = ACTIVE_STATUSES | {"failed"}
    has_live_job = (
        select(DownloadJob.id)
        .where(
            DownloadJob.issue_id == Issue.id,
            DownloadJob.status.in_(blocking),
        )
        .exists()
    )

    wanted_rows = (
        await db.execute(select(Issue.id).where(Issue.status == "wanted", ~has_live_job))
    ).scalars().all()

    auto_rows = (
        await db.execute(
            select(Issue.id)
            .join(Series, Series.id == Issue.series_id)
            .where(
                Issue.status == "unknown",
                Series.auto_download == True,  # noqa: E712
                ~has_live_job,
            )
        )
    ).scalars().all()

    # dict.fromkeys keeps insertion order while dropping any overlap between the two.
    issue_ids = list(dict.fromkeys([*wanted_rows, *auto_rows]))

    created_ids: list[int] = []
    for issue_id in issue_ids:
        try:
            job, created = await enqueue_issue(issue_id, db)
        except ValueError:
            logger.warning(
                "enqueue_unqueued_issues: could not enqueue issue %d", issue_id, exc_info=True
            )
            continue
        if created:
            created_ids.append(job.id)

    if created_ids:
        await db.flush()
        logger.info(
            "enqueue_unqueued_issues: enqueued %d issue(s) with no active job "
            "(%d wanted, %d unknown on auto_download series)",
            len(created_ids),
            len(wanted_rows),
            len(auto_rows),
        )
    return created_ids


async def process_job(job_id: int, db: AsyncSession, settings: Settings) -> None:
    """Run one processing cycle for a queued or failed job.

    Searches all enabled indexers. On success, sets status to 'pending' with
    top-scored result. On failure, sets exponential backoff next_attempt_at.
    Permanently fails after settings.max_retries attempts.

    Commits at each step rather than leaving one transaction open for the whole
    cycle. SQLite allows a single writer, and a flush takes that write lock until
    the transaction ends — so the previous "flush then search" shape held the lock
    across every indexer HTTP call. A sweep of a dozen jobs kept it for far longer
    than busy_timeout (5s), and the APScheduler datastore, which shares this
    database, got "database is locked" on its own bookkeeping write and crashed the
    whole scheduler. Every network call here must happen with no write pending.
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

    # Read everything the network steps need up front, while this is still a
    # read-only transaction holding no write lock.
    indexers = list(
        (await db.execute(select(Indexer).where(Indexer.enabled == True))).scalars().all()  # noqa: E712
    )
    dc = (
        await db.execute(
            select(DownloadClient)
            .where(DownloadClient.enabled == True)  # noqa: E712
            .order_by(DownloadClient.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    # Snapshot the client's fields: they are read again after a commit, and
    # detaching them here keeps that independent of session expiry settings.
    dc_type = dc.type if dc else None
    dc_category = dc.category if dc else None
    dl_client = _build_download_client(dc, job_id) if dc else None

    series_title = series.title
    issue_number = issue.issue_number
    # For the webhook payload's path_rel; read here while still read-only.
    library_root = await resolve_library_path(db, settings.library_path)

    # Claim the job in its own short transaction, then release the write lock.
    job.status = "searching"
    job.attempts += 1
    await db.commit()

    async def _fail(reason: str) -> None:
        """Record a failed attempt with backoff; give up at max_retries.

        Commits, then (only when the cap was hit) notifies — a routine
        no-results miss is not worth a webhook, since most issues simply have
        not reached the indexers yet.
        """
        now = datetime.now(tz=timezone.utc)
        job.last_attempt_at = now
        job.status = "failed"
        exhausted = job.attempts >= settings.max_retries
        if exhausted:
            job.next_attempt_at = None
        else:
            days = min(2 ** (job.attempts - 1), 7)
            job.next_attempt_at = now + timedelta(days=days)
        payload = (
            webhooks.build_issue_payload(
                issue,
                series,
                library_root,
                job=job,
                reason=f"{reason} (attempt {job.attempts} of {settings.max_retries})",
            )
            if exhausted
            else None
        )
        await db.commit()
        if payload is not None:
            webhooks.emit("download.exhausted", payload)

    try:
        results = await fan_out_search(issue, series, indexers)
    except Exception:
        # The claim above is already committed, so letting this propagate would
        # strand the job in 'searching' — a status neither the sweep nor the
        # reconciler ever revisits. Fail it so the normal backoff retries it.
        logger.warning("process_job: search failed for job %d", job_id, exc_info=True)
        await _fail("Indexer search failed")
        return

    now = datetime.now(tz=timezone.utc)

    if not results:
        await _fail("No results found on any indexer")
        return

    job.last_attempt_at = now
    scored = score_results(results, series_title, issue_number)
    top = scored[0]
    job.status = "pending"
    job.result_guid = top.guid
    job.result_title = top.title
    job.indexer_id = top.indexer_id
    job.source_type = top.source_type

    if dl_client is None:
        if dc is None:
            logger.warning(
                "process_job: no download client configured; job %d left at 'pending'", job_id
            )
        await db.commit()
        return

    # Persist 'pending' and drop the write lock before the dispatch HTTP call.
    await db.commit()

    try:
        sanitized = _sanitize_name(series_title, issue_number)
        client_job_id = await dl_client.send_nzb(top.download_url, sanitized, dc_category)
    except Exception:
        logger.warning(
            "process_job: %s dispatch failed for job %d", dc_type, job_id, exc_info=True
        )
        return

    job.client_job_id = client_job_id
    job.download_client_type = dc_type
    job.status = "downloading"
    issue.status = "downloading"
    payload = webhooks.build_issue_payload(issue, series, library_root, job=job)
    await db.commit()
    webhooks.emit("download.started", payload)


def _build_download_client(dc, job_id: int):
    """Construct the client for a DownloadClient row, or None if unsupported."""
    if dc.type == "nzbget":
        return NZBGetClient(
            host=dc.host,
            port=dc.port,
            username=dc.username or "nzbget",
            password=dc.password or "",
        )
    if dc.type == "sabnzbd":
        return SABnzbdClient(host=dc.host, port=dc.port, api_key=dc.api_key or "")
    logger.warning(
        "process_job: unsupported download client type %r; job %d left at 'pending'",
        dc.type,
        job_id,
    )
    return None


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
