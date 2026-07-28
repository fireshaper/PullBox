"""File Health API — find comic files on disk that readers will choke on.

Drives Settings → File Health. A scan inspects two sets of paths: every
``Issue.file_path`` PullBox tracks, plus every comic file found by walking the
library root (so files that arrived outside PullBox are checked too). Results
replace the previous scan's rows wholesale — see ``models.FileIssue``.

The actual inspection lives in ``services/file_health.py`` and runs in a worker
thread; this module only handles path collection, persistence and shaping.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep, SettingsDep
from pullbox.models import FileIssue, Issue
from pullbox.schemas import (
    FileHealthResponse,
    FileHealthScanRequest,
    FileHealthScanResponse,
    FileHealthSummary,
    FileIssueRecheckResponse,
    FileIssueResponse,
)
from pullbox.services import file_health as fh
from pullbox.services import sync_status as sync_svc
from pullbox.services.general import normalize_path, resolve_library_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/file-health", tags=["file-health"])

# Worst-first, so the list opens on the things that actually break a reader.
_KIND_ORDER = {
    fh.MISSING: 0,
    fh.WRONG_FORMAT: 1,
    fh.CORRUPT: 2,
    fh.EMPTY: 3,
    fh.UNKNOWN_FORMAT: 4,
    fh.UNREADABLE: 5,
    fh.NO_IMAGES: 6,
}


def _path_key(path: str) -> str:
    """Comparison key for two spellings of the same file.

    A tracked ``file_path`` and the same file found by walking the library can
    differ in separator and casing (``D:\\Comics\\x.cbz`` vs ``D:/comics/x.cbz``),
    and both reach the same file on Windows. Match on the normalized casefolded
    form but always store/display the original spelling.
    """
    return normalize_path(path).casefold()


def _to_response(row: FileIssue) -> FileIssueResponse:
    """Flatten the joined Issue/Series onto the response for the list view."""
    resp = FileIssueResponse.model_validate(row)
    issue = row.issue
    if issue is not None:
        resp.issue_number = issue.issue_number
        if issue.series is not None:
            resp.series_id = issue.series.id
            resp.series_title = issue.series.title
    return resp


def _summarize(rows: list[FileIssue]) -> FileHealthSummary:
    by_kind: dict[str, int] = {}
    errors = 0
    for row in rows:
        by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
        if row.severity == fh.ERROR:
            errors += 1
    return FileHealthSummary(
        total=len(rows), errors=errors, warnings=len(rows) - errors, by_kind=by_kind
    )


async def _load_rows(db) -> list[FileIssue]:
    result = await db.execute(
        select(FileIssue)
        .options(selectinload(FileIssue.issue).selectinload(Issue.series))
        .order_by(FileIssue.file_path.asc())
    )
    rows = list(result.scalars().all())
    rows.sort(key=lambda r: (_KIND_ORDER.get(r.kind, 99), r.file_path.casefold()))
    return rows


async def _build_response(db, settings) -> tuple[list[FileIssue], datetime | None, str | None, str]:
    rows = await _load_rows(db)
    status = await sync_svc.get_sync(db, sync_svc.FILE_HEALTH)
    root = await resolve_library_path(db, settings.library_path)
    return (
        rows,
        status.last_run_at if status else None,
        status.message if status else None,
        root,
    )


@router.get("", response_model=FileHealthResponse)
async def list_file_issues(db: DbDep, settings: SettingsDep):
    """The findings from the last scan. Empty until a scan has been run."""
    rows, last_at, last_msg, root = await _build_response(db, settings)
    return FileHealthResponse(
        summary=_summarize(rows),
        issues=[_to_response(r) for r in rows],
        last_scan_at=last_at,
        last_scan_message=last_msg,
        scanned_root=root,
    )


@router.post("/scan", response_model=FileHealthScanResponse)
async def scan(body: FileHealthScanRequest, db: DbDep, settings: SettingsDep):
    """Inspect the library and replace the stored findings.

    Runs inline (the user is waiting on it). A deep scan of a large library
    reads every byte of every archive and can take minutes.
    """
    root = (body.path or "").strip() or await resolve_library_path(db, settings.library_path)

    # Tracked files first, so a path known to PullBox keeps its issue link even
    # when the library walk reports the same file under a different spelling.
    tracked = (
        await db.execute(
            select(Issue.id, Issue.file_path).where(Issue.file_path.isnot(None))
        )
    ).all()

    # key → (path as we will store it, owning issue id or None)
    candidates: dict[str, tuple[str, int | None]] = {}
    for issue_id, path in tracked:
        if path and path.strip():
            candidates.setdefault(_path_key(path), (path, issue_id))

    walked = await asyncio.to_thread(fh.iter_comic_files, root)
    for file in walked:
        candidates.setdefault(_path_key(str(file)), (str(file), None))

    paths = [path for path, _ in candidates.values()]
    findings = await asyncio.to_thread(fh.scan_paths, paths, deep=body.deep)

    # Replace the previous scan wholesale — anything fixed since then drops off.
    await db.execute(delete(FileIssue))
    now = datetime.now(tz=timezone.utc)
    for finding in findings:
        _, issue_id = candidates[_path_key(finding.file_path)]
        db.add(
            FileIssue(
                issue_id=issue_id,
                file_path=finding.file_path,
                kind=finding.problem.kind,
                severity=finding.problem.severity,
                detail=finding.problem.detail,
                size_bytes=finding.problem.size_bytes,
                detected_at=now,
            )
        )
    await db.flush()

    scanned = len(paths)
    if not walked and not Path(root).is_dir():
        message = (
            f"Library folder {root!r} is not readable — only the "
            f"{scanned} tracked file(s) were checked."
        )
        success = False
    else:
        message = f"Checked {scanned} file(s); found {len(findings)} problem(s)."
        success = True
    await sync_svc.record_sync(db, sync_svc.FILE_HEALTH, success=success, message=message)

    rows = await _load_rows(db)
    return FileHealthScanResponse(
        summary=_summarize(rows),
        issues=[_to_response(r) for r in rows],
        last_scan_at=now,
        last_scan_message=message,
        scanned_root=root,
        files_scanned=scanned,
    )


@router.post("/{file_issue_id}/recheck", response_model=FileIssueRecheckResponse)
async def recheck(file_issue_id: int, db: DbDep):
    """Re-inspect one file after fixing it. Clears the row when it now passes."""
    row = await db.get(FileIssue, file_issue_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File issue not found")

    problem = await asyncio.to_thread(fh.inspect_file, row.file_path, deep=True)
    if problem is None:
        await db.delete(row)
        await db.flush()
        return FileIssueRecheckResponse(resolved=True, issue=None)

    row.kind = problem.kind
    row.severity = problem.severity
    row.detail = problem.detail
    row.size_bytes = problem.size_bytes
    row.detected_at = datetime.now(tz=timezone.utc)
    await db.flush()

    fresh = (
        await db.execute(
            select(FileIssue)
            .where(FileIssue.id == row.id)
            .options(selectinload(FileIssue.issue).selectinload(Issue.series))
        )
    ).scalar_one()
    return FileIssueRecheckResponse(resolved=False, issue=_to_response(fresh))


@router.delete("/{file_issue_id}", status_code=204)
async def dismiss(file_issue_id: int, db: DbDep):
    """Drop one finding from the list. The next scan will re-report it if it persists."""
    row = await db.get(FileIssue, file_issue_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File issue not found")
    await db.delete(row)
    await db.flush()
    return Response(status_code=204)
