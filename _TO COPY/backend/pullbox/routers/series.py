"""Series and issue-list endpoints."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from pullbox.clients.metadata import ids_for
from pullbox.deps import DbDep, MetadataProviderDep, SettingsDep
from pullbox.models import Issue, PostProcessingSettings, Series
from pullbox.schemas import (
    AddSeriesRequest,
    IssueListItem,
    MarkAllWantedResponse,
    PaginatedSeriesResponse,
    SeriesDetailResponse,
    SeriesRescanResponse,
    SeriesResponse,
    SeriesSearchResult,
    SyncIssuesResponse,
    UpdateSeriesRequest,
)
from pullbox.services import series_scan
from pullbox.services.arcs import enrich_issue_arcs
from pullbox.services.general import resolve_library_path
from pullbox.services.library_import import normalize_issue_number
from pullbox.services.queue import enqueue_issue, run_job_now

router = APIRouter(prefix="/api/series", tags=["series"])

# Folder pattern to assume when the post-processing settings row doesn't exist
# yet. Read off the column so it can't drift from the model's declared default.
_DEFAULT_FOLDER_PATTERN: str = PostProcessingSettings.__table__.c.folder_pattern.default.arg

# Application log — series/issue lifecycle events land in pullbox.log.
app_log = logging.getLogger("pullbox")


def _parse_date(value: str | None) -> date | None:
    """Parse a YYYY-MM-DD string (or None) returned by the metadata API."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


