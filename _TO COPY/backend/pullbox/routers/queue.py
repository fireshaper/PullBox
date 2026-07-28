"""Download queue API: list, enqueue, retry, delete."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep
from pullbox.models import DownloadJob, Issue
from pullbox.schemas import (
    DownloadJobResponse,
    IssueSummary,
    RetryFailedResponse,
    SeriesSummary,
)
from pullbox.services.queue import enqueue_issue, run_job_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"])


async def _build_response(job: DownloadJob) -> DownloadJobResponse:
    issue_summary = None
    series_summary = None
    if job.issue is not None:
        issue_summary = IssueSummary.model_validate(job.issue)
        if job.issue.series is not None:
            series_summary = SeriesSummary.model_validate(job.issue.series)
    return DownloadJobResponse(
        id=job.id,
        issue_id=job.issue_id,
        source_type=job.source_type,
        indexer_id=job.indexer_id,
        result_guid=job.result_guid,
        result_title=job.result_title,
        download_client_type=job.download_client_type,
        client_job_id=job.client_job_id,
        status=job.status,
        attempts=job.attempts,
        last_attempt_at=job.last_attempt_at,
        next_attempt_at=job.next_attempt_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        issue=issue_summary,
        series=series_summary,
    )


async def _load_job_with_relations(job_id: int, db) -> DownloadJob | None:
    result = await db.execute(
        select(DownloadJob)
        .where(DownloadJob.id == job_id)
        .options(selectinload(DownloadJob.issue).selectinload(Issue.series))
    )
    return result.scalar_one_or_none()


@router.get("/", response_model=list[DownloadJobResponse])
async def list_queue(db: DbDep, status: str | None = None):
    q = (
        select(DownloadJob)
        .where(DownloadJob.status != "completed")
        .options(selectinload(DownloadJob.issue).selectinload(Issue.series))
        .order_by(DownloadJob.next_attempt_at.asc().nullslast())
    )
    if status is not None:
        q = select(DownloadJob).where(DownloadJob.status == status).options(
            selectinload(DownloadJob.issue).selectinload(Issue.series)
        ).order_by(DownloadJob.next_attempt_at.asc().nullslast())
    result = await db.execute(q)
    jobs = result.scalars().all()
    return [await _build_response(j) for j in jobs]


@router.post("/enqueue/{issue_id}", status_code=201, response_model=DownloadJobResponse)
async def enqueue(issue_id: int, db: DbDep):
    try:
        job, created = await enqueue_issue(issue_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not created:
        raise HTTPException(
            status_code=409, detail="An active download job already exists for this issue"
        )
    loaded = await _load_job_with_relations(job.id, db)
    await db.commit()
    asyncio.create_task(run_job_now(job.id))
    return await _build_response(loaded)


@router.post("/retry-failed", response_model=RetryFailedResponse)
async def retry_failed(db: DbDep):
    """Re-queue every failed job at once (dashboard 'Retry All Failed' shortcut)."""
    jobs = (
        await db.execute(select(DownloadJob).where(DownloadJob.status == "failed"))
    ).scalars().all()
    now = datetime.now(tz=timezone.utc)
    job_ids: list[int] = []
    for job in jobs:
        job.status = "queued"
        job.next_attempt_at = now
        job_ids.append(job.id)
    await db.flush()
    await db.commit()
    for job_id in job_ids:
        asyncio.create_task(run_job_now(job_id))
    return RetryFailedResponse(retried=len(job_ids))


@router.post("/retry/{job_id}", response_model=DownloadJobResponse)
async def retry_job(job_id: int, db: DbDep):
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("searching", "downloading", "completed"):
        raise HTTPException(status_code=400, detail=f"Cannot retry job with status '{job.status}'")
    job.status = "queued"
    job.next_attempt_at = datetime.now(tz=timezone.utc)
    await db.flush()
    loaded = await _load_job_with_relations(job_id, db)
    response = await _build_response(loaded)
    await db.commit()
    asyncio.create_task(run_job_now(job_id))
    return response


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: int, db: DbDep):
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.delete(job)
    await db.flush()
    return Response(status_code=204)
