"""Read-only feed for local companion apps (Thwip).

Why this exists rather than letting the companion talk to Metron itself: Metron
is one account behind a hard 20/min, 5000/day cap, and the limiter enforcing it
(``clients.metron._limiter``) is a *process* global. A second process calling
Metron independently would race the same budget with no coordination. Routing
the companion through PullBox keeps exactly one throttled caller and lets the
companion have Metron-sourced arcs without Metron credentials.

**Every handler here serves cached DB rows and never touches the metadata
provider.** That is a load-bearing invariant, not an optimisation: a companion
re-syncs on every library scan, and ``services.arcs._build_arc_detail`` fetches
live on each call, so wiring these to the provider would drain the daily budget
on a medium library. See ``ExternalArcDetail`` for what that costs in fidelity.

Access is gated on a shared token (``external_api_token``). With no token set the
routes are disabled outright rather than left open — this feed exposes the whole
library including on-disk paths.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from pullbox.deps import DbDep, SettingsDep
from pullbox.models import Issue, StoryArc
from pullbox.schemas import (
    ExternalArcDetail,
    ExternalArcMember,
    ExternalIssue,
    ExternalLibraryPage,
)
from pullbox.services.general import relative_to_library, resolve_library_path

router = APIRouter(prefix="/api/external", tags=["external"])

logger = logging.getLogger("pullbox")

# Bulk feed page size. The default keeps a full-library sync to a handful of
# round trips without building an unbounded response for a large collection.
_DEFAULT_LIMIT = 500
_MAX_LIMIT = 2000


def _require_token(settings, token: str | None) -> None:
    """Reject the request unless ``token`` matches the configured shared secret."""
    expected = (settings.external_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "External API is not configured. Set external_api_token in "
                "config.yaml (or PULLBOX_EXTERNAL_API_TOKEN) to enable it."
            ),
        )
    # Constant-time compare so a wrong token can't be recovered by timing.
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-PullBox-Token")


@router.get("/library", response_model=ExternalLibraryPage)
async def library_feed(
    settings: SettingsDep,
    db: DbDep,
    since: datetime | None = Query(
        None,
        description=(
            "Only issues changed at or after this timestamp. Matches on both "
            "updated_at and arcs_synced_at, since arc enrichment bumps only the "
            "latter — filtering on updated_at alone would silently miss issues "
            "that just gained their arcs."
        ),
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    x_pullbox_token: str | None = Header(None, alias="X-PullBox-Token"),
) -> ExternalLibraryPage:
    """Every issue PullBox holds a file for, with its series and cached arcs.

    Ordered by issue id so paging stays stable across requests.
    """
    _require_token(settings, x_pullbox_token)

    library_path = await resolve_library_path(db, settings.library_path)

    # Only issues with a file on disk are useful to a reader — the rest are
    # wanted/queued rows a companion has nothing to match against.
    conditions = [Issue.file_path.is_not(None), Issue.file_path != ""]
    if since is not None:
        conditions.append(
            or_(Issue.updated_at >= since, Issue.arcs_synced_at >= since)
        )

    total = (
        await db.execute(select(func.count()).select_from(Issue).where(*conditions))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Issue)
                .where(*conditions)
                .options(selectinload(Issue.series), selectinload(Issue.arcs))
                .order_by(Issue.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = [
        ExternalIssue(
            id=issue.id,
            metron_id=issue.metron_id,
            comicvine_id=issue.comicvine_id,
            issue_number=issue.issue_number,
            title=issue.title,
            cover_date=issue.cover_date,
            store_date=issue.store_date,
            cover_url=issue.cover_url,
            status=issue.status,
            file_path=issue.file_path,
            path_rel=relative_to_library(issue.file_path, library_path),
            updated_at=issue.updated_at,
            arcs_synced_at=issue.arcs_synced_at,
            series=issue.series,
            arcs=sorted(issue.arcs, key=lambda a: a.name),
        )
        for issue in rows
    ]

    return ExternalLibraryPage(
        total=total,
        limit=limit,
        offset=offset,
        library_path=library_path,
        items=items,
    )


@router.get("/arcs/{arc_id}", response_model=ExternalArcDetail)
async def arc_detail(
    arc_id: int,
    settings: SettingsDep,
    db: DbDep,
    x_pullbox_token: str | None = Header(None, alias="X-PullBox-Token"),
) -> ExternalArcDetail:
    """One cached arc plus the members PullBox owns.

    Members are local rows only — see ``ExternalArcDetail`` for why the live
    cross-series list is deliberately not fetched here.
    """
    _require_token(settings, x_pullbox_token)

    arc = (
        await db.execute(
            select(StoryArc)
            .where(StoryArc.id == arc_id)
            .options(selectinload(StoryArc.issues).selectinload(Issue.series))
        )
    ).scalar_one_or_none()
    if arc is None:
        raise HTTPException(status_code=404, detail="Story arc not found")

    library_path = await resolve_library_path(db, settings.library_path)

    members = [
        ExternalArcMember(
            issue_id=issue.id,
            metron_id=issue.metron_id,
            comicvine_id=issue.comicvine_id,
            series_id=issue.series_id,
            series_title=issue.series.title,
            issue_number=issue.issue_number,
            title=issue.title,
            cover_date=issue.cover_date,
            status=issue.status,
            file_path=issue.file_path,
            path_rel=(
                relative_to_library(issue.file_path, library_path)
                if issue.file_path
                else None
            ),
        )
        for issue in sorted(arc.issues, key=lambda i: i.id)
    ]

    return ExternalArcDetail(
        id=arc.id,
        metron_id=arc.metron_id,
        comicvine_id=arc.comicvine_id,
        name=arc.name,
        publisher=arc.publisher,
        cover_url=arc.cover_url,
        description=arc.description,
        count_of_issue_appearances=arc.count_of_issue_appearances,
        detail_synced_at=arc.detail_synced_at,
        members=members,
    )
