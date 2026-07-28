"""Import an existing on-disk comic library into PullBox.

Three endpoints:
- ``POST /scan``   — walk a server-side folder into candidate series (filesystem only).
- ``POST /import`` — create the real ``Series``/``Issue`` rows immediately (with
  ``comicvine_id = NULL``) plus one ``ImportFile`` tracking row per issue (status
  ``pending``). Makes **no** ComicVine calls: the library is populated from the
  parsed folder/filename data right away. The ``sync_imported_issues`` scheduler job
  later backfills ComicVine metadata in throttled batches.
- ``GET /status``  — backfill snapshot (pending / synced / unmatched / no-match counts).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from pullbox.deps import DbDep
from pullbox.models import ImportFile, Issue, Series
from pullbox.schemas import (
    ImportStatusResponse,
    LibraryImportRequest,
    LibraryImportResponse,
    LibraryScanRequest,
    LibraryScanResponse,
    ScannedFile,
    ScannedSeries,
)
from pullbox.services.library_import import scan_library

# Dedicated library-import log — routes to library-import.log (see logging_config).
logger = logging.getLogger("pullbox.library_import")

router = APIRouter(prefix="/api/library-import", tags=["library-import"])


@router.post("/scan", response_model=LibraryScanResponse)
async def scan(body: LibraryScanRequest):
    path = body.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="A library path is required")
    logger.info("Scanning library path: %s", path)
    try:
        result = scan_library(path)
    except FileNotFoundError:
        logger.warning("Scan failed — path does not exist: %s", path)
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")
    except NotADirectoryError:
        logger.warning("Scan failed — path is not a directory: %s", path)
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")

    logger.info(
        "Scan complete: %d candidate series found under %s (%d file(s) unparsed)",
        len(result.series),
        result.root,
        result.unparsed_count,
    )
    return LibraryScanResponse(
        root=result.root,
        unparsed_count=result.unparsed_count,
        series=[
            ScannedSeries(
                title=s.title,
                year=s.year,
                file_count=s.file_count,
                files=[
                    ScannedFile(file_path=f.file_path, issue_number=f.issue_number)
                    for f in s.files
                ],
            )
            for s in result.series
        ],
    )


async def _get_or_create_import_series(db, title: str, year: int | None) -> Series:
    """Find-or-create an import-origin Series (comicvine_id NULL) by title + year."""
    year_filter = Series.start_year.is_(None) if year is None else Series.start_year == year
    existing = (
        await db.execute(
            select(Series).where(
                func.lower(Series.title) == title.casefold(),
                year_filter,
                Series.comicvine_id.is_(None),
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing
    series = Series(
        comicvine_id=None,
        title=title,
        start_year=year,
        # Owned comics: subscribe so they show on the (subscribed-only) Series page.
        subscribed=True,
    )
    db.add(series)
    await db.flush()
    return series


@router.post("/import", response_model=LibraryImportResponse)
async def import_library(body: LibraryImportRequest, db: DbDep):
    """Create Series/Issue rows immediately + queue each issue for ComicVine backfill."""
    series_queued = 0
    files_queued = 0
    errors: list[str] = []

    logger.info("Library import starting: %d series selected", len(body.series))

    for selection in body.series:
        try:
            series = await _get_or_create_import_series(db, selection.title, selection.year)
            added = 0
            for scanned in selection.files:
                # Idempotent re-import: skip files already tracked in the library.
                already = (
                    await db.execute(
                        select(Issue.id).where(Issue.file_path == scanned.file_path)
                    )
                ).first()
                if already is not None:
                    continue
                issue = Issue(
                    series_id=series.id,
                    comicvine_id=None,
                    issue_number=scanned.issue_number or "",
                    status="downloaded",
                    file_path=scanned.file_path,
                )
                db.add(issue)
                await db.flush()
                db.add(ImportFile(issue_id=issue.id, series_id=series.id, status="pending"))
                added += 1
            await db.flush()
            series_queued += 1
            files_queued += added
            logger.info(
                "Imported %r (%s): %d issue(s) added",
                selection.title,
                selection.year or "no year",
                added,
            )
        except Exception as exc:  # noqa: BLE001 — isolate one bad series from the batch
            logger.warning("Library import failed for %r: %s", selection.title, exc)
            errors.append(f"{selection.title}: {exc}")

    logger.info(
        "Library import complete: %d series, %d issues added",
        series_queued,
        files_queued,
    )
    return LibraryImportResponse(
        series_queued=series_queued,
        files_queued=files_queued,
        errors=errors,
    )


@router.get("/status", response_model=ImportStatusResponse)
async def import_status(db: DbDep):
    """Snapshot of the imported-issue ComicVine backfill."""

    async def _count(status: str) -> int:
        return (
            await db.execute(
                select(func.count()).select_from(ImportFile).where(ImportFile.status == status)
            )
        ).scalar_one()

    series_pending = (
        await db.execute(
            select(func.count(func.distinct(ImportFile.series_id))).where(
                ImportFile.status == "pending"
            )
        )
    ).scalar_one()

    return ImportStatusResponse(
        pending_files=await _count("pending"),
        series_pending=series_pending,
        synced_files=await _count("synced"),
        unmatched_files=await _count("unmatched"),
        no_match_files=await _count("no_match"),
    )
