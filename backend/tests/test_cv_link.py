"""Tests for ComicVine id recovery (services/cv_link.py).

The matcher is the part worth testing hard: ``comicvine_id`` is what later
refreshes match on, so a wrong id binds a series to another book's metadata.
Every case here is one where guessing would be wrong.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from pullbox.services.cv_link import pick_volume_match, resolve_series_cv_ids


def _vol(cv_id: str, title: str, start_year: int | None) -> dict:
    return {"comicvine_id": cv_id, "title": title, "start_year": start_year}


# ── Matcher ───────────────────────────────────────────────────────────────────


def test_matches_on_title_and_year():
    """The real case: three volumes share a title, the year picks the right one."""
    results = [
        _vol("176990", "Archie's Halloween Spectacular", 2026),
        _vol("114232", "Archie's Halloween Spectacular", 2018),
        _vol("167315", "Archie's Halloween Spectacular", 2025),
    ]
    assert pick_volume_match("Archie's Halloween Spectacular", 2026, results) == "176990"


def test_matches_across_punctuation_differences():
    """Sources punctuate differently; the normalized title bridges that."""
    results = [_vol("1", "Batman/Superman: World's Finest", 2022)]
    assert pick_volume_match("Batman / Superman: Worlds Finest", 2022, results) == "1"


def test_tolerates_one_year_of_drift():
    """A December launch is dated next year by one source and not the other."""
    results = [_vol("5", "Some Book", 2025)]
    assert pick_volume_match("Some Book", 2024, results) == "5"


def test_rejects_year_beyond_tolerance():
    results = [_vol("5", "Some Book", 2019)]
    assert pick_volume_match("Some Book", 2026, results) is None


def test_rejects_ambiguous_title_when_year_unknown():
    """Without a year, three same-titled volumes are a coin flip — link nothing."""
    results = [
        _vol("176990", "Archie's Halloween Spectacular", 2026),
        _vol("114232", "Archie's Halloween Spectacular", 2018),
    ]
    assert pick_volume_match("Archie's Halloween Spectacular", None, results) is None


def test_accepts_sole_candidate_when_year_unknown():
    results = [_vol("7", "Uniquely Named Book", 2020)]
    assert pick_volume_match("Uniquely Named Book", None, results) == "7"


def test_rejects_tie_on_equal_year_distance():
    """Two volumes of one name in the same year cannot be told apart from here."""
    results = [_vol("1", "Twin Book", 2024), _vol("2", "Twin Book", 2024)]
    assert pick_volume_match("Twin Book", 2024, results) is None


def test_ignores_near_miss_titles():
    """ComicVine search returns related books; only an exact title counts."""
    results = [
        _vol("106847", "Archie's Christmas Spectacular", 2026),
        _vol("999", "Archie's Halloween Spectacular Annual", 2026),
    ]
    assert pick_volume_match("Archie's Halloween Spectacular", 2026, results) is None


def test_matches_ampersand_against_spelled_out_and():
    """Real disagreement: ComicVine "Black & White" vs Metron "Black and White"."""
    results = [_vol("174201", "Do a Powerbomb Black & White", 2026)]
    assert pick_volume_match("Do a Powerbomb Black and White", 2026, results) == "174201"


def test_handles_approximate_year_strings():
    """ComicVine really returns "1950?" for older books; it must not abort the sweep."""
    results = [_vol("3", "Old Book", "1950?"), _vol("4", "Old Book", "2024")]
    assert pick_volume_match("Old Book", 1950, results) == "3"


def test_unparseable_year_is_treated_as_unknown():
    results = [_vol("3", "Odd Book", "n/a")]
    assert pick_volume_match("Odd Book", 2024, results) == "3"  # sole candidate


def test_no_results_is_no_match():
    assert pick_volume_match("Nothing Here", 2020, []) is None


# ── Sweep ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session(tmp_path):
    """A real SQLite session factory — the sweep's uniqueness check needs a DB."""
    import pullbox.database as db_module
    from pullbox.config import Settings

    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    db_module.init_db(settings)

    async def _create():
        from pullbox.models import Base

        engine = db_module.get_engine(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return db_module.AsyncSessionLocal


def _add_series(factory, **kwargs):
    from pullbox.models import Series

    async def _run():
        async with factory() as db:
            s = Series(**kwargs)
            db.add(s)
            await db.commit()
            return s.id

    return asyncio.run(_run())


def _read(factory, series_id):
    from pullbox.models import Series

    async def _run():
        async with factory() as db:
            s = await db.get(Series, series_id)
            return s.comicvine_id, s.cv_lookup_at

    return asyncio.run(_run())


def test_sweep_links_series_and_stamps_attempt(db_session):
    sid = _add_series(db_session, metron_id="m1", title="Vampyrates!", start_year=2026)
    cv = AsyncMock()
    cv.search_series.return_value = [_vol("174215", "Vampyrates!", 2026)]

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats == {"checked": 1, "resolved": 1, "candidates": 1}
    comicvine_id, stamped = _read(db_session, sid)
    assert comicvine_id == "174215"
    assert stamped is not None


def test_sweep_stamps_even_when_unmatched(db_session):
    """An unmatchable series must not be re-searched on every sweep forever."""
    sid = _add_series(db_session, metron_id="m1", title="Obscure Zine", start_year=2026)
    cv = AsyncMock()
    cv.search_series.return_value = [_vol("1", "Something Else", 2026)]

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["resolved"] == 0
    comicvine_id, stamped = _read(db_session, sid)
    assert comicvine_id is None
    assert stamped is not None


def test_sweep_does_not_steal_an_id_another_series_holds(db_session):
    """comicvine_id is UNIQUE — a collision is a duplicate pair, not a link."""
    _add_series(db_session, comicvine_id="174215", title="Vampyrates! (2026)")
    sid = _add_series(db_session, metron_id="m1", title="Vampyrates!", start_year=2026)
    cv = AsyncMock()
    cv.search_series.return_value = [_vol("174215", "Vampyrates!", 2026)]

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["resolved"] == 0
    comicvine_id, stamped = _read(db_session, sid)
    assert comicvine_id is None
    assert stamped is not None  # attempted, so it drops out of the queue


def test_sweep_stops_cleanly_on_rate_limit(db_session):
    """Budget exhaustion leaves the remainder unstamped, so they retry first."""
    from pullbox.clients.comicvine import ComicVineRateLimitError

    sid = _add_series(db_session, metron_id="m1", title="Some Book", start_year=2026)
    cv = AsyncMock()
    cv.search_series.side_effect = ComicVineRateLimitError("budget exhausted")

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["checked"] == 0
    assert _read(db_session, sid) == (None, None)


def test_sweep_skips_series_that_already_have_an_id(db_session):
    _add_series(db_session, comicvine_id="123", title="Already Linked")
    cv = AsyncMock()

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["candidates"] == 0
    cv.search_series.assert_not_awaited()


def test_only_unattempted_skips_previously_tried_series(db_session):
    """Request paths must not re-search a settled miss — that is how browsing
    weeks back and forth would burn the whole hourly ComicVine budget."""
    from datetime import datetime

    _add_series(
        db_session,
        metron_id="m1",
        title="Tried Already",
        start_year=2026,
        cv_lookup_at=datetime.utcnow(),
    )
    cv = AsyncMock()

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv, only_unattempted=True))

    assert stats["candidates"] == 0
    cv.search_series.assert_not_awaited()


