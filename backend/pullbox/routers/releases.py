"""Weekly releases API: GET /api/releases/weekly."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pullbox.deps import ComicVineClientDep, DbDep, SettingsDep
from pullbox.models import Issue, Series, WeeklyRelease
from pullbox.schemas import ReleaseIssueSummary, ReleaseSeriesSummary, WeeklyReleaseResponse

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


async def _refresh_week(db, cv_client, monday: date, sunday: date) -> None:
    """Fetch this week's releases from ComicVine and upsert into Series/Issue/WeeklyRelease.

    Uses the caller's DB session so all writes land in the same transaction.
    Never overwrites Issue.status on existing rows.
    """
    releases = await cv_client.get_weekly_releases(monday.isoformat(), sunday.isoformat())

    for release_data in releases:
        series_cv_id = str(release_data["series"]["comicvine_id"])

        result = await db.execute(select(Series).where(Series.comicvine_id == series_cv_id))
        series = result.scalar_one_or_none()
        if series is None:
            series = Series(
                comicvine_id=series_cv_id,
                title=release_data["series"].get("title", "Unknown Series"),
            )
            db.add(series)
            await db.flush()

        issue_cv_id = str(release_data["comicvine_id"])
        result = await db.execute(select(Issue).where(Issue.comicvine_id == issue_cv_id))
        issue = result.scalar_one_or_none()
        if issue is None:
            issue = Issue(
                series_id=series.id,
                comicvine_id=issue_cv_id,
                issue_number=str(release_data.get("issue_number", "")),
                title=release_data.get("title"),
                store_date=_parse_date_str(release_data.get("store_date")),
                cover_url=release_data.get("cover_url"),
                status="unknown",
            )
            db.add(issue)
            await db.flush()

        release_date = issue.store_date or monday
        result = await db.execute(
            select(WeeklyRelease).where(
                WeeklyRelease.issue_id == issue.id,
                WeeklyRelease.release_date == release_date,
            )
        )
        if result.scalar_one_or_none() is None:
            db.add(WeeklyRelease(issue_id=issue.id, release_date=release_date, source="comicvine"))

    await db.flush()


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
    cv: ComicVineClientDep,
    settings: SettingsDep,
    week: str | None = None,
):
    """Fetch releases from ComicVine for the given ISO week, upsert into DB, and return results.

    Falls back to cached DB data if the API key is not configured or ComicVine is unreachable.
    """
    if week is None:
        week = _current_week_str()

    try:
        monday, sunday = _week_date_range(week)
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="Invalid week format — expected YYYY-WW")

    if settings.comicvine_api_key:
        try:
            await _refresh_week(db, cv, monday, sunday)
        except Exception:
            logger.warning(
                "weekly_releases: ComicVine fetch failed for %s, returning cached data",
                week,
                exc_info=True,
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
