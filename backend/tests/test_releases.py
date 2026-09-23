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
    subscribed: bool = False,
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
            subscribed=subscribed,
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


def test_weekly_releases_cached_skips_provider():
    """?cached=true serves DB rows without calling the provider, even when configured.

    The pull list renders this read immediately while the live refresh runs
    alongside it, so it must never block on provider HTTP.
    """
    from pullbox.config import Settings
    from pullbox.deps import get_settings

    monday = _current_week_monday()
    stub = _client_returning([])
    app.dependency_overrides[get_settings] = lambda: Settings(comicvine_api_key="test-key")
    try:
        with TestClient(app) as c:
            asyncio.run(_seed_release(monday))
            cached = c.get("/api/releases/weekly?cached=true")
            assert stub.get_weekly_releases.await_count == 0

            live = c.get("/api/releases/weekly")
            assert stub.get_weekly_releases.await_count == 1
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)
        app.dependency_overrides.pop(get_settings, None)

    assert cached.status_code == 200
    assert [r["id"] for r in cached.json()] == [r["id"] for r in live.json()]
    assert len(cached.json()) == 1


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
    assert "subscribed" in series


def test_weekly_release_reports_subscription_state(client):
    """The pull list swaps 'Add to Pullbox' for 'Go to Series' off this flag, so it
    must reflect the Series row rather than defaulting to false."""
    monday = _current_week_monday()
    asyncio.run(_seed_release(monday, series_title="Followed", subscribed=True))
    asyncio.run(_seed_release(monday, series_title="Unfollowed", subscribed=False))

    resp = client.get("/api/releases/weekly")
    assert resp.status_code == 200
    by_title = {r["series"]["title"]: r["series"]["subscribed"] for r in resp.json()}

    assert by_title["Followed"] is True
    assert by_title["Unfollowed"] is False


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


def _metron_weekly_payload() -> list[dict]:
    """A Metron weekly release: series carries metron_id only (list rows omit cv_id)."""
    return [
        {
            "metron_id": "m-issue-1",
            "issue_number": "1",
            "title": "Issue X",
            "store_date": "2025-05-07",
            "cover_url": None,
            "series": {"metron_id": "m-vol-1", "comicvine_id": None, "title": "Series X"},
        }
    ]


async def _series_ids(metron_id: str) -> tuple[str | None, str | None]:
    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        s = (await db.execute(select(Series).where(Series.metron_id == metron_id))).scalar_one()
        return s.metron_id, s.comicvine_id


def test_refresh_week_adopts_comicvine_id_from_volume_detail(client):
    """A Metron-only series picks up cv_id from the detail lookup that fills publisher."""
    from datetime import date

    import pullbox.database as db_module
    from pullbox.routers.releases import _refresh_week

    fake = AsyncMock()
    fake.get_weekly_releases.return_value = _metron_weekly_payload()
    fake.get_volume.return_value = {
        "metron_id": "m-vol-1",
        "comicvine_id": "cv-999",
        "publisher": "Boom! Studios",
        "start_year": 2024,
    }

    async def _run():
        async with db_module.AsyncSessionLocal() as db:
            await _refresh_week(db, fake, date(2025, 5, 5), date(2025, 5, 11))
            await db.commit()

    asyncio.run(_run())

    assert asyncio.run(_series_ids("m-vol-1")) == ("m-vol-1", "cv-999")


def test_refresh_week_does_not_steal_comicvine_id_held_by_another_series(client):
    """If another row already owns that cv_id, leave it — that is a merge for Settings."""
    from datetime import date

    import pullbox.database as db_module
    from pullbox.models import Series
    from pullbox.routers.releases import _refresh_week

    async def _seed():
        async with db_module.AsyncSessionLocal() as db:
            # Deliberately a different title so the norm_title bridge does not
            # match it and the weekly row is created as a separate series.
            db.add(Series(comicvine_id="cv-999", title="Series X (2019)", publisher="Boom"))
            await db.commit()

    asyncio.run(_seed())

    fake = AsyncMock()
    fake.get_weekly_releases.return_value = _metron_weekly_payload()
    fake.get_volume.return_value = {
        "metron_id": "m-vol-1",
        "comicvine_id": "cv-999",
        "publisher": "Boom! Studios",
        "start_year": 2024,
    }

    async def _run():
        async with db_module.AsyncSessionLocal() as db:
            await _refresh_week(db, fake, date(2025, 5, 5), date(2025, 5, 11))
            await db.commit()

    asyncio.run(_run())  # must not raise a UNIQUE violation

    assert asyncio.run(_series_ids("m-vol-1")) == ("m-vol-1", None)


