"""Calendar API — upcoming issues on a date grid, Sonarr/Radarr style.

Answers "what is coming, and will PullBox grab it?" over an arbitrary date range.

Two deliberate choices:

* **Reads are pure cache.** Unlike ``/api/releases/weekly`` (which refreshes the
  requested week from the metadata provider on every page load), the calendar
  never calls a provider. A month view spans four to five weeks, so refreshing
  inline would spend four to five provider calls per navigation click and drain
  the shared rate-limit budget just by paging back and forth. Filling the
  calendar is the job of series sync and the nightly calendar refresh; the page
  offers an explicit Refresh button that triggers the latter.
* **Issues, not WeeklyRelease rows, are the source.** A subscribed series' sync
  pulls its whole issue list including future-dated issues, so the ``issues``
  table already knows about releases the weekly-release table has not been
  refreshed far enough ahead to cover.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep
from pullbox.models import DownloadJob, Issue, Series, StoryArc, issue_story_arcs
from pullbox.schemas import CalendarEntry, CalendarResponse, CalendarSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# A month grid is ~6 weeks; an agenda view a few months. This cap stops a
# hand-edited URL from asking for a decade of issues in one query.
MAX_RANGE_DAYS = 400

# An issue in one of these states is settled — nothing further will be
# downloaded for it, so the calendar shows it as history rather than a plan.
SETTLED_STATUSES = frozenset({"downloaded", "downloading", "skipped"})


def _effective_date():
    """Shelf date if the source gave us one, else the cover date.

    Comic metadata is inconsistent about which it populates, and a calendar with
    holes in it is worse than one that falls back to the cover month.
    """
    return func.coalesce(Issue.store_date, Issue.cover_date)


async def _subscribed_arc_issue_ids(db, issue_ids: list[int]) -> set[int]:
    """Which of these issues are on the calendar because of a *story arc*
    subscription rather than (or as well as) a series subscription."""
    if not issue_ids:
        return set()
    rows = (
        (
            await db.execute(
                select(issue_story_arcs.c.issue_id)
                .join(StoryArc, StoryArc.id == issue_story_arcs.c.story_arc_id)
                .where(
                    issue_story_arcs.c.issue_id.in_(issue_ids),
                    StoryArc.subscribed.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _latest_job_status(db, issue_ids: list[int]) -> dict[int, str]:
    """Most recent download job status per issue.

    ``Issue.status`` says wanted/downloaded; it does not say *failed*. A calendar
    that cannot show "this one is failing" hides exactly the row the user needs.
    """
    if not issue_ids:
        return {}
    rows = (
        await db.execute(
            select(DownloadJob.issue_id, DownloadJob.status)
            .where(DownloadJob.issue_id.in_(issue_ids))
            .order_by(DownloadJob.issue_id.asc(), DownloadJob.id.asc())
        )
    ).all()
    # Ordered ascending by id, so the last row written per issue wins.
    return {issue_id: status for issue_id, status in rows}


@router.get("", response_model=CalendarResponse)
async def calendar(
    db: DbDep,
    start: date = Query(..., description="First day of the range (inclusive)"),
    end: date = Query(..., description="Last day of the range (inclusive)"),
    scope: str = Query(
        "subscribed",
        description="'subscribed' — series/arcs you follow; 'all' — every known issue",
    ),
) -> CalendarResponse:
    """Issues releasing between ``start`` and ``end``, one entry per issue."""
    if end < start:
        raise HTTPException(status_code=422, detail="end must not be before start")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(status_code=422, detail=f"Range must not exceed {MAX_RANGE_DAYS} days")
    if scope not in ("subscribed", "all"):
        raise HTTPException(status_code=422, detail="scope must be 'subscribed' or 'all'")

    released = _effective_date()
    query = (
        select(Issue, released.label("release_date"))
        .join(Series, Issue.series_id == Series.id)
        .where(released >= start, released <= end)
        .options(selectinload(Issue.series))
        .order_by(released.asc(), Series.title.asc(), Issue.issue_number.asc())
    )

    if scope == "subscribed":
        # Either the series is followed, or the issue belongs to a followed arc —
        # both are subscriptions, and both mean PullBox intends to chase the issue.
        arc_members = (
            select(issue_story_arcs.c.issue_id)
            .join(StoryArc, StoryArc.id == issue_story_arcs.c.story_arc_id)
            .where(StoryArc.subscribed.is_(True))
        )
        query = query.where(or_(Series.subscribed.is_(True), Issue.id.in_(arc_members)))

    rows = (await db.execute(query)).all()
    issue_ids = [issue.id for issue, _ in rows]

    arc_ids = await _subscribed_arc_issue_ids(db, issue_ids)
    job_status = await _latest_job_status(db, issue_ids)

    entries: list[CalendarEntry] = []
    by_status: dict[str, int] = {}
    for issue, release_date in rows:
        series = issue.series
        sources = []
        if series is not None and series.subscribed:
            sources.append("series")
        if issue.id in arc_ids:
            sources.append("arc")

        entries.append(
            CalendarEntry(
                issue_id=issue.id,
                issue_number=issue.issue_number,
                title=issue.title,
                cover_url=issue.cover_url,
                status=issue.status,
                job_status=job_status.get(issue.id),
                release_date=release_date,
                date_source="store" if issue.store_date else "cover",
                series_id=issue.series_id,
                series_title=series.title if series else "",
                publisher=series.publisher if series else None,
                subscribed=bool(series and series.subscribed),
                auto_download=bool(series and series.auto_download),
                sources=sources,
            )
        )
        by_status[issue.status] = by_status.get(issue.status, 0) + 1

    pending = sum(1 for e in entries if e.status not in SETTLED_STATUSES)

    return CalendarResponse(
        start=start,
        end=end,
        scope=scope,
        entries=entries,
        summary=CalendarSummary(
            total=len(entries),
            pending=pending,
            by_status=by_status,
        ),
    )
