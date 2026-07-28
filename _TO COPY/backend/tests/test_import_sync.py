"""Tests for the deferred library-import sync: rate limiter + backlog sync."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from pullbox.clients.comicvine import ComicVineClient, ComicVineRateLimitError, _RateLimiter


@pytest.fixture
async def db_session():
    """Lock-free session with tables created directly (no TestClient/scheduler)."""
    import pullbox.database as db_module
    from pullbox.config import Settings
    from pullbox.models import Base

    settings = Settings()
    db_module.init_db(settings)
    async with db_module._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_module.AsyncSessionLocal() as session:
        yield session
    await db_module._engine.dispose()
    db_module._engine = None
    db_module.AsyncSessionLocal = None


# ── Rate limiter ──────────────────────────────────────────────────────────────


async def test_rate_limiter_hourly_cap():
    lim = _RateLimiter(min_interval=0.0, per_hour=2)
    await lim.acquire()
    await lim.acquire()
    with pytest.raises(ComicVineRateLimitError):
        await lim.acquire()


async def test_rate_limiter_min_interval_spacing():
    lim = _RateLimiter(min_interval=0.2, per_hour=100)
    # Time across both acquisitions: the first is immediate, the second must wait
    # ~min_interval. Assert a tolerant lower bound (Windows asyncio timers can fire
    # a few ms early) that still clearly distinguishes spaced from unspaced (~0s).
    start = time.monotonic()
    await lim.acquire()
    await lim.acquire()
    assert time.monotonic() - start >= 0.15


async def test_rate_limiter_cooldown_after_throttle():
    lim = _RateLimiter(min_interval=0.0, per_hour=100)
    lim.note_throttle()
    with pytest.raises(ComicVineRateLimitError):
        await lim.acquire()


class _JsonTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int, payload: dict) -> None:
        self._status = status_code
        self._payload = payload

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self._status, json=self._payload)


async def test_client_raises_rate_limit_on_status_107():
    payload = {"status_code": 107, "error": "Rate Limit Exceeded", "results": {}}
    transport = _JsonTransport(200, payload)
    client = ComicVineClient("key", "https://fake.comicvine.test", transport=transport)
    try:
        with pytest.raises(ComicVineRateLimitError):
            await client.get_volume("500")
    finally:
        await client.close()


async def test_client_raises_rate_limit_on_http_429():
    transport = _JsonTransport(429, {})
    client = ComicVineClient("key", "https://fake.comicvine.test", transport=transport)
    try:
        with pytest.raises(ComicVineRateLimitError):
            await client.get_issues("500")
    finally:
        await client.close()


# ── Match picking ─────────────────────────────────────────────────────────────


def test_pick_best_match_prefers_title_and_year():
    from pullbox.services.import_sync import pick_best_match

    results = [
        {"comicvine_id": "1", "title": "Batman", "start_year": 1940},
        {"comicvine_id": "2", "title": "Batman", "start_year": 2016},
        {"comicvine_id": "3", "title": "Batman: Year One", "start_year": 1987},
    ]
    assert pick_best_match(results, "Batman", 2016)["comicvine_id"] == "2"
    # No year → first exact title match.
    assert pick_best_match(results, "batman", None)["comicvine_id"] == "1"
    # No exact title → first result as fallback.
    assert pick_best_match(results, "Nightwing", None)["comicvine_id"] == "1"
    assert pick_best_match([], "Batman", 2016) is None


# ── Backfill ComicVine metadata for one imported series ───────────────────────


async def _seed_import(db, title, year, issue_numbers):
    """Create an import-origin Series + owned Issues + pending tracking rows."""
    from pullbox.models import ImportFile, Issue, Series

    series = Series(comicvine_id=None, title=title, start_year=year, subscribed=True)
    db.add(series)
    await db.flush()
    rows = []
    for n in issue_numbers:
        issue = Issue(
            series_id=series.id, comicvine_id=None, issue_number=n,
            status="downloaded", file_path=f"/c/{title}-{n}.cbz",
        )
        db.add(issue)
        await db.flush()
        row = ImportFile(issue_id=issue.id, series_id=series.id, status="pending")
        db.add(row)
        rows.append(row)
    await db.flush()
    return series, rows


async def test_backfill_enriches_owned_issues_and_stamps_synced(db_session):
    from pullbox.models import ImportFile, Issue
    from pullbox.services.import_sync import resolve_series_for_import

    series, rows = await _seed_import(db_session, "Batman", 2016, ["001", "99"])

    cv = AsyncMock()
    cv.search_series.return_value = [
        {"comicvine_id": "500", "title": "Batman", "publisher": "DC", "start_year": 2016,
         "cover_url": None, "description": None, "issue_count": 2},
    ]
    cv.get_issues.return_value = [
        {"comicvine_id": "9001", "issue_number": "1", "title": "One", "cover_date": None,
         "store_date": None, "cover_url": None, "description": None},
        {"comicvine_id": "9002", "issue_number": "2", "title": "Two", "cover_date": None,
         "store_date": None, "cover_url": None, "description": None},
    ]

    synced, unmatched, no_match = await resolve_series_for_import(db_session, cv, series, rows)
    assert (synced, unmatched, no_match) == (1, 1, 0)

    # Series adopted the ComicVine match; still subscribed.
    assert series.comicvine_id == "500"
    assert series.publisher == "DC"
    assert series.subscribed is True

    issues = (
        await db_session.execute(select(Issue).where(Issue.series_id == series.id))
    ).scalars().all()
    by_num = {i.issue_number: i for i in issues}
    # Owned #001 was enriched in place (kept its filename number, downloaded, file path).
    assert by_num["001"].comicvine_id == "9001"
    assert by_num["001"].title == "One"
    assert by_num["001"].status == "downloaded"
    assert by_num["001"].file_path == "/c/Batman-001.cbz"
    # #99 has no counterpart in the volume → stays un-enriched.
    assert by_num["99"].comicvine_id is None

    # No extra Issue rows created for the rest of the volume (owned-only).
    assert len(issues) == 2

    tracking = (await db_session.execute(select(ImportFile))).scalars().all()
    assert sorted(t.status for t in tracking) == ["synced", "unmatched"]
    assert all(t.synced_at is not None for t in tracking)  # terminal, not retried

    # Arc enrichment must not run during import backfill (rate limits).
    assert cv.get_issue.await_count == 0


async def test_backfill_merges_into_existing_series_on_duplicate_cv_id(db_session):
    """When the matched volume already exists as another series, merge into it
    instead of crashing on series.comicvine_id's UNIQUE constraint."""
    from pullbox.models import ImportFile, Issue, Series
    from pullbox.services.import_sync import resolve_series_for_import

    # Existing series (e.g. from the pull list) already owns CV volume 167340,
    # with issue #1 already carrying its CV id.
    existing = Series(comicvine_id="167340", title="Absolute Batman", start_year=2025)
    db_session.add(existing)
    await db_session.flush()
    db_session.add(
        Issue(series_id=existing.id, comicvine_id="9001", issue_number="1", status="wanted")
    )
    await db_session.flush()

    # Import-origin duplicate of the same book, with two owned files.
    src, rows = await _seed_import(db_session, "Absolute Batman", 2025, ["1", "2"])
    src_id = src.id

    cv = AsyncMock()
    cv.search_series.return_value = [
        {"comicvine_id": "167340", "title": "Absolute Batman", "publisher": "DC Comics",
         "start_year": 2025, "cover_url": None, "description": None, "issue_count": 2},
    ]
    cv.get_issues.return_value = [
        {"comicvine_id": "9001", "issue_number": "1", "title": "One", "cover_date": None,
         "store_date": None, "cover_url": None, "description": None},
        {"comicvine_id": "9002", "issue_number": "2", "title": "Two", "cover_date": None,
         "store_date": None, "cover_url": None, "description": None},
    ]

    synced, unmatched, no_match = await resolve_series_for_import(db_session, cv, src, rows)
    # Both owned issues matched a remote issue → synced; nothing crashed.
    assert (synced, unmatched, no_match) == (2, 0, 0)

    # The import-origin series was consolidated away.
    assert (await db_session.get(Series, src_id)) is None

    # Both imported issues now live under the existing series, with files intact.
    moved = (
        await db_session.execute(select(Issue).where(Issue.series_id == existing.id))
    ).scalars().all()
    by_num = {i.issue_number: i for i in moved}
    assert by_num["1"].file_path == "/c/Absolute Batman-1.cbz"
    assert by_num["2"].file_path == "/c/Absolute Batman-2.cbz"
    # #2's CV id was free → assigned; #1's (9001) was already taken → left null, no clash.
    assert by_num["2"].comicvine_id == "9002"
    assert by_num["1"].comicvine_id is None

    # Tracking rows are terminal and now point at the surviving series.
    tracking = (await db_session.execute(select(ImportFile))).scalars().all()
    assert all(t.status == "synced" and t.series_id == existing.id for t in tracking)


async def test_backfill_no_comicvine_match_marks_no_match(db_session):
    from pullbox.models import ImportFile
    from pullbox.services.import_sync import resolve_series_for_import

    series, rows = await _seed_import(db_session, "Totally Made Up Title", None, ["1"])

    cv = AsyncMock()
    cv.search_series.return_value = []

    synced, unmatched, no_match = await resolve_series_for_import(db_session, cv, series, rows)
    assert (synced, unmatched, no_match) == (0, 0, 1)

    tracking = (await db_session.execute(select(ImportFile))).scalars().all()
    assert tracking[0].status == "no_match"
    assert tracking[0].synced_at is not None  # terminal, not retried
    assert series.comicvine_id is None
    cv.get_issues.assert_not_awaited()
