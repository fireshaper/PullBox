"""Background ComicVine backfill for imported issues.

The import endpoint creates the ``Series``/``Issue`` rows immediately (with
``comicvine_id = NULL``) plus a ``pending`` ``ImportFile`` tracking row per issue.
This service backfills ComicVine metadata: for a given import-origin ``Series`` it
matches the volume, enriches its owned ``Issue`` rows in place, and stamps each
tracking row ``synced`` / ``unmatched`` / ``no_match`` (all terminal).

Throttling / rate-limit safety lives in the ComicVine client's shared limiter;
this module lets ``ComicVineRateLimitError`` propagate so the scheduler can pause.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.clients.comicvine import ComicVineClient
from pullbox.models import ImportFile, Issue, Series
from pullbox.services.library_import import normalize_issue_number

logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def pick_best_match(results: list[dict], title: str, year: int | None) -> dict | None:
    """Choose the best ComicVine search result for a scanned series.

    Prefers an exact (case-insensitive) title match with the same start year, then
    an exact title match, then the first result. Returns None if there are none.
    """
    if not results:
        return None
    norm = title.casefold().strip()
    if year is not None:
        for r in results:
            if (r.get("title") or "").casefold().strip() == norm and r.get("start_year") == year:
                return r
    for r in results:
        if (r.get("title") or "").casefold().strip() == norm:
            return r
    return results[0]


def _apply_volume_match(series: Series, match: dict) -> None:
    """Stamp a matched ComicVine volume's metadata onto an import-origin series."""
    series.comicvine_id = match["comicvine_id"]
    series.title = match.get("title") or series.title
    series.publisher = match.get("publisher") or series.publisher
    if match.get("start_year") is not None:
        series.start_year = match.get("start_year")
    series.cover_url = match.get("cover_url") or series.cover_url
    series.description = match.get("description") or series.description
    series.subscribed = True


async def resolve_series_for_import(
    db: AsyncSession,
    cv: ComicVineClient,
    series: Series,
    tracking_rows: Sequence[ImportFile],
) -> tuple[int, int, int]:
    """Backfill ComicVine metadata for one import-origin series.

    Matches ``series`` to a ComicVine volume (if not already), fetches the volume's
    issues, and for every pending ``tracking_rows`` entry enriches the owned
    ``Issue`` in place (matched by normalized issue number) and stamps the row
    ``synced`` — or ``unmatched`` (issue number not in the volume). If the series
    has no ComicVine match, all pending rows are marked ``no_match``. All outcomes
    are terminal (``synced_at`` set), so rows are never retried.

    Returns ``(synced, unmatched, no_match)``.
    """
    pending = [r for r in tracking_rows if r.status == "pending"]
    if not pending:
        return 0, 0, 0
    now = datetime.utcnow()

    if series.comicvine_id is None:
        results = await cv.search_series(series.title)
        match = pick_best_match(results, series.title, series.start_year)
        if match is None:
            for row in pending:
                row.status = "no_match"
                row.synced_at = now
                row.attempts += 1
            await db.flush()
            logger.info(
                "import backfill: no ComicVine match for %r (%s)",
                series.title,
                series.start_year or "no year",
            )
            return 0, 0, len(pending)
        _apply_volume_match(series, match)
        await db.flush()

    remote_issues = await cv.get_issues(series.comicvine_id)
    by_number: dict[str, dict] = {}
    for remote in remote_issues:
        by_number.setdefault(normalize_issue_number(remote.get("issue_number", "")), remote)

    issue_ids = [r.issue_id for r in pending]
    issues = (
        await db.execute(select(Issue).where(Issue.id.in_(issue_ids)))
    ).scalars().all()
    issues_by_id: dict[int, Issue] = {i.id: i for i in issues}

    synced = 0
    unmatched = 0
    used_cv_ids: set[str] = set()
    for row in pending:
        row.attempts += 1
        row.synced_at = now
        issue = issues_by_id.get(row.issue_id)
        remote = by_number.get(normalize_issue_number(issue.issue_number)) if issue else None
        if issue is None or remote is None:
            row.status = "unmatched"
            unmatched += 1
            continue
        cv_id = remote["comicvine_id"]
        # Skip cv_id assignment on duplicate issue numbers to avoid a unique clash;
        # still enrich display metadata and mark synced (terminal).
        if cv_id not in used_cv_ids:
            issue.comicvine_id = cv_id
            used_cv_ids.add(cv_id)
        issue.title = remote.get("title") or issue.title
        issue.cover_date = _parse_date(remote.get("cover_date"))
        issue.store_date = _parse_date(remote.get("store_date"))
        issue.cover_url = remote.get("cover_url") or issue.cover_url
        issue.description = remote.get("description") or issue.description
        row.status = "synced"
        synced += 1

    await db.flush()
    logger.info(
        "import backfill: %r → ComicVine %s (%s): %d synced, %d unmatched",
        series.title,
        series.comicvine_id,
        series.title,
        synced,
        unmatched,
    )
    return synced, unmatched, 0
