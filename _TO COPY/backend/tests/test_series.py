"""Tests for Phase 4: Series & Issue API (steps 4.1–4.7)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pullbox.deps import get_comicvine_client
from pullbox.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────

FAKE_VOLUME = {
    "comicvine_id": "99001",
    "title": "Batman",
    "publisher": "DC Comics",
    "start_year": 2016,
    "cover_url": "https://example.com/batman.jpg",
    "description": "The Dark Knight.",
    "issue_count": 3,
}

FAKE_SEARCH_RESULTS = [
    {
        "comicvine_id": "99001",
        "title": "Batman",
        "publisher": "DC Comics",
        "start_year": 2016,
        "cover_url": "https://example.com/batman.jpg",
        "description": "The Dark Knight.",
        "issue_count": 3,
    },
    {
        "comicvine_id": "99002",
        "title": "Batman: Year One",
        "publisher": "DC Comics",
        "start_year": 1987,
        "cover_url": None,
        "description": None,
        "issue_count": 4,
    },
]

FAKE_ISSUES = [
    {
        "comicvine_id": "555001",
        "issue_number": "1",
        "title": "Issue One",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "description": None,
    },
    {
        "comicvine_id": "555002",
        "issue_number": "2",
        "title": "Issue Two",
        "cover_date": "2016-02-01",
        "store_date": "2016-02-03",
        "cover_url": None,
        "description": None,
    },
    {
        "comicvine_id": "555003",
        "issue_number": "3",
        "title": "Issue Three",
        "cover_date": "2016-03-01",
        "store_date": None,
        "cover_url": None,
        "description": None,
    },
]


def _make_mock_cv(
    *,
    search_results=None,
    volume=None,
    issues=None,
):
    """Return an async generator override for get_comicvine_client."""
    mock = AsyncMock()
    if search_results is not None:
        mock.search_series.return_value = search_results
    if volume is not None:
        mock.get_volume.return_value = volume
    if issues is not None:
        mock.get_issues.return_value = issues

    async def _override():
        yield mock

    return _override


@pytest.fixture
def client():
    """Clean TestClient with no ComicVine override (use per-test overrides)."""
    with TestClient(app) as c:
        yield c


# ── Step 4.1 — Router registered, no import errors ───────────────────────────


def test_series_router_registered(client):
    """/api/series prefix appears in openapi schema."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    series_paths = [p for p in paths if p.startswith("/api/series")]
    assert len(series_paths) > 0, "No /api/series routes found in openapi.json"


# ── Step 4.2 — Search endpoint ────────────────────────────────────────────────


def test_search_rejects_short_query(client):
    resp = client.get("/api/series/search?q=b")
    assert resp.status_code == 422


def test_search_returns_results_with_in_library(client):
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(
        search_results=FAKE_SEARCH_RESULTS
    )
    try:
        resp = client.get("/api/series/search?q=Batman")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        for item in data:
            assert "in_library" in item
        # Neither series is in the DB yet
        assert data[0]["in_library"] is False
        assert data[1]["in_library"] is False
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_search_marks_existing_series_as_in_library(client):
    # First add a series so it exists in the DB
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(
        volume=FAKE_VOLUME, issues=[]
    )
    try:
        add_resp = client.post("/api/series/", json={"comicvine_id": "99001"})
        assert add_resp.status_code == 201
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    # Now search — 99001 should be in_library=True
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(
        search_results=FAKE_SEARCH_RESULTS
    )
    try:
        resp = client.get("/api/series/search?q=Batman")
        assert resp.status_code == 200
        data = resp.json()
        by_cv_id = {item["comicvine_id"]: item for item in data}
        assert by_cv_id["99001"]["in_library"] is True
        assert by_cv_id["99002"]["in_library"] is False
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


# ── Step 4.3 — Add series ─────────────────────────────────────────────────────


