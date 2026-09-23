"""Recover a ComicVine volume id for series that Metron has no ``cv_id`` for.

Metron is the primary metadata source, and it carries a ComicVine cross-reference
in ``cv_id`` — but that field is null for a large share of series (every
``Archie's Halloween Spectacular`` volume, for instance). Those rows end up with
``Series.comicvine_id = NULL`` no matter how many times they are refreshed,
because there is no id at the source to copy. The only way back to a ComicVine
id is to search ComicVine for the title and match the result ourselves.

That is a guess, so it is a deliberately conservative one: the normalized titles
must be equal, and the start years must agree. A wrong id here does not just
produce a broken link — ``comicvine_id`` is what later refreshes match on, so it
would silently bind this series to another book's metadata. When in doubt the
series is left unlinked and the pull list falls back to a ComicVine search URL.

Each attempt stamps ``Series.cv_lookup_at`` whether or not it matched, so an
unmatchable series costs one search ever rather than one per sweep.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from pullbox.clients.metadata import PROVIDER_ERRORS, RATE_LIMIT_ERRORS
from pullbox.models import Series
from pullbox.services.dedupe import normalize_title

logger = logging.getLogger(__name__)

# ComicVine's start_year and Metron's year_began disagree by one on books that
# ship in December under the next year's cover date, so one year of slack is
# allowed. Two would start pulling in genuinely adjacent volumes of annuals and
# seasonal specials, which is exactly the class of title this feature exists for.
_YEAR_TOLERANCE = 1

_LEADING_YEAR = re.compile(r"\d{4}")

# How long before a failed lookup is worth retrying. Comics are solicited months
# ahead of ComicVine cataloguing them, so a miss on an announced-but-unlisted
# book is often temporary — but retrying on every sweep would spend the whole
# hourly budget re-asking the same questions. The page-load path never retries
# (see ``only_unattempted``); only the background sweep does, this slowly.
_RETRY_AFTER = timedelta(days=14)


def _match_key(title: str | None) -> str:
    """Comparison key for title matching, slightly looser than ``normalize_title``.

    ``normalize_title`` drops "&" entirely, so "Black & White" and "Black and
    White" fold to different keys — and the two sources really do disagree that
    way (ComicVine lists "Do a Powerbomb Black & White" where Metron has "…Black
    and White"). Expanding the ampersand first makes them agree. This is not
    folded back into ``normalize_title`` because that feeds the stored
    ``norm_title`` column, and changing it would need a backfill of every row.
    """
    return normalize_title((title or "").replace("&", " and "))


def _year(value: object) -> int | None:
    """Best-effort year from a provider field.

    ComicVine returns ``start_year`` as a string, and for older books an
    approximate one — "1950?" is real data. Take the first four-digit run and
    treat anything else as unknown rather than letting it abort the sweep.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = _LEADING_YEAR.search(str(value))
    return int(match.group()) if match else None


def pick_volume_match(title: str, start_year: int | None, results: list[dict]) -> str | None:
    """The ComicVine id for ``title``, or None when no candidate is safe.

    Requires an exact normalized-title match. Among those, the year decides:
    with a known ``start_year`` the candidate's must be within
    ``_YEAR_TOLERANCE``, and the closest one wins. Without a year, a match is
    only returned if exactly one candidate shares the title — "Archie's
    Halloween Spectacular" has three ComicVine volumes, and picking one by
    search rank would be a coin flip.
    """
    key = _match_key(title)
    if not key:
        return None

    candidates = [r for r in results if _match_key(r.get("title")) == key]
    if not candidates:
        return None

    if start_year is None:
        return str(candidates[0]["comicvine_id"]) if len(candidates) == 1 else None

    dated = [
        (abs(year - start_year), r)
        for r in candidates
        if (year := _year(r.get("start_year"))) is not None
    ]
    within = [(delta, r) for delta, r in dated if delta <= _YEAR_TOLERANCE]
    if not within:
        # A single candidate with no usable year is still the only thing it can be.
        undated = [r for r in candidates if _year(r.get("start_year")) is None]
        return str(undated[0]["comicvine_id"]) if len(undated) == 1 else None

    within.sort(key=lambda pair: pair[0])
    # An exact-year tie between two ComicVine volumes of the same name is not
    # resolvable from what we hold; leave it rather than guess.
    if len(within) > 1 and within[0][0] == within[1][0]:
        return None
    return str(within[0][1]["comicvine_id"])


async def _claim_id(db, series: Series, comicvine_id: str) -> bool:
    """Write the id unless another series already owns it (the column is UNIQUE)."""
    taken = (
        await db.execute(
            select(Series.id).where(Series.comicvine_id == comicvine_id, Series.id != series.id)
        )
    ).scalar_one_or_none()
    if taken is not None:
        logger.info(
            "cv_link: %s resolved to %s, already held by series %s — left unlinked "
            "(likely a duplicate pair; see Settings → Duplicate Series)",
            series.title,
            comicvine_id,
            taken,
        )
        return False
    series.comicvine_id = comicvine_id
    return True


async def resolve_series_cv_ids(
    session_factory,
    comicvine,
    *,
    series_ids: list[int] | None = None,
    limit: int = 25,
    only_unattempted: bool = False,
) -> dict:
    """Search ComicVine for the id of up to ``limit`` unlinked series.

    ``comicvine`` must be the ComicVine source itself, not the composite
    provider: the composite tries Metron first and would answer the search with
    Metron records, which carry no ComicVine id at all — the very gap this is
    filling. Use ``CompositeProvider.comicvine_source``.

    Network calls happen outside any write transaction, and each result is
    committed on its own: a ComicVine search takes real wall-clock time, and
    holding a SQLite write lock across it wedges the whole process (the
    scheduler included). Reads, then HTTP, then a short write per series.

    ``series_ids`` restricts the sweep to specific rows — that is how the series
    on the week being viewed get resolved now instead of waiting for the sweep.

    ``only_unattempted`` skips every series already tried, at any age. Request
    paths must set it: they fire on each page load, and without it browsing back
    and forth over a few weeks would re-search the same unmatchable series until
    the hourly budget was gone.
    """
    if comicvine is None:
        return {"checked": 0, "resolved": 0, "candidates": 0}

    async with session_factory() as db:
        query = select(Series).where(Series.comicvine_id.is_(None))
        if series_ids is not None:
            query = query.where(Series.id.in_(series_ids))
        if only_unattempted:
            query = query.where(Series.cv_lookup_at.is_(None))
        else:
            query = query.where(
                or_(
                    Series.cv_lookup_at.is_(None),
                    Series.cv_lookup_at < datetime.utcnow() - _RETRY_AFTER,
                )
            )
        # Never-attempted rows first, then least-recently attempted: a sweep that
        # keeps re-reading the same failures makes no progress.
        query = query.order_by(Series.cv_lookup_at.is_not(None), Series.cv_lookup_at).limit(limit)
        pending = [(s.id, s.title, s.start_year) for s in (await db.execute(query)).scalars().all()]

    resolved = attempted = 0
    for series_id, title, start_year in pending:
        try:
            results = await comicvine.search_series(title, limit=10)
        except RATE_LIMIT_ERRORS:
            # Out of budget: stop cleanly and leave the rest for the next sweep.
            # Nothing is stamped, so they stay at the front of the queue.
            logger.info("cv_link: rate limited after %d lookup(s), stopping", attempted)
            break
        except PROVIDER_ERRORS:
            logger.debug("cv_link: search failed for %s", title, exc_info=True)
            continue

        attempted += 1
        match = pick_volume_match(title, start_year, results)

        async with session_factory() as db:
            series = await db.get(Series, series_id)
            if series is None or series.comicvine_id is not None:
                continue  # merged or resolved while we were on the network
            series.cv_lookup_at = datetime.utcnow()
            if match is not None and await _claim_id(db, series, match):
                resolved += 1
                logger.info("cv_link: %s → ComicVine %s", title, match)
            await db.commit()

    return {"checked": attempted, "resolved": resolved, "candidates": len(pending)}