def test_sweep_retries_a_stale_miss(db_session):
    """A book announced before ComicVine catalogued it deserves a second look."""
    from datetime import datetime, timedelta

    sid = _add_series(
        db_session,
        metron_id="m1",
        title="Late Arrival",
        start_year=2026,
        cv_lookup_at=datetime.utcnow() - timedelta(days=30),
    )
    cv = AsyncMock()
    cv.search_series.return_value = [_vol("555", "Late Arrival", 2026)]

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["resolved"] == 1
    assert _read(db_session, sid)[0] == "555"


def test_sweep_leaves_a_recent_miss_alone(db_session):
    """Within the retry window the background sweep skips it too."""
    from datetime import datetime, timedelta

    _add_series(
        db_session,
        metron_id="m1",
        title="Recent Miss",
        start_year=2026,
        cv_lookup_at=datetime.utcnow() - timedelta(days=1),
    )
    cv = AsyncMock()

    stats = asyncio.run(resolve_series_cv_ids(db_session, cv))

    assert stats["candidates"] == 0
    cv.search_series.assert_not_awaited()


def test_sweep_is_a_noop_without_comicvine_configured(db_session):
    _add_series(db_session, metron_id="m1", title="Some Book", start_year=2026)
    assert asyncio.run(resolve_series_cv_ids(db_session, None)) == {
        "checked": 0,
        "resolved": 0,
        "candidates": 0,
    }