def test_add_series_returns_201(client):
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(volume=FAKE_VOLUME)
    try:
        resp = client.post("/api/series/", json={"comicvine_id": "99001"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert data["title"] == "Batman"
        assert data["comicvine_id"] == "99001"
        assert data["subscribed"] is False
        assert data["auto_download"] is False
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_add_series_respects_subscription_flag(client):
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(volume=FAKE_VOLUME)
    try:
        resp = client.post(
            "/api/series/", json={"comicvine_id": "99001", "subscribed": True}
        )
        assert resp.status_code == 201
        assert resp.json()["subscribed"] is True
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_add_series_409_on_duplicate(client):
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(volume=FAKE_VOLUME)
    try:
        r1 = client.post("/api/series/", json={"comicvine_id": "99001"})
        assert r1.status_code == 201
        r2 = client.post("/api/series/", json={"comicvine_id": "99001"})
        assert r2.status_code == 409
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


# ── Step 4.4 — List and get series ───────────────────────────────────────────


def _add_series(client, cv_id: str, title: str):
    volume = {**FAKE_VOLUME, "comicvine_id": cv_id, "title": title}
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(volume=volume)
    try:
        resp = client.post("/api/series/", json={"comicvine_id": cv_id})
        assert resp.status_code == 201
        return resp.json()
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_list_series_returns_all(client):
    _add_series(client, "99001", "Batman")
    _add_series(client, "99002", "Superman")

    resp = client.get("/api/series/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["per_page"] == 20


def test_list_series_pagination(client):
    _add_series(client, "99001", "Batman")
    _add_series(client, "99002", "Superman")

    resp = client.get("/api/series/?page=1&per_page=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1

    resp2 = client.get("/api/series/?page=2&per_page=1")
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1


def test_list_series_ordered_alphabetically(client):
    # Insert out of alphabetical order; expect a case-insensitive A→Z result.
    _add_series(client, "99003", "aquaman")
    _add_series(client, "99001", "Batman")
    _add_series(client, "99002", "Superman")

    resp = client.get("/api/series/")
    assert resp.status_code == 200
    titles = [s["title"] for s in resp.json()["items"]]
    assert titles == ["aquaman", "Batman", "Superman"]


def test_list_series_all_ignores_pagination(client):
    for i in range(25):
        _add_series(client, f"9900{i}", f"Series {i:02d}")

    # Default paging caps at 20 items…
    default = client.get("/api/series/").json()
    assert default["total"] == 25
    assert len(default["items"]) == 20

    # …but all=true returns every row in one response.
    everything = client.get("/api/series/?all=true").json()
    assert everything["total"] == 25
    assert len(everything["items"]) == 25
    assert everything["page"] == 1


def test_get_series_returns_detail(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    resp = client.get(f"/api/series/{series_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == series_id
    assert data["title"] == "Batman"
    assert "description" in data  # SeriesDetailResponse includes description


def test_get_series_404(client):
    resp = client.get("/api/series/99999")
    assert resp.status_code == 404


# ── Step 4.5 — Update series ─────────────────────────────────────────────────


def test_patch_series_subscribed(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    # Subscribe
    resp = client.patch(f"/api/series/{series_id}", json={"subscribed": True})
    assert resp.status_code == 200
    assert resp.json()["subscribed"] is True

    # Verify it persisted
    get_resp = client.get(f"/api/series/{series_id}")
    assert get_resp.json()["subscribed"] is True

    # Unsubscribe
    resp2 = client.patch(f"/api/series/{series_id}", json={"subscribed": False})
    assert resp2.json()["subscribed"] is False


def test_patch_series_404(client):
    resp = client.patch("/api/series/99999", json={"subscribed": True})
    assert resp.status_code == 404


# ── Step 4.6 — Sync issues ────────────────────────────────────────────────────


def test_sync_issues_adds_on_first_call(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        resp = client.post(f"/api/series/{series_id}/sync-issues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 3
        assert data["updated"] == 0
        assert data["total"] == 3
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_sync_issues_upserts_on_second_call(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
        resp = client.post(f"/api/series/{series_id}/sync-issues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 0
        assert data["updated"] == 3
        assert data["total"] == 3
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


def test_sync_issues_does_not_overwrite_status(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    # Mark first issue as downloaded directly via the want endpoint
    issues_resp = client.get(f"/api/series/{series_id}/issues")
    issue_id = issues_resp.json()[0]["id"]
    client.post(f"/api/issues/{issue_id}/want")

    # Sync again — status should NOT revert to "unknown"
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    get_resp = client.get(f"/api/series/{series_id}/issues")
    first_issue = next(i for i in get_resp.json() if i["id"] == issue_id)
    assert first_issue["status"] == "wanted", "sync must not overwrite issue status"


# ── Step 4.7 — Issue list ────────────────────────────────────────────────────


def test_list_issues_ordered_by_number(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    resp = client.get(f"/api/series/{series_id}/issues")
    assert resp.status_code == 200
    numbers = [i["issue_number"] for i in resp.json()]
    assert numbers == sorted(numbers), "Issues must be returned in ascending issue_number order"


def test_list_issues_status_filter(client):
    added = _add_series(client, "99001", "Batman")
    series_id = added["id"]

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    # No wanted issues yet
    resp = client.get(f"/api/series/{series_id}/issues?status=wanted")
    assert resp.status_code == 200
    assert resp.json() == []

    # Mark one issue as wanted
    issues_resp = client.get(f"/api/series/{series_id}/issues")
    issue_id = issues_resp.json()[0]["id"]
    client.post(f"/api/issues/{issue_id}/want")

    # Now one wanted issue should appear
    resp2 = client.get(f"/api/series/{series_id}/issues?status=wanted")
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["status"] == "wanted"


def test_list_issues_404_for_bad_series(client):
    resp = client.get("/api/series/99999/issues")
    assert resp.status_code == 404
