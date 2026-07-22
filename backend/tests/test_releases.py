"""Tests for Phase 10: Weekly Releases API (step 10.1) and nightly calendar refresh (step 10.2)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pullbox.deps import get_metadata_provider
from pullbox.main import app

# ── Helpers ───────────────────────────────────────────────────────────────────


def _current_week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


async def _seed_release(
    release_date: date,
    series_title: str = "Test Series",
    publisher: str = "Test Publisher",
    issue_number: str = "1",
) -> tuple[int, int]:
    """Insert Series → Issue → WeeklyRelease directly via DB. Returns (wr_id, issue_id)."""
    import pullbox.database as db_module
    from pullbox.models import Issue, Series, WeeklyRelease

    cv_suffix = f"{series_title.replace(' ', '-')}-{release_date}-{issue_number}"
    async with db_module.AsyncSessionLocal() as db:
        series = Series(
            comicvine_id=f"cv-series-{cv_suffix}",
            title=series_title,
            publisher=publisher,
        )
        db.add(series)
        await db.flush()

        issue = Issue(
            series_id=series.id,
            comicvine_id=f"cv-issue-{cv_suffix}",
            issue_number=issue_number,
            status="unknown",
        )
        db.add(issue)
        await db.flush()

        wr = WeeklyRelease(issue_id=issue.id, release_date=release_date, source="comicvine")
        db.add(wr)
        await db.commit()
        return wr.id, issue.id


async def _seed_existing_series_and_issue() -> None:
    """Pre-insert a series and issue used by the calendar refresh duplicate tests.

    The issue is given store_date=2025-05-07 to match FAKE_RELEASES. Without a
    store_date the refresh would use week_monday (different per week), creating one
    WeeklyRelease per week instead of deduplicating to one.
    """
    import pullbox.database as db_module
    from pullbox.models import Issue, Series

    async with db_module.AsyncSessionLocal() as db:
        series = Series(comicvine_id="series-existing", title="Existing Series")
        db.add(series)
        await db.flush()

        issue = Issue(
            series_id=series.id,
            comicvine_id="issue-existing",
            issue_number="5",
            title="Existing Issue",
            store_date=date(2025, 5, 7),  # matches FAKE_RELEASES[2]["store_date"]
            status="wanted",
        )
        db.add(issue)
        await db.commit()


async def _count_rows() -> tuple[int, int, int]:
    """Return (series_count, issue_count, weekly_release_count)."""
    from sqlalchemy import func, select

    import pullbox.database as db_module
    from pullbox.models import Issue, Series, WeeklyRelease

    async with db_module.AsyncSessionLocal() as db:
        s = (await db.execute(select(func.count()).select_from(Series))).scalar()
        i = (await db.execute(select(func.count()).select_from(Issue))).scalar()
        wr = (await db.execute(select(func.count()).select_from(WeeklyRelease))).scalar()
        return s, i, wr


# Three fake releases: 2 for a brand-new series, 1 for an existing series/issue
FAKE_RELEASES = [
    {
        "comicvine_id": "issue-new-1",
        "issue_number": "1",
        "title": "First Issue",
        "store_date": "2025-05-07",
        "cover_url": None,
        "series": {"comicvine_id": "series-new-1", "title": "New Series One"},
    },
    {
        "comicvine_id": "issue-new-2",
        "issue_number": "2",
        "title": "Second Issue",
        "store_date": "2025-05-07",
        "cover_url": None,
        "series": {"comicvine_id": "series-new-1", "title": "New Series One"},
    },
    {
        "comicvine_id": "issue-existing",
        "issue_number": "5",
        "title": "Existing Issue",
        "store_date": "2025-05-07",
        "cover_url": None,
        "series": {"comicvine_id": "series-existing", "title": "Existing Series"},
    },
]


@pytest.fixture
def client():
    """TestClient whose metadata provider returns no live releases.

    The weekly-releases endpoint refreshes the current week from the provider when
    credentials are configured. Tests seed their own rows and assert on them, so we
    stub the provider to return an empty week — keeping the endpoint deterministic
    regardless of whether a real config.yaml has credentials.
    """
    stub = AsyncMock()
    stub.get_weekly_releases.return_value = []

    async def _override():
        yield stub

    app.dependency_overrides[get_metadata_provider] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)


# ── Step 10.1: GET /api/releases/weekly ──────────────────────────────────────


def test_weekly_releases_returns_current_week_by_default(client):
    """No ?week param → returns releases for the current ISO week."""
    monday = _current_week_monday()
    asyncio.run(_seed_release(monday))

    resp = client.get("/api/releases/weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["release_date"] == monday.isoformat()


def test_weekly_releases_filters_by_week_param(client):
    """?week=YYYY-WW returns only releases in that week, not other weeks."""
    # Week 2025-02 = Jan 6–12, 2025
    old_monday = date(2025, 1, 6)
    current_monday = _current_week_monday()

    asyncio.run(_seed_release(old_monday, "Old Series"))
    asyncio.run(_seed_release(current_monday, "Current Series"))

    resp = client.get("/api/releases/weekly?week=2025-02")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["series"]["title"] == "Old Series"


def test_weekly_releases_ordered_by_series_title_then_issue_number(client):
    """Releases within a week are sorted by series.title asc, issue_number asc."""
    monday = _current_week_monday()
    asyncio.run(_seed_release(monday, "Zorro Comics", issue_number="1"))
    asyncio.run(_seed_release(monday, "Amazing Comics", issue_number="1"))

    resp = client.get("/api/releases/weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    titles = [r["series"]["title"] for r in data]
    assert titles == sorted(titles)


def test_weekly_releases_empty_for_unpopulated_week(client):
    """Returns empty list when no releases exist for the requested week."""
    resp = client.get("/api/releases/weekly?week=2020-01")
    assert resp.status_code == 200
    assert resp.json() == []


def test_weekly_releases_invalid_week_returns_422(client):
    """Malformed week param returns HTTP 422."""
    resp = client.get("/api/releases/weekly?week=not-a-week")
    assert resp.status_code == 422


def test_weekly_releases_response_shape(client):
    """Response objects contain all required fields: release_date, pulled, issue, series."""
    monday = _current_week_monday()
    asyncio.run(_seed_release(monday))

    resp = client.get("/api/releases/weekly")
    assert resp.status_code == 200
    item = resp.json()[0]

    # Top-level fields
    assert "id" in item
    assert "release_date" in item
    assert "pulled" in item

    # Nested issue fields
    issue = item["issue"]
    assert "id" in issue
    assert "issue_number" in issue
    assert "status" in issue
    assert "cover_url" in issue

    # Nested series fields
    series = item["series"]
    assert "id" in series
    assert "title" in series
    assert "publisher" in series


# ── Step 10.2: nightly_calendar_refresh ──────────────────────────────────────


def test_calendar_refresh_creates_new_series_issues_and_releases(client):
    """Refresh creates Series, Issue, and WeeklyRelease rows from mock ComicVine data."""
    # Pre-insert existing series + issue (the third fake release points at these)
    asyncio.run(_seed_existing_series_and_issue())

    # Before: 1 series, 1 issue, 0 weekly releases
    s, i, wr = asyncio.run(_count_rows())
    assert s == 1
    assert i == 1
    assert wr == 0

    import pullbox.deps as deps_module

    deps_module._settings.comicvine_api_key = "fake-key"

    mock_cv = AsyncMock()
    mock_cv.get_weekly_releases.return_value = FAKE_RELEASES
    mock_cv.get_volume.return_value = {"publisher": "Test Publisher", "start_year": 2021}

    with patch("pullbox.deps.build_metadata_provider", return_value=mock_cv):
        from pullbox.scheduler import nightly_calendar_refresh

        asyncio.run(nightly_calendar_refresh())

    after_s, after_i, after_wr = asyncio.run(_count_rows())
    assert after_s == 2  # 1 new series (series-new-1)
    assert after_i == 3  # 2 new issues (issue-new-1, issue-new-2); issue-existing pre-existed
    assert after_wr == 3  # 1 WeeklyRelease per release item

    # The new series was enriched with a publisher from the per-volume lookup.
    assert asyncio.run(_series_publisher("series-new-1")) == ("Test Publisher", 2021)


def test_calendar_refresh_no_duplicates_on_rerun(client):
    """Running the refresh twice with the same mock data produces no duplicate rows."""
    import pullbox.deps as deps_module

    deps_module._settings.comicvine_api_key = "fake-key"

    mock_cv = AsyncMock()
    mock_cv.get_weekly_releases.return_value = FAKE_RELEASES
    mock_cv.get_volume.return_value = {"publisher": "Test Publisher", "start_year": 2021}

    with patch("pullbox.deps.build_metadata_provider", return_value=mock_cv):
        from pullbox.scheduler import nightly_calendar_refresh

        asyncio.run(nightly_calendar_refresh())
        asyncio.run(nightly_calendar_refresh())

    s, i, wr = asyncio.run(_count_rows())
    assert s == 2
    assert i == 3
    assert wr == 3  # no duplicates on second run


def test_calendar_refresh_preserves_existing_issue_status(client):
    """The refresh never overwrites status on an issue that already exists in the DB."""
    asyncio.run(_seed_existing_series_and_issue())  # issue-existing has status='wanted'

    import pullbox.deps as deps_module

    deps_module._settings.comicvine_api_key = "fake-key"

    mock_cv = AsyncMock()
    mock_cv.get_weekly_releases.return_value = [FAKE_RELEASES[2]]  # only the existing issue
    mock_cv.get_volume.return_value = {"publisher": "Test Publisher", "start_year": 2021}

    with patch("pullbox.deps.build_metadata_provider", return_value=mock_cv):
        from pullbox.scheduler import nightly_calendar_refresh

        asyncio.run(nightly_calendar_refresh())

    async def _get_status() -> str:
        from sqlalchemy import select

        import pullbox.database as db_module
        from pullbox.models import Issue

        async with db_module.AsyncSessionLocal() as db:
            result = await db.execute(select(Issue).where(Issue.comicvine_id == "issue-existing"))
            return result.scalar_one().status

    assert asyncio.run(_get_status()) == "wanted"


def test_calendar_refresh_skips_when_no_source(client):
    """Refresh exits early and never builds a provider when no source is configured."""
    import pullbox.deps as deps_module

    # Force an unconfigured state regardless of any real config.yaml credentials.
    deps_module._settings.metron_username = ""
    deps_module._settings.metron_password = ""
    deps_module._settings.comicvine_api_key = ""

    with patch("pullbox.deps.build_metadata_provider") as mock_build:
        from pullbox.scheduler import nightly_calendar_refresh

        asyncio.run(nightly_calendar_refresh())

    mock_build.assert_not_called()


# ── Publisher enrichment on the live page-load path (_refresh_week) ───────────


def _weekly_payload(series_cv_id: str = "vol-100") -> list[dict]:
    """One weekly release whose reduced volume object carries no publisher."""
    return [
        {
            "comicvine_id": "issue-enrich",
            "issue_number": "1",
            "title": "Issue X",
            "store_date": "2025-05-07",
            "cover_url": None,
            "series": {"comicvine_id": series_cv_id, "title": "Series X"},
        }
    ]


async def _series_publisher(series_cv_id: str) -> tuple[str | None, int | None]:
    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        s = (
            await db.execute(select(Series).where(Series.comicvine_id == series_cv_id))
        ).scalar_one()
        return s.publisher, s.start_year


def test_refresh_week_enriches_publisher_via_volume_lookup(client):
    """A new series gets its publisher (and start_year) from a per-volume lookup."""
    from datetime import date

    import pullbox.database as db_module
    from pullbox.routers.releases import _refresh_week

    fake_cv = AsyncMock()
    fake_cv.get_weekly_releases.return_value = _weekly_payload()
    fake_cv.get_volume.return_value = {"publisher": "Marvel Comics", "start_year": 2020}

    async def _run():
        async with db_module.AsyncSessionLocal() as db:
            await _refresh_week(db, fake_cv, date(2025, 5, 5), date(2025, 5, 11))
            await db.commit()

    asyncio.run(_run())

    assert asyncio.run(_series_publisher("vol-100")) == ("Marvel Comics", 2020)
    fake_cv.get_volume.assert_awaited_once_with(metron_id=None, comicvine_id="vol-100")


def test_refresh_week_skips_lookup_when_publisher_already_known(client):
    """A series that already has a publisher is not re-fetched (steady-state = 0 lookups)."""
    from datetime import date

    import pullbox.database as db_module
    from pullbox.models import Series
    from pullbox.routers.releases import _refresh_week

    async def _seed():
        async with db_module.AsyncSessionLocal() as db:
            db.add(Series(comicvine_id="vol-100", title="Series X", publisher="Image"))
            await db.commit()

    asyncio.run(_seed())

    fake_cv = AsyncMock()
    fake_cv.get_weekly_releases.return_value = _weekly_payload()

    async def _run():
        async with db_module.AsyncSessionLocal() as db:
            await _refresh_week(db, fake_cv, date(2025, 5, 5), date(2025, 5, 11))
            await db.commit()

    asyncio.run(_run())

    assert asyncio.run(_series_publisher("vol-100")) == ("Image", None)
    fake_cv.get_volume.assert_not_awaited()


def test_refresh_week_survives_volume_lookup_failure(client):
    """A failed (or rate-limited) volume lookup leaves publisher None without aborting."""
    from datetime import date

    import pullbox.database as db_module
    from pullbox.clients.comicvine import ComicVineRateLimitError
    from pullbox.routers.releases import _refresh_week

    fake_cv = AsyncMock()
    fake_cv.get_weekly_releases.return_value = _weekly_payload()
    fake_cv.get_volume.side_effect = ComicVineRateLimitError("budget exhausted")

    async def _run():
        async with db_module.AsyncSessionLocal() as db:
            await _refresh_week(db, fake_cv, date(2025, 5, 5), date(2025, 5, 11))
            await db.commit()

    asyncio.run(_run())  # must not raise

    # The release still landed; the series just has no publisher yet.
    assert asyncio.run(_series_publisher("vol-100")) == (None, None)
