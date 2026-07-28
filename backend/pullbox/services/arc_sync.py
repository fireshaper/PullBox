"""Resolving a story arc's missing issues into local library rows.

``services/arcs.py`` answers "which arcs does this issue belong to" — it reads.
This module writes: given an arc, it fetches the arc's cross-series member list
and turns the members PullBox does *not* own into real ``Series``/``Issue`` rows
marked ``wanted``, so the download pipeline can go and get them. That is what
subscribing to an arc means.

**Cost model.** The member list is one provider call, but a member carries only an
id — resolving it to a series and issue number needs one ``get_issue`` per missing
member. On a big crossover that is dozens of calls against a shared, hard-capped
budget (see ``clients/metron._RateLimiter``), so every entry point takes a
``budget`` and stops when it runs out; the next run picks up where this one left
off. Members already in the library cost nothing — they are matched by id against
local rows before any lookup happens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pullbox.clients.metadata import RATE_LIMIT_ERRORS, MetadataProvider, ids_for
from pullbox.models import Issue, Series, StoryArc
from pullbox.services.arcs import _arc_key, _get_or_create_arc
from pullbox.services.queue import enqueue_issue

logger = logging.getLogger(__name__)
app_log = logging.getLogger("pullbox")

# Default cap on ``get_issue`` lookups per arc resolution. One call per missing
# member, so this bounds what a single run can spend of the shared provider budget.
DEFAULT_BUDGET = 25


@dataclass
class ArcSyncResult:
    """What one resolution pass did. ``added`` rows are new and ``wanted``."""

    members: int = 0
    in_library: int = 0
    added: int = 0
    enqueued: int = 0
    failed: int = 0
    remaining: int = 0
    rate_limited: bool = False
    job_ids: list[int] = field(default_factory=list)

    @property
    def budget_exhausted(self) -> bool:
        return self.remaining > 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


async def _local_issues_for_members(db: AsyncSession, members: list[dict]) -> dict[str, Issue]:
    """Index the local ``Issue`` rows matching any member id, keyed by every id they carry."""
    metron_ids = [m["metron_id"] for m in members if m.get("metron_id")]
    cv_ids = [m["comicvine_id"] for m in members if m.get("comicvine_id")]
    if not metron_ids and not cv_ids:
        return {}
    clauses = []
    if metron_ids:
        clauses.append(Issue.metron_id.in_(metron_ids))
    if cv_ids:
        clauses.append(Issue.comicvine_id.in_(cv_ids))
    rows = (
        (await db.execute(select(Issue).where(or_(*clauses)))).scalars().all()
    )
    index: dict[str, Issue] = {}
    for row in rows:
        if row.metron_id:
            index[row.metron_id] = row
        if row.comicvine_id:
            index[row.comicvine_id] = row
    return index


def _member_local(index: dict[str, Issue], member: dict) -> Issue | None:
    return index.get(member.get("metron_id") or "") or index.get(member.get("comicvine_id") or "")


async def _get_or_create_series(
    db: AsyncSession, cache: dict[str, Series], data: dict
) -> Series:
    """Find or create the ``Series`` an arc member belongs to.

    Created rows are **not** subscribed: the user subscribed to an arc, not to
    every series the arc touches. The series is still reachable from the arc page
    and its detail route; a later ``sync-issues`` fills in the metadata the
    embedded issue payload didn't carry (publisher, start year, cover).
    """
    key = data.get("metron_id") or data.get("comicvine_id") or ""
    if key in cache:
        return cache[key]

    clauses = []
    if data.get("metron_id"):
        clauses.append(Series.metron_id == data["metron_id"])
    if data.get("comicvine_id"):
        clauses.append(Series.comicvine_id == data["comicvine_id"])
    series = (
        (await db.execute(select(Series).where(or_(*clauses)))).scalars().first()
        if clauses
        else None
    )
    if series is None:
        series = Series(
            metron_id=data.get("metron_id"),
            comicvine_id=data.get("comicvine_id"),
            title=data.get("title") or "Unknown Series",
            publisher=data.get("publisher"),
            start_year=data.get("start_year"),
            subscribed=False,
            auto_download=False,
        )
        db.add(series)
        await db.flush()
        app_log.info(
            "Story arc sync added series %r (metron=%s cv=%s) for a missing arc issue",
            series.title,
            series.metron_id,
            series.comicvine_id,
        )
    cache[key] = series
    return series


async def _create_issue_from_detail(
    db: AsyncSession,
    arc: StoryArc,
    detail: dict,
    series_cache: dict[str, Series],
    arc_cache: dict[str, StoryArc],
) -> Issue | None:
    """Create the local ``Issue`` row for a resolved arc member, marked ``wanted``.

    Returns None when the payload has no series to hang the issue off — an issue
    row needs a ``series_id``, and guessing one would attach the book to the wrong
    title. The member simply stays unresolved and is retried on a later run.
    """
    series_data = detail.get("series")
    if not series_data:
        logger.warning(
            "Arc member metron=%s cv=%s has no series in its detail payload; skipping",
            detail.get("metron_id"),
            detail.get("comicvine_id"),
        )
        return None

    series = await _get_or_create_series(db, series_cache, series_data)

    # Resolve every arc the detail reported *before* building the issue, not just
    # the one being synced: the data is already in hand, so the other arcs' pages
    # get it for free. Assigning the collection at construction time also keeps
    # SQLAlchemy from lazy-loading `issue.arcs` in async context after the flush.
    linked = [arc]
    seen = {_arc_key({"metron_id": arc.metron_id, "comicvine_id": arc.comicvine_id})}
    for arc_data in detail.get("story_arcs") or []:
        key = _arc_key(arc_data)
        if key in seen:
            continue
        seen.add(key)
        linked.append(await _get_or_create_arc(db, arc_cache, arc_data))

    issue = Issue(
        series_id=series.id,
        metron_id=detail.get("metron_id"),
        comicvine_id=detail.get("comicvine_id"),
        issue_number=detail.get("issue_number") or "",
        title=detail.get("title"),
        cover_date=_parse_date(detail.get("cover_date")),
        store_date=_parse_date(detail.get("store_date")),
        cover_url=detail.get("cover_url"),
        status="wanted",
        # The detail call already returned this issue's full arc membership, so
        # it is enriched on arrival — no follow-up enrichment fetch is owed.
        arcs_synced_at=datetime.utcnow(),
        arcs=linked,
    )
    db.add(issue)
    await db.flush()
    return issue


def _apply_arc_metadata(arc: StoryArc, data: dict) -> None:
    arc.name = data.get("name") or arc.name
    arc.publisher = data.get("publisher") or arc.publisher
    arc.cover_url = data.get("cover_url") or arc.cover_url
    arc.description = data.get("description") or arc.description
    arc.count_of_issue_appearances = data.get("count_of_issue_appearances")
    arc.detail_synced_at = datetime.utcnow()
    if not arc.metron_id and data.get("metron_id"):
        arc.metron_id = data["metron_id"]
    if not arc.comicvine_id and data.get("comicvine_id"):
        arc.comicvine_id = data["comicvine_id"]


async def resolve_arc_members(
    db: AsyncSession,
    provider: MetadataProvider,
    arc: StoryArc,
    *,
    budget: int = DEFAULT_BUDGET,
    download: bool = False,
) -> ArcSyncResult:
    """Fetch ``arc``'s member list and create local rows for the ones we lack.

    Members already in the library are linked to the arc (so the arc page shows
    them) and cost no provider call. Each missing member costs one ``get_issue``;
    at most ``budget`` are resolved per call and ``result.remaining`` reports how
    many were left for the next run. A provider rate-limit stops the pass early
    with ``rate_limited=True`` — everything resolved so far is kept.

    ``download=True`` also enqueues each newly-created issue. The returned
    ``job_ids`` are *not* dispatched here: the caller must commit first, then fire
    ``run_job_now`` for each, so the worker never reads a half-written session.

    Flushes but does not commit.
    """
    result = ArcSyncResult()

    try:
        data = await provider.get_story_arc(**ids_for(arc))
    except RATE_LIMIT_ERRORS as exc:
        logger.warning("Arc sync for %r hit the provider rate limit: %s", arc.name, exc)
        result.rate_limited = True
        return result

    _apply_arc_metadata(arc, data)

    # Re-load with members eagerly present so appending never triggers a lazy load.
    arc = (
        await db.execute(
            select(StoryArc)
            .where(StoryArc.id == arc.id)
            .options(selectinload(StoryArc.issues))
        )
    ).scalar_one()

    # Held as a plain string: a savepoint rollback below expires the arc row, and
    # reading an attribute off an expired instance mid-loop (or in the final log
    # line) would fire lazy IO outside SQLAlchemy's greenlet context.
    arc_name = arc.name

    members = data.get("issues") or []
    result.members = len(members)
    local_index = await _local_issues_for_members(db, members)
    already_linked = {i.id for i in arc.issues}

    series_cache: dict[str, Series] = {}
    arc_cache: dict[str, StoryArc] = {}
    spent = 0

    for member in members:
        existing = _member_local(local_index, member)
        if existing is not None:
            result.in_library += 1
            if existing.id not in already_linked:
                arc.issues.append(existing)
                already_linked.add(existing.id)
            continue

        if spent >= budget:
            result.remaining += 1
            continue

        spent += 1
        try:
            detail = await provider.get_issue(
                metron_id=member.get("metron_id"), comicvine_id=member.get("comicvine_id")
            )
        except RATE_LIMIT_ERRORS as exc:
            logger.warning("Arc sync for %r paused on rate limit: %s", arc_name, exc)
            result.rate_limited = True
            result.remaining += 1
            break
        except Exception as exc:  # noqa: BLE001 — one bad member must not abort the arc
            logger.warning(
                "Arc member metron=%s cv=%s failed to resolve: %s",
                member.get("metron_id"),
                member.get("comicvine_id"),
                exc,
            )
            result.failed += 1
            continue

        # SAVEPOINT per member: a member the provider reports twice under two ids
        # would trip the issues' metadata-id UNIQUE constraints on flush, and an
        # un-rolled-back flush error poisons the session for every member after it.
        # Rolling back just this one keeps the rest of the arc resolvable.
        try:
            async with db.begin_nested():
                issue = await _create_issue_from_detail(
                    db, arc, detail, series_cache, arc_cache
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Arc member metron=%s cv=%s could not be stored: %s",
                member.get("metron_id"),
                member.get("comicvine_id"),
                exc,
            )
            # The rollback expunges rows created inside the savepoint and expires
            # the arc, so drop the caches and re-hydrate the members collection
            # before the next iteration appends to it.
            series_cache.clear()
            arc_cache.clear()
            await db.refresh(arc, ["issues"])
            already_linked = {i.id for i in arc.issues}
            result.failed += 1
            continue

        if issue is None:
            result.failed += 1
            continue
        already_linked.add(issue.id)
        result.added += 1

        if download:
            try:
                job, created = await enqueue_issue(issue.id, db)
            except ValueError:  # terminal status — nothing to enqueue
                continue
            if created:
                result.enqueued += 1
                result.job_ids.append(job.id)

    await db.flush()
    app_log.info(
        "Story arc %r synced: %d members, %d already held, %d added as wanted, "
        "%d enqueued, %d failed, %d left for the next run",
        arc_name,
        result.members,
        result.in_library,
        result.added,
        result.enqueued,
        result.failed,
        result.remaining,
    )
    return result


async def enqueue_arc_missing(db: AsyncSession, arc: StoryArc) -> list[int]:
    """Enqueue every issue linked to ``arc`` that isn't downloaded or in flight.

    Operates purely on rows PullBox already has — no provider call — so it is the
    cheap half of "get me this arc": run a sync first to discover members, then
    this to actually fetch them. Returns the ids of newly-created jobs; the caller
    commits and then dispatches them.

    ``skipped`` issues are left alone. Skipping is an explicit user decision about
    one issue, and a bulk arc action is not the place to silently reverse it.
    """
    issues = (
        (
            await db.execute(
                select(Issue)
                .join(Issue.arcs)
                .where(StoryArc.id == arc.id)
                .where(Issue.status.notin_(["downloaded", "downloading", "skipped"]))
            )
        )
        .scalars()
        .all()
    )

    job_ids: list[int] = []
    for issue in issues:
        try:
            job, created = await enqueue_issue(issue.id, db)
        except ValueError:
            continue
        if created:
            job_ids.append(job.id)
    await db.flush()
    app_log.info(
        "Story arc %r: enqueued %d missing issue(s) for download", arc.name, len(job_ids)
    )
    return job_ids