async def _seed_unlinked_release(release_date: date, title: str) -> int:
    """A Metron-sourced release: series has metron_id but no comicvine_id."""
    import pullbox.database as db_module
    from pullbox.models import Issue, Series, WeeklyRelease

    async with db_module.AsyncSessionLocal() as db:
        series = Series(metron_id=f"m-{title}", title=title, publisher="Boom!")
        db.add(series)
        await db.flush()
        issue = Issue(
            series_id=series.id, metron_id=f"mi-{title}", issue_number="1", status="unknown"
        )
        db.add(issue)
        await db.flush()
        db.add(WeeklyRelease(issue_id=issue.id, release_date=release_date, source="metron"))
        await db.commit()
        return series.id


def _dispatched_for(client, week: str) -> list[list[int]]:
    """Run the endpoint with _resolve_links stubbed, returning its call args."""
    from pullbox.routers import releases as releases_module

    calls: list[list[int]] = []

    async def _fake_resolve(settings, series_ids):
        calls.append(sorted(series_ids))

    releases_module._resolving.clear()
    with patch.object(releases_module, "_resolve_links", _fake_resolve):
        resp = client.get(f"/api/releases/weekly?week={week}")
    assert resp.status_code == 200
    return calls


def test_weekly_releases_dispatches_link_resolution_for_unlinked_series(client, monkeypatch):
    """The pull list resolves every unlinked series on the week it serves.

    Not just the ones this refresh created: a series added by an earlier refresh
    is still unlinked, and waiting for the background sweep to come round means
    the week the user is looking at right now has no links on it.
    """
    import pullbox.deps as deps_module
    from pullbox.routers import releases as releases_module

    monday = date(2025, 5, 5)
    unlinked_id = asyncio.run(_seed_unlinked_release(monday, "Unlinked Book"))
    asyncio.run(_seed_release(monday, series_title="Linked Book"))

    settings = deps_module.get_settings()
    monkeypatch.setattr(settings, "comicvine_api_key", "test-key", raising=False)

    calls = _dispatched_for(client, "2025-19")
    releases_module._resolving.clear()

    assert calls == [[unlinked_id]], "only the unlinked series should be looked up"


def test_weekly_releases_does_not_redispatch_while_in_flight(client, monkeypatch):
    """Two quick loads of the same week must not pay for the same searches twice."""
    import pullbox.deps as deps_module
    from pullbox.routers import releases as releases_module

    monday = date(2025, 5, 12)
    asyncio.run(_seed_unlinked_release(monday, "Inflight Book"))

    settings = deps_module.get_settings()
    monkeypatch.setattr(settings, "comicvine_api_key", "test-key", raising=False)

    calls: list[list[int]] = []

    async def _fake_resolve(settings, series_ids):
        calls.append(sorted(series_ids))  # never clears _resolving — simulates in-flight

    releases_module._resolving.clear()
    with patch.object(releases_module, "_resolve_links", _fake_resolve):
        client.get("/api/releases/weekly?week=2025-20")
        client.get("/api/releases/weekly?week=2025-20")
    releases_module._resolving.clear()

    assert len(calls) == 1


# ── auto_download on the page-load refresh path ──────────────────────────────


def _auto_release(monday):
    return [
        {
            "comicvine_id": "issue-auto-1",
            "issue_number": "1",
            "title": "Auto Issue",
            "store_date": monday.isoformat(),
            "cover_url": None,
            "series": {"comicvine_id": "series-auto", "title": "Auto Series"},
        }
    ]