async def _get_series_or_404(series_id: int, db) -> Series:
    row = await db.get(Series, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Series not found")
    return row


# ── Step 4.2 — Search the metadata provider ──────────────────────────────────


@router.get("/search", response_model=list[SeriesSearchResult])
async def search_series(
    provider: MetadataProviderDep,
    db: DbDep,
    q: str = Query(min_length=2),
):
    results = await provider.search_series(q)

    # A result is already in the library if either of its ids matches a stored series.
    metron_ids = [r["metron_id"] for r in results if r.get("metron_id")]
    cv_ids = [r["comicvine_id"] for r in results if r.get("comicvine_id")]
    have_metron: set[str] = set()
    have_cv: set[str] = set()
    if metron_ids:
        have_metron = set(
            (await db.execute(select(Series.metron_id).where(Series.metron_id.in_(metron_ids))))
            .scalars()
            .all()
        )
    if cv_ids:
        have_cv = set(
            (await db.execute(select(Series.comicvine_id).where(Series.comicvine_id.in_(cv_ids))))
            .scalars()
            .all()
        )

    def _in_library(r: dict) -> bool:
        return (r.get("metron_id") in have_metron and r.get("metron_id") is not None) or (
            r.get("comicvine_id") in have_cv and r.get("comicvine_id") is not None
        )

    return [SeriesSearchResult(**r, in_library=_in_library(r)) for r in results]


# ── Step 4.3 — Add series ─────────────────────────────────────────────────────


@router.post("/", response_model=SeriesDetailResponse, status_code=201)
async def add_series(
    body: AddSeriesRequest,
    provider: MetadataProviderDep,
    db: DbDep,
):
    # Check duplicate on whichever id(s) the request carries.
    dup_clauses = []
    if body.metron_id:
        dup_clauses.append(Series.metron_id == body.metron_id)
    if body.comicvine_id:
        dup_clauses.append(Series.comicvine_id == body.comicvine_id)
    existing = (
        await db.execute(select(Series).where(or_(*dup_clauses)))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Series already in library")

    volume = await provider.get_volume(metron_id=body.metron_id, comicvine_id=body.comicvine_id)

    series = Series(
        metron_id=volume.get("metron_id"),
        comicvine_id=volume.get("comicvine_id"),
        title=volume["title"],
        publisher=volume.get("publisher"),
        start_year=volume.get("start_year"),
        cover_url=volume.get("cover_url"),
        description=volume.get("description"),
        subscribed=body.subscribed,
        auto_download=body.auto_download,
    )
    db.add(series)
    await db.flush()
    await db.refresh(series)
    app_log.info(
        "Added series to library: %r (metron=%s cv=%s, subscribed=%s, auto_download=%s)",
        series.title,
        series.metron_id,
        series.comicvine_id,
        series.subscribed,
        series.auto_download,
    )
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.4 — List and get series ───────────────────────────────────────────


@router.get("/", response_model=PaginatedSeriesResponse)
async def list_series(
    db: DbDep,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    subscribed: bool | None = Query(default=None),
    all_: bool = Query(default=False, alias="all"),
):
    # Alphabetical (case-insensitive) so pagination and the frontend's A–Z jump
    # picker share one stable ordering.
    stmt_base = select(Series).order_by(func.lower(Series.title))
    if subscribed is not None:
        stmt_base = stmt_base.where(Series.subscribed == subscribed)

    total_result = await db.execute(select(func.count()).select_from(stmt_base.subquery()))
    total = total_result.scalar_one()

    if all_:
        # Load the entire (filtered) set in one shot — used by the Series page,
        # which reveals rows client-side and needs every letter available to jump.
        rows = (await db.execute(stmt_base)).scalars().all()
        return PaginatedSeriesResponse(
            total=total,
            page=1,
            per_page=total,
            items=[SeriesResponse.model_validate(s) for s in rows],
        )

    offset = (page - 1) * per_page
    rows = (
        await db.execute(stmt_base.offset(offset).limit(per_page))
    ).scalars().all()

    return PaginatedSeriesResponse(
        total=total,
        page=page,
        per_page=per_page,
        items=[SeriesResponse.model_validate(s) for s in rows],
    )


@router.get("/{series_id}", response_model=SeriesDetailResponse)
async def get_series(series_id: int, db: DbDep):
    series = await _get_series_or_404(series_id, db)
    return SeriesDetailResponse.model_validate(series)


# ── Enrich series from ComicVine ─────────────────────────────────────────────


@router.post("/{series_id}/enrich", response_model=SeriesDetailResponse)
async def enrich_series(series_id: int, provider: MetadataProviderDep, db: DbDep):
    """Fetch full metadata from the provider and update the series record.

    Safe to call repeatedly — only overwrites metadata fields, never touches
    subscribed/auto_download/status or any issue rows.
    """
    series = await _get_series_or_404(series_id, db)
    volume = await provider.get_volume(**ids_for(series))
    series.title = volume["title"]
    series.publisher = volume.get("publisher")
    series.start_year = volume.get("start_year")
    series.cover_url = volume.get("cover_url")
    series.description = volume.get("description")
    # Backfill a missing cross-reference id if the provider now supplies one.
    series.comicvine_id = series.comicvine_id or volume.get("comicvine_id")
    series.metron_id = series.metron_id or volume.get("metron_id")
    await db.flush()
    await db.refresh(series)
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.5 — Update series ─────────────────────────────────────────────────


@router.patch("/{series_id}", response_model=SeriesDetailResponse)
async def update_series(series_id: int, body: UpdateSeriesRequest, db: DbDep):
    series = await _get_series_or_404(series_id, db)

    if body.subscribed is not None:
        series.subscribed = body.subscribed
    if body.auto_download is not None:
        series.auto_download = body.auto_download

    await db.flush()
    await db.refresh(series)
    return SeriesDetailResponse.model_validate(series)


# ── Step 4.6 — Sync issues from the metadata provider ────────────────────────


@router.post("/{series_id}/sync-issues", response_model=SyncIssuesResponse)
async def sync_issues(series_id: int, provider: MetadataProviderDep, db: DbDep):
    series = await _get_series_or_404(series_id, db)

    # A series added before Metron was configured carries only a comicvine_id, and
    # id-based provider calls route by the ids a record actually has — so it would
    # stay pinned to ComicVine forever. Adopt the Metron id first (when Metron is
    # the primary source and knows this series) so this sync and every later one
    # run against Metron. No-op for ComicVine-primary setups.
    if series.metron_id is None and series.comicvine_id:
        resolved = await provider.resolve_metron_id(series.comicvine_id)
        if resolved:
            series.metron_id = resolved
            app_log.info(
                "Series %r matched to Metron id %s (was ComicVine-only)",
                series.title,
                resolved,
            )

    # Refresh series-level metadata (notably the cover image) alongside the issue
    # list. Imported series can land without a cover_url — an exact match from
    # search doesn't always carry the volume image — so a sync doubles as the fix
    # for a missing series cover. `or`-guards avoid clobbering existing values with
    # a null from the API.
    volume, remote_issues = await asyncio.gather(
        provider.get_volume(**ids_for(series)),
        provider.get_issues(**ids_for(series)),
    )
    series.title = volume.get("title") or series.title
    series.publisher = volume.get("publisher") or series.publisher
    if volume.get("start_year") is not None:
        series.start_year = volume.get("start_year")
    series.cover_url = volume.get("cover_url") or series.cover_url
    series.description = volume.get("description") or series.description

    # Load all existing issues for this series, indexed by each metadata id.
    existing_rows = (
        await db.execute(select(Issue).where(Issue.series_id == series_id))
    ).scalars().all()
    existing_by_metron: dict[str, Issue] = {
        i.metron_id: i for i in existing_rows if i.metron_id is not None
    }
    existing_by_cv_id: dict[str, Issue] = {
        i.comicvine_id: i for i in existing_rows if i.comicvine_id is not None
    }
    # Fallback pool for remotes the id pass doesn't claim, keyed by normalized issue
    # number. Covers import-origin rows that carry no id at all *and* rows carrying
    # only the other source's id — Metron's issue *list* endpoint omits ``cv_id``,
    # so a series that just graduated from ComicVine to Metron has zero id overlap
    # and would otherwise duplicate every single issue.
    by_number: dict[str, list[Issue]] = {}
    for i in existing_rows:
        by_number.setdefault(normalize_issue_number(i.issue_number), []).append(i)

    # Pass 1 — match on ids, so a number-based guess can never steal a row that a
    # later remote issue owns outright.
    claimed: set[int] = set()
    pairings: list[tuple[Issue | None, dict]] = []
    unresolved: list[dict] = []
    for remote in remote_issues:
        issue = None
        if remote.get("metron_id"):
            issue = existing_by_metron.get(remote["metron_id"])
        if issue is None and remote.get("comicvine_id"):
            issue = existing_by_cv_id.get(remote["comicvine_id"])
        if issue is None:
            unresolved.append(remote)
        else:
            claimed.add(issue.id)
            pairings.append((issue, remote))

    # Pass 2 — the leftovers match by number against rows nothing else claimed.
    for remote in unresolved:
        pool = by_number.get(normalize_issue_number(remote.get("issue_number", "")), [])
        issue = next((i for i in pool if i.id not in claimed), None)
        if issue is not None:
            claimed.add(issue.id)
        pairings.append((issue, remote))

    added = 0
    updated = 0
    new_issues: list[Issue] = []

    def _adopt_ids(issue: Issue, remote: dict) -> None:
        """Fill in whichever ids the remote record carries.

        No unique-constraint risk: an id already held by a *different* row would
        have been matched by pass 1, which pairs that remote with that row.
        """
        if remote.get("metron_id"):
            issue.metron_id = remote["metron_id"]
        if remote.get("comicvine_id"):
            issue.comicvine_id = remote["comicvine_id"]

    for issue, remote in pairings:
        metron_id = remote.get("metron_id")
        cv_id = remote.get("comicvine_id")
        cover_date = _parse_date(remote.get("cover_date"))
        store_date = _parse_date(remote.get("store_date"))

        if issue is not None:
            # Refresh metadata only — never touch status or file_path
            issue.issue_number = remote.get("issue_number", issue.issue_number)
            issue.title = remote.get("title", issue.title)
            issue.cover_date = cover_date
            issue.store_date = store_date
            issue.cover_url = remote.get("cover_url", issue.cover_url)
            issue.description = remote.get("description", issue.description)
            _adopt_ids(issue, remote)
            updated += 1
        else:
            issue = Issue(
                series_id=series_id,
                metron_id=metron_id,
                comicvine_id=cv_id,
                issue_number=remote.get("issue_number", ""),
                title=remote.get("title"),
                cover_date=cover_date,
                store_date=store_date,
                cover_url=remote.get("cover_url"),
                description=remote.get("description"),
            )
            db.add(issue)
            new_issues.append(issue)
            added += 1

    await db.flush()

    if added:
        app_log.info(
            "Added %d new issue(s) to watch for series %r (%d updated)",
            added,
            series.title,
            updated,
        )

    # Enrich story arc membership for every issue not yet enriched (all issues on
    # first sync, only new issues thereafter). Failures are non-fatal and retried
    # on the next sync. See services/arcs.py.
    all_issues = list({id(i): i for i in existing_rows + new_issues}.values())
    await enrich_issue_arcs(db, provider, all_issues)

    if series.auto_download and new_issues:
        new_job_ids: list[int] = []
        for issue in new_issues:
            try:
                job, created = await enqueue_issue(issue.id, db)
                if created:
                    new_job_ids.append(job.id)
            except ValueError:
                pass
        if new_job_ids:
            await db.commit()
            for job_id in new_job_ids:
                asyncio.create_task(run_job_now(job_id))

    return SyncIssuesResponse(added=added, updated=updated, total=added + updated)


# ── Re-scan the series folder on disk ────────────────────────────────────────


# Cap on how many unmatched filenames travel back to the UI — enough to see the
# pattern, not enough to bloat the response for a badly-named folder.
_UNMATCHED_LIMIT = 25


@router.post("/{series_id}/rescan", response_model=SeriesRescanResponse)
async def rescan_series(series_id: int, db: DbDep, settings: SettingsDep):
    """Reconcile this series' issues with the comic files actually on disk.

    Complements ``sync-issues`` (which talks to the metadata provider): this one
    never leaves the filesystem. Files that appeared in the series folder get
    linked to the matching issue number and marked downloaded; issues whose file
    is gone lose their ``file_path`` and drop back to *wanted* so they can be
    re-acquired. Nothing is enqueued — the user decides whether to re-download.
    """
    series = await _get_series_or_404(series_id, db)
    issues = (
        await db.execute(select(Issue).where(Issue.series_id == series_id))
    ).scalars().all()

    library_root = await resolve_library_path(db, settings.library_path)
    cfg = (
        await db.execute(select(PostProcessingSettings).limit(1))
    ).scalar_one_or_none()
    root = ((cfg.destination_root or "").strip() if cfg else "") or library_root
    folder_pattern = cfg.folder_pattern if cfg else _DEFAULT_FOLDER_PATTERN

    tracked_paths = [i.file_path for i in issues if i.file_path]
    folders = series_scan.series_folders(
        root=root,
        folder_pattern=folder_pattern,
        series=series,
        tracked_paths=tracked_paths,
    )
    scan = await asyncio.to_thread(series_scan.inspect, folders, tracked_paths)

    # ── Pass 1: tracked files that are gone ──────────────────────────────────
    missing_ids: set[int] = set()
    unchanged = 0
    for issue in issues:
        if not issue.file_path:
            continue
        if series_scan.path_key(issue.file_path) in scan.present:
            unchanged += 1
            # The file is there but the row disagrees (a manual status change, or
            # a job that failed after the move) — trust the disk.
            if issue.status != "downloaded":
                issue.status = "downloaded"
            continue
        missing_ids.add(issue.id)
        issue.file_path = None
        if issue.status in ("downloaded", "downloading"):
            issue.status = "wanted"

    # ── Pass 2: files on disk not linked to an issue ─────────────────────────
    # Issues cleared above are candidates again, so a renamed file re-links to
    # its own issue instead of being reported as unmatched.
    unclaimed: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.file_path:
            continue
        unclaimed.setdefault(normalize_issue_number(issue.issue_number), []).append(issue)

    found = 0
    relinked = 0
    unmatched: list[str] = []
    for file in scan.files:
        if series_scan.path_key(file.path) in scan.present:
            continue  # already tracked by an issue and still in place
        candidates = unclaimed.get(file.issue_number) if file.issue_number else None
        if not candidates:
            unmatched.append(file.path)
            continue
        issue = candidates.pop(0)
        issue.file_path = file.path
        issue.status = "downloaded"
        if issue.id in missing_ids:
            missing_ids.discard(issue.id)
            relinked += 1
        else:
            found += 1

    await db.flush()

    missing = len(missing_ids)
    if not folders:
        message = (
            "No folder to scan for this series — no files are tracked yet and the "
            f"post-processing folder pattern resolves to the library root ({root})."
        )
    else:
        message = (
            f"Scanned {len(scan.files)} file(s) in {len(folders)} folder(s): "
            f"{found} newly matched, {relinked} re-linked, {missing} missing, "
            f"{unchanged} unchanged."
        )

    if found or relinked or missing:
        app_log.info("Re-scan of series %r: %s", series.title, message)

    return SeriesRescanResponse(
        found=found,
        relinked=relinked,
        missing=missing,
        unchanged=unchanged,
        files_scanned=len(scan.files),
        folders=folders,
        unmatched_files=unmatched[:_UNMATCHED_LIMIT],
        message=message,
    )


# ── Mark all issues as wanted ────────────────────────────────────────────────


@router.post("/{series_id}/mark-all-wanted", response_model=MarkAllWantedResponse)
async def mark_all_wanted(series_id: int, db: DbDep):
    await _get_series_or_404(series_id, db)

    rows = (
        await db.execute(
            select(Issue).where(
                Issue.series_id == series_id,
                Issue.status.in_(["unknown", "failed"]),
            )
        )
    ).scalars().all()

    for issue in rows:
        issue.status = "wanted"

    await db.flush()

    new_job_ids: list[int] = []
    for issue in rows:
        try:
            job, created = await enqueue_issue(issue.id, db)
            if created:
                new_job_ids.append(job.id)
        except ValueError:
            pass

    if new_job_ids:
        await db.commit()
        for job_id in new_job_ids:
            asyncio.create_task(run_job_now(job_id))

    return MarkAllWantedResponse(marked=len(rows))


# ── Step 4.7 — Issue list ────────────────────────────────────────────────────


@router.get("/{series_id}/issues", response_model=list[IssueListItem])
async def list_issues(
    series_id: int,
    db: DbDep,
    status: str | None = Query(default=None),
):
    await _get_series_or_404(series_id, db)

    stmt = (
        select(Issue)
        .where(Issue.series_id == series_id)
        .options(selectinload(Issue.arcs))
    )
    if status is not None:
        stmt = stmt.where(Issue.status == status)
    stmt = stmt.order_by(Issue.issue_number)

    rows = (await db.execute(stmt)).scalars().all()
    return [IssueListItem.model_validate(i) for i in rows]
