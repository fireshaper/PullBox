"""Background metadata backfill for imported issues.

The import endpoint creates the ``Series``/``Issue`` rows immediately (with no
metadata id) plus a ``pending`` ``ImportFile`` tracking row per issue. This service
backfills metadata via the provider: for a given import-origin ``Series`` it matches
the series, enriches its owned ``Issue`` rows in place, and stamps each tracking row
``synced`` / ``unmatched`` / ``no_match`` (all terminal).

Throttling / rate-limit safety lives in each client's shared limiter; this module
lets the provider rate-limit errors propagate so the scheduler can pause.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.clients.metadata import MetadataProvider, ids_for
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
    """Choose the best search result for a scanned series.

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
    """Stamp a matched series' metadata onto an import-origin series."""
    series.metron_id = match.get("metron_id")
    series.comicvine_id = match.get("comicvine_id")
    series.title = match.get("title") or series.title
    series.publisher = match.get("publisher") or series.publisher
    if match.get("start_year") is not None:
        series.start_year = match.get("start_year")
    series.cover_url = match.get("cover_url") or series.cover_url
    series.description = match.get("description") or series.description
    series.subscribed = True


async def _merge_into_existing_series(db: AsyncSession, src: Series, dst: Series) -> None:
    """Move an import-origin series' issues + tracking rows onto an existing series.

    Used when a scanned series matches a series that another ``Series`` row already
    represents (typically the same book previously added via the pull list). Stamping
    the metadata id onto ``src`` would violate the ``metron_id``/``comicvine_id`` UNIQUE
    constraints, so we consolidate everything onto ``dst`` (which already owns the id)
    and delete the now-empty source. Bulk UPDATEs keep it independent of which rows
    happen to be loaded in the session.
    """
    await db.execute(update(Issue).where(Issue.series_id == src.id).values(series_id=dst.id))
    await db.execute(
        update(ImportFile).where(ImportFile.series_id == src.id).values(series_id=dst.id)
    )
    dst.subscribed = True  # owned comics stay visible on the (subscribed-only) Series page
    await db.flush()
    await db.delete(src)
    await db.flush()


async def resolve_series_for_import(
    db: AsyncSession,
    provider: MetadataProvider,
    series: Series,
    tracking_rows: Sequence[ImportFile],
) -> tuple[int, int, int]:
    """Backfill metadata for one import-origin series.

    Matches ``series`` to a provider series (if not already), fetches its issues, and
    for every pending ``tracking_rows`` entry enriches the owned ``Issue`` in place
    (matched by normalized issue number) and stamps the row ``synced`` — or
    ``unmatched`` (issue number not in the series). If the series has no match, all
    pending rows are marked ``no_match``. All outcomes are terminal (``synced_at``
    set), so rows are never retried.

    Returns ``(synced, unmatched, no_match)``.
    """
    pending = [r for r in tracking_rows if r.status == "pending"]
    if not pending:
        return 0, 0, 0
    now = datetime.utcnow()

    if series.metron_id is None and series.comicvine_id is None:
        results = await provider.search_series(series.title)
        match = pick_best_match(results, series.title, series.start_year)
        if match is None:
            for row in pending:
                row.status = "no_match"
                row.synced_at = now
                row.attempts += 1
            await db.flush()
            logger.info(
                "import backfill: no match for %r (%s)",
                series.title,
                series.start_year or "no year",
            )
            return 0, 0, len(pending)

        # If the matched series already exists as another Series row (e.g. the same
        # book was added earlier via the pull list), we can't stamp its id onto this
        # import-origin series without violating the metadata-id UNIQUE constraints.
        # Consolidate the imported issues into that existing series and enrich there.
        match_clauses = []
        if match.get("metron_id"):
            match_clauses.append(Series.metron_id == match["metron_id"])
        if match.get("comicvine_id"):
            match_clauses.append(Series.comicvine_id == match["comicvine_id"])
        existing = (
            (
                await db.execute(
                    select(Series).where(or_(*match_clauses), Series.id != series.id)
                )
            ).scalar_one_or_none()
            if match_clauses
            else None
        )
        if existing is not None:
            logger.info(
                "import backfill: %r matches metron=%s cv=%s, already present as "
                "series id=%d — merging imported issues into it",
                series.title,
                match.get("metron_id"),
                match.get("comicvine_id"),
                existing.id,
            )
            await _merge_into_existing_series(db, series, existing)
            series = existing
        else:
            _apply_volume_match(series, match)
            await db.flush()

    remote_issues = await provider.get_issues(**ids_for(series))
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
    # Seed "already taken" with any of this series' issue ids that other rows already
    # own (e.g. a merged series' pre-existing pull-list issues), so enriching never
    # trips the issues' metadata-id UNIQUE constraints.
    remote_metron = [r["metron_id"] for r in remote_issues if r.get("metron_id")]
    remote_cv = [r["comicvine_id"] for r in remote_issues if r.get("comicvine_id")]
    used_metron: set[str] = (
        set(
            (await db.execute(select(Issue.metron_id).where(Issue.metron_id.in_(remote_metron))))
            .scalars()
            .all()
        )
        if remote_metron
        else set()
    )
    used_cv: set[str] = (
        set(
            (
                await db.execute(
                    select(Issue.comicvine_id).where(Issue.comicvine_id.in_(remote_cv))
                )
            )
            .scalars()
            .all()
        )
        if remote_cv
        else set()
    )
    for row in pending:
        row.attempts += 1
        row.synced_at = now
        issue = issues_by_id.get(row.issue_id)
        remote = by_number.get(normalize_issue_number(issue.issue_number)) if issue else None
        if issue is None or remote is None:
            row.status = "unmatched"
            unmatched += 1
            continue
        # Skip id assignment on duplicate issue numbers to avoid a unique clash; still
        # enrich display metadata and mark synced (terminal).
        metron_id = remote.get("metron_id")
        cv_id = remote.get("comicvine_id")
        if metron_id and metron_id not in used_metron:
            issue.metron_id = metron_id
            used_metron.add(metron_id)
        if cv_id and cv_id not in used_cv:
            issue.comicvine_id = cv_id
            used_cv.add(cv_id)
        issue.title = remote.get("title") or issue.title
        issue.cover_date = _parse_date(remote.get("cover_date"))
        issue.store_date = _parse_date(remote.get("store_date"))
        issue.cover_url = remote.get("cover_url") or issue.cover_url
        issue.description = remote.get("description") or issue.description
        row.status = "synced"
        synced += 1

    await db.flush()
    logger.info(
        "import backfill: %r → metron=%s cv=%s: %d synced, %d unmatched",
        series.title,
        series.metron_id,
        series.comicvine_id,
        synced,
        unmatched,
    )
    return synced, unmatched, 0
