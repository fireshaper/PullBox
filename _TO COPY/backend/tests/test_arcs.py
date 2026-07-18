"""Tests for story arc support: ComicVine client methods, enrichment, and the
issue-arcs endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from pullbox.clients.comicvine import ComicVineClient, ComicVineError
from pullbox.deps import get_comicvine_client
from pullbox.main import app


class SequentialMockTransport(httpx.AsyncBaseTransport):
    """Returns pre-configured responses in sequence; repeats last entry if exhausted."""

    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self._responses = responses
        self._index = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        status_code, json_data = self._responses[idx]
        return httpx.Response(status_code, json=json_data)


def _make_client(*responses: tuple[int, dict]) -> tuple[ComicVineClient, SequentialMockTransport]:
    transport = SequentialMockTransport(list(responses))
    client = ComicVineClient(
        api_key="testkey",
        base_url="https://fake.comicvine.test",
        transport=transport,
    )
    return client, transport


# ── ComicVineClient.get_issue ─────────────────────────────────────────────────


async def test_get_issue_maps_story_arc_credits():
    payload = {
        "status_code": 1,
        "results": {
            "id": 555001,
            "issue_number": "5",
            "name": "Rebirth",
            "story_arc_credits": [
                {"id": 4045111, "name": "Dark Nights"},
                {"id": 4045222, "name": "Metal"},
            ],
        },
    }
    client, transport = _make_client((200, payload))
    result = await client.get_issue("555001")
    await client.close()

    assert result["comicvine_id"] == "555001"
    assert result["issue_number"] == "5"
    assert result["story_arcs"] == [
        {"comicvine_id": "4045111", "name": "Dark Nights"},
        {"comicvine_id": "4045222", "name": "Metal"},
    ]
    # Uses the 4000- resource prefix on the detail endpoint.
    assert "/issue/4000-555001" in str(transport.requests[0].url)


async def test_get_issue_handles_no_arcs():
    payload = {
        "status_code": 1,
        "results": {"id": 1, "issue_number": "1", "name": None, "story_arc_credits": None},
    }
    client, _ = _make_client((200, payload))
    result = await client.get_issue("1")
    await client.close()
    assert result["story_arcs"] == []


async def test_get_issue_raises_on_missing():
    client, _ = _make_client((200, {"status_code": 1, "results": {}}))
    with pytest.raises(ComicVineError):
        await client.get_issue("999")
    await client.close()


# ── ComicVineClient.get_story_arc ─────────────────────────────────────────────


async def test_get_story_arc_maps_metadata_and_issues():
    payload = {
        "status_code": 1,
        "results": {
            "id": 4045111,
            "name": "Dark Nights",
            "publisher": {"name": "DC Comics"},
            "image": {"small_url": "https://example.com/arc.jpg"},
            "description": "A crossover.",
            "count_of_issue_appearances": 12,
            "issues": [
                {"id": 555001, "name": "Rebirth", "site_detail_url": "https://cv/i/1"},
                {"id": 777888, "name": None, "site_detail_url": "https://cv/i/2"},
            ],
        },
    }
    client, transport = _make_client((200, payload))
    result = await client.get_story_arc("4045111")
    await client.close()

    assert result["comicvine_id"] == "4045111"
    assert result["name"] == "Dark Nights"
    assert result["publisher"] == "DC Comics"
    assert result["cover_url"] == "https://example.com/arc.jpg"
    assert result["count_of_issue_appearances"] == 12
    assert result["issues"][0] == {
        "comicvine_id": "555001",
        "name": "Rebirth",
        "site_detail_url": "https://cv/i/1",
    }
    assert "/story_arc/4045-4045111" in str(transport.requests[0].url)


# ── Enrichment on sync + issue-arcs endpoint (via TestClient) ─────────────────

FAKE_VOLUME = {
    "comicvine_id": "99001",
    "title": "Batman",
    "publisher": "DC Comics",
    "start_year": 2016,
    "cover_url": None,
    "description": "desc",
    "issue_count": 2,
}

FAKE_ISSUES = [
    {
        "comicvine_id": "555001",
        "issue_number": "1",
        "title": "One",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "description": None,
    },
    {
        "comicvine_id": "555002",
        "issue_number": "2",
        "title": "Two",
        "cover_date": "2016-02-01",
        "store_date": "2016-02-03",
        "cover_url": None,
        "description": None,
    },
]

# Per-issue detail responses keyed by comicvine issue id.
ISSUE_DETAILS = {
    "555001": {
        "comicvine_id": "555001",
        "issue_number": "1",
        "title": "One",
        "story_arcs": [{"comicvine_id": "4045111", "name": "Dark Nights"}],
    },
    "555002": {
        "comicvine_id": "555002",
        "issue_number": "2",
        "title": "Two",
        "story_arcs": [],
    },
}

ARC_DETAIL = {
    "comicvine_id": "4045111",
    "name": "Dark Nights",
    "publisher": "DC Comics",
    "cover_url": "https://example.com/arc.jpg",
    "description": "A crossover.",
    "count_of_issue_appearances": 3,
    "issues": [
        # One is in the local library (matches 555001), the other is external.
        {"comicvine_id": "555001", "name": "One", "site_detail_url": "https://cv/i/1"},
        {"comicvine_id": "888999", "name": "Elsewhere #4", "site_detail_url": "https://cv/i/2"},
    ],
}


def _make_mock_cv() -> AsyncMock:
    mock = AsyncMock()
    mock.get_volume.return_value = FAKE_VOLUME
    mock.get_issues.return_value = FAKE_ISSUES
    mock.get_issue.side_effect = lambda cv_id: ISSUE_DETAILS[cv_id]
    mock.get_story_arc.return_value = ARC_DETAIL
    return mock


@pytest.fixture
def cv_mock():
    mock = _make_mock_cv()

    async def _override():
        yield mock

    app.dependency_overrides[get_comicvine_client] = _override
    try:
        yield mock
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _add_and_sync(client) -> int:
    add = client.post("/api/series/", json={"comicvine_id": "99001"})
    assert add.status_code == 201
    series_id = add.json()["id"]
    sync = client.post(f"/api/series/{series_id}/sync-issues")
    assert sync.status_code == 200
    return series_id


def test_sync_populates_arc_badges(client, cv_mock):
    series_id = _add_and_sync(client)

    issues = client.get(f"/api/series/{series_id}/issues").json()
    by_number = {i["issue_number"]: i for i in issues}

    # Issue 1 belongs to Dark Nights; issue 2 has no arcs.
    assert [a["name"] for a in by_number["1"]["arcs"]] == ["Dark Nights"]
    assert by_number["2"]["arcs"] == []


def test_sync_only_enriches_unsynced_issues(client, cv_mock):
    series_id = _add_and_sync(client)
    first_call_count = cv_mock.get_issue.call_count
    assert first_call_count == 2  # both issues enriched on first sync

    # Second sync should not re-fetch already-enriched issues.
    client.post(f"/api/series/{series_id}/sync-issues")
    assert cv_mock.get_issue.call_count == first_call_count


def test_issue_arcs_endpoint_returns_members(client, cv_mock):
    series_id = _add_and_sync(client)
    issues = client.get(f"/api/series/{series_id}/issues").json()
    issue1 = next(i for i in issues if i["issue_number"] == "1")

    resp = client.get(f"/api/issues/{issue1['id']}/arcs")
    assert resp.status_code == 200
    arcs = resp.json()
    assert len(arcs) == 1
    arc = arcs[0]
    assert arc["name"] == "Dark Nights"
    assert arc["count_of_issue_appearances"] == 3

    members = {m["comicvine_id"]: m for m in arc["issues"]}
    # Local member is hydrated with library info.
    assert members["555001"]["in_library"] is True
    assert members["555001"]["local_issue_id"] == issue1["id"]
    assert members["555001"]["local_series_id"] == series_id
    assert members["555001"]["local_series_title"] == "Batman"
    # External member is flagged out-of-library with just its ComicVine data.
    assert members["888999"]["in_library"] is False
    assert members["888999"]["local_issue_id"] is None
    assert members["888999"]["site_detail_url"] == "https://cv/i/2"


def test_issue_arcs_empty_for_issue_without_arcs(client, cv_mock):
    series_id = _add_and_sync(client)
    issues = client.get(f"/api/series/{series_id}/issues").json()
    issue2 = next(i for i in issues if i["issue_number"] == "2")

    resp = client.get(f"/api/issues/{issue2['id']}/arcs")
    assert resp.status_code == 200
    assert resp.json() == []
