"""Detect and merge duplicate Series rows.

PullBox talks to two metadata sources whose ids live in separate namespaces, and
Metron's *list* endpoints omit ``cv_id``. So a weekly refresh served by Metron
cannot recognise the ComicVine-sourced rows an earlier refresh created (or vice
versa) and mints a parallel set: same book, two ``Series`` rows, two ``Issue``
rows, two entries on the pull list.

``routers/releases.py`` now bridges the id spaces on write so this stops
happening going forward. This module is the cleanup for rows created before
that, exposed as an explicit, previewable Settings action rather than an
automatic migration — merging series repoints issues and deletes rows, and that
is not something to do to a library behind the user's back.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import selectinload

from pullbox.models import (
    DownloadJob,
    FileIssue,
    ImportFile,
    Issue,
    Series,
    WeeklyRelease,
    issue_story_arcs,
)
from pullbox.services.library_import import normalize_issue_number

logger = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize_title(title: str | None) -> str:
    """Fold a series title to a comparison key.

    The two sources punctuate differently — ``"Batman / Superman: World's
    Finest"`` against ``"Batman/Superman: World's Finest"`` — so everything that
    is not a letter or digit is dropped.
    """
    return _NON_ALNUM.sub("", (title or "").lower())


async def find_series_for_release(db, ids: dict, title: str | None) -> Series | None:
    """Locate the local Series for a provider record, bridging the two id spaces.

    An id match is authoritative. When it misses, fall back to the normalized
    title — this is the case that used to create duplicates, because a Metron
    weekly record carries no ``cv_id`` and so can never match a ComicVine-sourced
    row on id alone.

    The fallback only fires when **exactly one** local series has that title. Two
    candidates means two volumes share a name and picking one would mis-file
    issues, so the caller creates a new row instead — the same "never guess"
    stance ``arc_sync`` takes with a member whose series is unknown.

    On a successful title match the record's id is written onto the row, so the
    two id spaces are bridged permanently and later refreshes match on id.
    """
    clauses = []
    if ids.get("metron_id"):
        clauses.append(Series.metron_id == ids["metron_id"])
    if ids.get("comicvine_id"):
        clauses.append(Series.comicvine_id == ids["comicvine_id"])
    if clauses:
        found = (await db.execute(select(Series).where(or_(*clauses)))).scalars().first()
        if found is not None:
            return found

    key = normalize_title(title)
    if not key:
        return None
    candidates = (
        (await db.execute(select(Series).where(Series.norm_title == key).limit(2))).scalars().all()
    )
    if len(candidates) != 1:
        return None

    match = candidates[0]
    # Adopt the incoming id, but never overwrite a different one already there —
    # that would silently repoint the row at another volume.
    if ids.get("metron_id") and match.metron_id is None:
        match.metron_id = ids["metron_id"]
    if ids.get("comicvine_id") and match.comicvine_id is None:
        match.comicvine_id = ids["comicvine_id"]
    return match


async def find_issue_for_release(db, ids: dict, series_id: int, issue_number: str) -> Issue | None:
    """The Issue counterpart of :func:`find_series_for_release`.

    Falls back to the normalized issue number *within the resolved series*, which
    is a far tighter scope than the series-title fallback — the series is already
    known, so matching "1" to "01" cannot stray onto another book.
    """
    clauses = []
    if ids.get("metron_id"):
        clauses.append(Issue.metron_id == ids["metron_id"])
    if ids.get("comicvine_id"):
        clauses.append(Issue.comicvine_id == ids["comicvine_id"])
    if clauses:
        found = (await db.execute(select(Issue).where(or_(*clauses)))).scalars().first()
        if found is not None:
            return found

    key = normalize_issue_number(issue_number)
    if not key:
        return None
    for candidate in (
        (await db.execute(select(Issue).where(Issue.series_id == series_id))).scalars().all()
    ):
        if normalize_issue_number(candidate.issue_number) == key:
            if ids.get("metron_id") and candidate.metron_id is None:
                candidate.metron_id = ids["metron_id"]
            if ids.get("comicvine_id") and candidate.comicvine_id is None:
                candidate.comicvine_id = ids["comicvine_id"]
            return candidate
    return None


@dataclass
class SeriesCandidate:
    """One row within a duplicate group, with the counts the UI shows."""

    id: int
    metron_id: str | None
    comicvine_id: str | None
    title: str
    publisher: str | None
    start_year: int | None
    subscribed: bool
    auto_download: bool
    cover_url: str | None
    issue_count: int
    downloaded_count: int


@dataclass
class DuplicateGroup:
    key: str
    title: str
    rows: list[SeriesCandidate]
    # True when the rows disagree on a non-null start_year, i.e. these are
    # probably genuinely different volumes that happen to share a title
    # (Action Comics 1938 vs 2011). Reported, never auto-merged.
    conflicting_years: bool = False

    @property
    def mergeable(self) -> bool:
        return not self.conflicting_years and len(self.rows) > 1


@dataclass
class MergeResult:
    kept_series_id: int
    removed_series_ids: list[int] = field(default_factory=list)
    issues_moved: int = 0
    issues_merged: int = 0


async def _series_counts(db) -> dict[int, tuple[int, int]]:
    """(issue_count, downloaded_count) per series id, in one pass."""
    rows = (await db.execute(select(Issue.series_id, Issue.status))).all()
    counts: dict[int, list[int]] = {}
    for series_id, status in rows:
        entry = counts.setdefault(series_id, [0, 0])
        entry[0] += 1
        if status == "downloaded":
            entry[1] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


async def find_duplicate_groups(db) -> list[DuplicateGroup]:
    """Every set of Series rows sharing a normalized title, worst first."""
    series = list((await db.execute(select(Series))).scalars().all())
    counts = await _series_counts(db)

    grouped: dict[str, list[Series]] = {}
    for s in series:
        key = normalize_title(s.title)
        if not key:
            continue
        grouped.setdefault(key, []).append(s)

    groups: list[DuplicateGroup] = []
    for key, rows in grouped.items():
        if len(rows) < 2:
            continue
        years = {s.start_year for s in rows if s.start_year is not None}
        candidates = [
            SeriesCandidate(
                id=s.id,
                metron_id=s.metron_id,
                comicvine_id=s.comicvine_id,
                title=s.title,
                publisher=s.publisher,
                start_year=s.start_year,
                subscribed=s.subscribed,
                auto_download=s.auto_download,
                cover_url=s.cover_url,
                issue_count=counts.get(s.id, (0, 0))[0],
                downloaded_count=counts.get(s.id, (0, 0))[1],
            )
            for s in rows
        ]
        candidates.sort(key=lambda c: (-c.issue_count, c.id))
        groups.append(
            DuplicateGroup(
                key=key,
                title=candidates[0].title,
                rows=candidates,
                conflicting_years=len(years) > 1,
            )
        )

    groups.sort(key=lambda g: (-len(g.rows), g.title.lower()))
    return groups


def pick_winner(rows: list[Series]) -> Series:
    """The row the others fold into: most issues, then oldest.

    Issue count first because that row's issues already carry file paths,
    statuses, and download history; moving fewer rows means fewer chances to
    lose something in a collision.
    """
    return sorted(rows, key=lambda s: (-len(s.issues), s.id))[0]


async def _merge_issue(db, winner: Issue, loser: Issue) -> None:
    """Fold ``loser`` into ``winner``, then delete it.

    The two rows are the same book seen through two providers, so the useful
    fields are unioned rather than overwritten: whichever row actually has the
    file wins on status/path, and each id space is preserved.
    """
    # Ids: adopt whatever the winner lacks. metron_id and comicvine_id are
    # UNIQUE, and SQLAlchemy emits both rows' UPDATEs in one flush — so the
    # loser must *release* the value before the winner can take it, otherwise
    # the two statements overlap and SQLite rejects the batch.
    loser_metron, loser_comicvine = loser.metron_id, loser.comicvine_id
    loser.metron_id = None
    loser.comicvine_id = None
    await db.flush()

    if winner.metron_id is None and loser_metron is not None:
        winner.metron_id = loser_metron
    if winner.comicvine_id is None and loser_comicvine is not None:
        winner.comicvine_id = loser_comicvine

    # A downloaded copy is the most valuable state either row can be in.
    if loser.status == "downloaded" and winner.status != "downloaded":
        winner.status = "downloaded"
    if winner.file_path is None and loser.file_path is not None:
        winner.file_path = loser.file_path

    for attr in ("title", "cover_date", "store_date", "cover_url", "description"):
        if getattr(winner, attr) is None and getattr(loser, attr) is not None:
            setattr(winner, attr, getattr(loser, attr))
    if winner.arcs_synced_at is None and loser.arcs_synced_at is not None:
        winner.arcs_synced_at = loser.arcs_synced_at

    # Arc links: (issue_id, story_arc_id) is a composite PK, so only move the
    # arcs the winner is not already linked to.
    winner_arc_ids = {
        r
        for r in (
            await db.execute(
                select(issue_story_arcs.c.story_arc_id).where(
                    issue_story_arcs.c.issue_id == winner.id
                )
            )
        )
        .scalars()
        .all()
    }
    loser_arc_ids = {
        r
        for r in (
            await db.execute(
                select(issue_story_arcs.c.story_arc_id).where(
                    issue_story_arcs.c.issue_id == loser.id
                )
            )
        )
        .scalars()
        .all()
    }
    for arc_id in loser_arc_ids - winner_arc_ids:
        await db.execute(
            update(issue_story_arcs)
            .where(
                issue_story_arcs.c.issue_id == loser.id,
                issue_story_arcs.c.story_arc_id == arc_id,
            )
            .values(issue_id=winner.id)
        )
    await db.execute(delete(issue_story_arcs).where(issue_story_arcs.c.issue_id == loser.id))

    # WeeklyRelease is unique on (issue_id, release_date) — drop the loser's
    # rows for dates the winner already covers, repoint the rest.
    winner_dates = {
        d
        for d in (
            await db.execute(
                select(WeeklyRelease.release_date).where(WeeklyRelease.issue_id == winner.id)
            )
        )
        .scalars()
        .all()
    }
    for wr in (
        (await db.execute(select(WeeklyRelease).where(WeeklyRelease.issue_id == loser.id)))
        .scalars()
        .all()
    ):
        if wr.release_date in winner_dates:
            await db.delete(wr)
        else:
            wr.issue_id = winner.id
            winner_dates.add(wr.release_date)

    # No uniqueness to worry about on these — just repoint.
    for model in (DownloadJob, ImportFile, FileIssue):
        await db.execute(update(model).where(model.issue_id == loser.id).values(issue_id=winner.id))

    # Core DELETE + expunge rather than db.delete(): an ORM delete walks the
    # relationship cascades, which for an Issue means loading download_jobs,
    # weekly_releases, and arcs to de-associate them. Those rows have already
    # been repointed above, and the lazy loads that walk would trigger blow up
    # under async. Expunge drops the row from the identity map so nothing
    # touches it afterwards.
    await db.flush()
    await db.execute(delete(Issue).where(Issue.id == loser.id))
    db.expunge(loser)


async def merge_series_group(db, series_ids: list[int]) -> MergeResult:
    """Merge the given Series rows into one. Does not commit."""
    if len(series_ids) < 2:
        raise ValueError("A merge needs at least two series")

    rows = list(
        (
            await db.execute(
                select(Series).where(Series.id.in_(series_ids)).options(selectinload(Series.issues))
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < 2:
        raise ValueError("Fewer than two of those series exist")

    winner = pick_winner(rows)
    losers = [s for s in rows if s.id != winner.id]
    result = MergeResult(kept_series_id=winner.id)

    # Index the winner's issues by normalized number so a loser issue can find
    # its counterpart. Sources spell the same issue "1", "01", "#1".
    by_number: dict[str, Issue] = {}
    for issue in winner.issues:
        by_number.setdefault(normalize_issue_number(issue.issue_number), issue)

    for loser in losers:
        # Union the identity across the rows. Both id columns are UNIQUE, so the
        # loser has to give the value up in its own flush before the winner can
        # claim it — see the same dance in _merge_issue.
        loser_metron, loser_comicvine = loser.metron_id, loser.comicvine_id
        loser.metron_id = None
        loser.comicvine_id = None
        await db.flush()

        if winner.metron_id is None and loser_metron is not None:
            winner.metron_id = loser_metron
        if winner.comicvine_id is None and loser_comicvine is not None:
            winner.comicvine_id = loser_comicvine
        # Subscription is an explicit user choice on either row — never drop it.
        winner.subscribed = winner.subscribed or loser.subscribed
        winner.auto_download = winner.auto_download or loser.auto_download
        for attr in ("publisher", "start_year", "cover_url", "description"):
            if getattr(winner, attr) is None and getattr(loser, attr) is not None:
                setattr(winner, attr, getattr(loser, attr))

        move_ids: list[int] = []
        for issue in list(loser.issues):
            key = normalize_issue_number(issue.issue_number)
            existing = by_number.get(key)
            if existing is None:
                move_ids.append(issue.id)
                by_number[key] = issue
                result.issues_moved += 1
            else:
                await _merge_issue(db, existing, issue)
                result.issues_merged += 1

        # Reparent with a Core UPDATE rather than by assigning to the ORM
        # relationship, and delete the shell the same way. An ORM delete of a
        # Series cascades "de-associate my children", setting issues.series_id
        # to NULL on a NOT NULL column for anything still in the loser's
        # in-memory collection. Going through Core keeps the ORM out of it, and
        # expunge drops the dead row from the identity map.
        if move_ids:
            await db.execute(
                update(Issue).where(Issue.id.in_(move_ids)).values(series_id=winner.id)
            )

        await db.flush()
        await db.execute(delete(Series).where(Series.id == loser.id))
        db.expunge(loser)
        result.removed_series_ids.append(loser.id)

    await db.flush()
    logger.info(
        "Merged series %s into %s (%d issues moved, %d merged)",
        result.removed_series_ids,
        winner.id,
        result.issues_moved,
        result.issues_merged,
    )
    return result
