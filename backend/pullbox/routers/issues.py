"""Issue-level endpoints: mark as wanted or skipped."""

import asyncio

from fastapi import APIRouter, HTTPException

from pullbox.deps import DbDep
from pullbox.models import Issue
from pullbox.schemas import IssueResponse
from pullbox.services.queue import enqueue_issue, run_job_now

router = APIRouter(prefix="/api/issues", tags=["issues"])


async def _get_issue_or_404(issue_id: int, db) -> Issue:
    row = await db.get(Issue, issue_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return row


# ── Step 4.8 — Want / Skip ───────────────────────────────────────────────────


@router.post("/{issue_id}/want", response_model=IssueResponse)
async def mark_wanted(issue_id: int, db: DbDep):
    issue = await _get_issue_or_404(issue_id, db)
    if issue.status == "downloaded":
        raise HTTPException(status_code=409, detail="Issue is already downloaded")
    issue.status = "wanted"
    await db.flush()
    try:
        job, created = await enqueue_issue(issue_id, db)
        if created:
            await db.commit()
            asyncio.create_task(run_job_now(job.id))
    except ValueError:
        pass
    await db.refresh(issue)
    return IssueResponse.model_validate(issue)


@router.post("/{issue_id}/skip", response_model=IssueResponse)
async def mark_skipped(issue_id: int, db: DbDep):
    issue = await _get_issue_or_404(issue_id, db)
    issue.status = "skipped"
    await db.flush()
    await db.refresh(issue)
    return IssueResponse.model_validate(issue)
