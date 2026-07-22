"""Issue-level endpoints: mark as wanted or skipped."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from pullbox.deps import DbDep, MetadataProviderDep
from pullbox.models import Issue
from pullbox.schemas import IssueResponse, StoryArcDetail
from pullbox.services.arcs import get_issue_arc_detail
from pullbox.services.queue import enqueue_issue, run_job_now

router = APIRouter(prefix="/api/issues", tags=["issues"])

# Application log — issue status changes land in pullbox.log.
app_log = logging.getLogger("pullbox")


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
    previous = issue.status
    issue.status = "wanted"
    await db.flush()
    app_log.info(
        "Issue id=%d #%s status %s → wanted (queued for download)",
        issue.id,
        issue.issue_number,
        previous,
    )
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
    previous = issue.status
    issue.status = "skipped"
    await db.flush()
    app_log.info(
        "Issue id=%d #%s status %s → skipped",
        issue.id,
        issue.issue_number,
        previous,
    )
    await db.refresh(issue)
    return IssueResponse.model_validate(issue)


# ── Story arcs for an issue ──────────────────────────────────────────────────


@router.get("/{issue_id}/arcs", response_model=list[StoryArcDetail])
async def issue_arcs(issue_id: int, provider: MetadataProviderDep, db: DbDep):
    """Every story arc this issue belongs to, each with its full member list.

    Enriches the issue on demand, then fetches each arc's cross-series issue list
    live from the metadata provider, annotating members in the local library.
    """
    issue = await _get_issue_or_404(issue_id, db)
    return await get_issue_arc_detail(db, provider, issue)
