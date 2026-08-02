"""Duplicate-series maintenance — Settings → Duplicate Series.

Finding duplicates is free (a title grouping over rows already in the database);
merging them repoints issues and deletes rows, so it only ever happens on an
explicit request naming the exact series ids the user saw in the preview.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from pullbox.deps import DbDep
from pullbox.schemas import (
    DuplicateGroupResponse,
    DuplicateScanResponse,
    DuplicateSeriesRow,
    MergeAllResponse,
    MergeRequest,
    MergeResponse,
)
from pullbox.services.dedupe import DuplicateGroup, find_duplicate_groups, merge_series_group

logger = logging.getLogger(__name__)
app_log = logging.getLogger("pullbox")

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


def _to_response(group: DuplicateGroup) -> DuplicateGroupResponse:
    return DuplicateGroupResponse(
        key=group.key,
        title=group.title,
        conflicting_years=group.conflicting_years,
        mergeable=group.mergeable,
        rows=[
            DuplicateSeriesRow(
                id=r.id,
                metron_id=r.metron_id,
                comicvine_id=r.comicvine_id,
                title=r.title,
                publisher=r.publisher,
                start_year=r.start_year,
                subscribed=r.subscribed,
                auto_download=r.auto_download,
                cover_url=r.cover_url,
                issue_count=r.issue_count,
                downloaded_count=r.downloaded_count,
            )
            for r in group.rows
        ],
    )


# Registered as "" rather than "/" so GET /api/duplicates matches exactly and
# never triggers the project-wide 307-redirect gotcha (see architecture.md).
@router.get("", response_model=DuplicateScanResponse)
async def list_duplicates(db: DbDep) -> DuplicateScanResponse:
    """Every set of series sharing a normalized title."""
    groups = await find_duplicate_groups(db)
    return DuplicateScanResponse(
        groups=[_to_response(g) for g in groups],
        total_groups=len(groups),
        mergeable_groups=sum(1 for g in groups if g.mergeable),
        conflicting_groups=sum(1 for g in groups if g.conflicting_years),
    )


@router.post("/merge", response_model=MergeResponse)
async def merge(body: MergeRequest, db: DbDep) -> MergeResponse:
    """Merge one explicitly-named set of series into a single row."""
    try:
        result = await merge_series_group(db, body.series_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_log.info(
        "Merged duplicate series %s into %s", result.removed_series_ids, result.kept_series_id
    )
    return MergeResponse(
        kept_series_id=result.kept_series_id,
        removed_series_ids=result.removed_series_ids,
        issues_moved=result.issues_moved,
        issues_merged=result.issues_merged,
    )


@router.post("/merge-all", response_model=MergeAllResponse)
async def merge_all(db: DbDep) -> MergeAllResponse:
    """Merge every unambiguous group in one pass.

    Groups whose rows disagree on a non-null ``start_year`` are skipped — those
    are most likely distinct volumes that happen to share a title, and collapsing
    them would file one volume's issues under the other.
    """
    groups = await find_duplicate_groups(db)
    merged = 0
    skipped = 0
    issues_moved = 0
    issues_merged = 0

    for group in groups:
        if not group.mergeable:
            skipped += 1
            continue
        result = await merge_series_group(db, [r.id for r in group.rows])
        merged += 1
        issues_moved += result.issues_moved
        issues_merged += result.issues_merged

    app_log.info("Merged %d duplicate series groups (%d skipped)", merged, skipped)
    return MergeAllResponse(
        merged_groups=merged,
        skipped_groups=skipped,
        issues_moved=issues_moved,
        issues_merged=issues_merged,
    )