def _client_returning(releases):
    stub = AsyncMock()
    stub.get_weekly_releases.return_value = releases
    # get_volume must return a real dict: _enrich_publishers runs for every new
    # series and a bare AsyncMock's .get() hands back a coroutine, which SQLAlchemy
    # then tries to bind as a column value.
    stub.get_volume.return_value = {"publisher": "Test Publisher", "start_year": 2025}

    async def _override():
        yield stub

    app.dependency_overrides[get_metadata_provider] = _override
    return stub


def _jobs_for_series(title):
    """Return (issue_status, job_statuses) for the single issue of `title`."""
    from sqlalchemy import select

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue, Series

    async def _run():
        async with AsyncSessionLocal() as db:
            series = (await db.execute(select(Series).where(Series.title == title))).scalar_one()
            issue = (
                await db.execute(select(Issue).where(Issue.series_id == series.id))
            ).scalar_one()
            jobs = (
                (await db.execute(select(DownloadJob).where(DownloadJob.issue_id == issue.id)))
                .scalars()
                .all()
            )
            return issue.status, [j.status for j in jobs]

    return asyncio.run(_run())


def _set_auto_download(title):
    from sqlalchemy import select

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import Series

    async def _run():
        async with AsyncSessionLocal() as db:
            series = (await db.execute(select(Series).where(Series.title == title))).scalar_one()
            series.auto_download = True
            series.subscribed = True
            await db.commit()

    asyncio.run(_run())


def test_weekly_refresh_does_not_enqueue_without_auto_download():
    """A new issue on an ordinary series stays 'unknown' with no job — unchanged."""
    monday = _current_week_monday()
    _client_returning(_auto_release(monday))
    try:
        with TestClient(app) as c:
            c.get("/api/releases/weekly")
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    status, jobs = _jobs_for_series("Auto Series")
    assert status == "unknown"
    assert jobs == []


def test_weekly_refresh_enqueues_new_issue_on_auto_download_series(monkeypatch):
    """The page-load refresh queues newly-discovered issues on auto_download series.

    Regression test for the real gap: this enqueue existed only in
    nightly_calendar_refresh, which runs solely from the manual Refresh button, so
    auto_download series silently accumulated 'unknown' issues that nothing queued.
    """
    # Don't let the queued job actually run a search against real indexers.
    monkeypatch.setattr("pullbox.routers.releases.run_job_now", AsyncMock())

    monday = _current_week_monday()
    releases = _auto_release(monday)

    # First load creates the series (auto_download defaults off), so flip it on and
    # bring in a second issue the way a new release would arrive.
    _client_returning(releases)
    try:
        with TestClient(app) as c:
            c.get("/api/releases/weekly")
            _set_auto_download("Auto Series")
            releases.append(
                {
                    "comicvine_id": "issue-auto-2",
                    "issue_number": "2",
                    "title": "Auto Issue Two",
                    "store_date": monday.isoformat(),
                    "cover_url": None,
                    "series": {"comicvine_id": "series-auto", "title": "Auto Series"},
                }
            )
            c.get("/api/releases/weekly")
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    from sqlalchemy import select

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue, Series

    async def _run():
        async with AsyncSessionLocal() as db:
            series = (
                await db.execute(select(Series).where(Series.title == "Auto Series"))
            ).scalar_one()
            issues = (
                (await db.execute(select(Issue).where(Issue.series_id == series.id)))
                .scalars()
                .all()
            )
            by_num = {i.issue_number: i for i in issues}
            jobs = (await db.execute(select(DownloadJob))).scalars().all()
            return by_num, {j.issue_id for j in jobs}

    by_num, job_issue_ids = asyncio.run(_run())

    # #2 arrived while auto_download was on → queued and promoted out of 'unknown'.
    assert by_num["2"].id in job_issue_ids
    assert by_num["2"].status == "wanted"
    # #1 predates the flag; the contract is "each newly-created row", not a backfill.
    assert by_num["1"].id not in job_issue_ids
