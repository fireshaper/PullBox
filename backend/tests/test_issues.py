"""Tests for Phase 4 step 4.8: mark issue as wanted / skipped."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pullbox.deps import get_metadata_provider
from pullbox.main import app

FAKE_VOLUME = {
    "metron_id": "m99010",
    "comicvine_id": "99010",
    "title": "X-Men",
    "publisher": "Marvel",
    "start_year": 2019,
    "cover_url": None,
    "description": None,
    "issue_count": 2,
}

FAKE_ISSUES = [
    {
        "metron_id": "m600001",
        "comicvine_id": "600001",
        "issue_number": "1",
        "title": "First Issue",
        "cover_date": "2019-01-01",
        "store_date": "2019-01-02",
        "cover_url": None,
        "description": None,
    },
    {
        "metron_id": "m600002",
        "comicvine_id": "600002",
        "issue_number": "2",
        "title": "Second Issue",
        "cover_date": "2019-02-01",
        "store_date": None,
        "cover_url": None,
        "description": None,
    },
]


def _make_mock_provider(*, volume=None, issues=None):
    mock = AsyncMock()
    mock.get_volume.return_value = volume if volume is not None else FAKE_VOLUME
    if issues is not None:
        mock.get_issues.return_value = issues
    mock.get_issue.return_value = {"metron_id": None, "comicvine_id": None, "story_arcs": []}

    async def _override():
        yield mock

    return _override


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(client):
    """Add a series with two synced issues; return list of issue dicts."""
    app.dependency_overrides[get_metadata_provider] = _make_mock_provider(volume=FAKE_VOLUME)
    try:
        add_resp = client.post("/api/series/", json={"comicvine_id": "99010"})
        series_id = add_resp.json()["id"]
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    app.dependency_overrides[get_metadata_provider] = _make_mock_provider(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    issues_resp = client.get(f"/api/series/{series_id}/issues")
    return issues_resp.json()


# ── Step 4.8 ──────────────────────────────────────────────────────────────────


def test_mark_wanted_sets_status(client, seeded):
    issue_id = seeded[0]["id"]
    resp = client.post(f"/api/issues/{issue_id}/want")
    assert resp.status_code == 200
    assert resp.json()["status"] == "wanted"


def test_mark_skipped_sets_status(client, seeded):
    issue_id = seeded[1]["id"]
    resp = client.post(f"/api/issues/{issue_id}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_mark_downloaded_issue_as_wanted_returns_409(client, seeded):
    """An issue that is already 'downloaded' cannot be re-wanted."""
    import asyncio

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import Issue

    issue_id = seeded[0]["id"]

    # Force the issue status to 'downloaded' directly in the database.
    # asyncio.run() creates a fresh event loop so we can use async SQLAlchemy
    # from within a sync test. Both this session and the TestClient's session
    # hit the same SQLite file on disk; the commit is visible after WAL sync.
    async def _force_downloaded():
        async with AsyncSessionLocal() as session:
            issue = await session.get(Issue, issue_id)
            issue.status = "downloaded"
            await session.commit()

    asyncio.run(_force_downloaded())

    resp = client.post(f"/api/issues/{issue_id}/want")
    assert resp.status_code == 409


def test_mark_wanted_404_for_missing_issue(client):
    resp = client.post("/api/issues/99999/want")
    assert resp.status_code == 404


def test_mark_skipped_404_for_missing_issue(client):
    resp = client.post("/api/issues/99999/skip")
    assert resp.status_code == 404
