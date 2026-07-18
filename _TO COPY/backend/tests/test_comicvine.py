"""Tests for the ComicVine API client (Phase 3)."""

import httpx
import pytest

from pullbox.clients.comicvine import ComicVineClient, ComicVineError


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


# ---------------------------------------------------------------------------
# Step 3.1 — Base client: success path, API-level error, HTTP error
# ---------------------------------------------------------------------------


async def test_get_returns_data_on_success():
    client, _ = _make_client((200, {"status_code": 1, "results": []}))
    data = await client._get("/search")
    await client.close()
    assert data["results"] == []


async def test_get_raises_on_api_error_status():
    """HTTP 200 but ComicVine status_code != 1 → ComicVineError."""
    client, _ = _make_client((200, {"status_code": 101, "error": "Invalid API Key"}))
    with pytest.raises(ComicVineError):
        await client._get("/search")
    await client.close()


async def test_get_raises_on_http_error():
    """Non-200 HTTP response → ComicVineError."""
    client, _ = _make_client((404, {}))
    with pytest.raises(ComicVineError):
        await client._get("/search")
    await client.close()


# ---------------------------------------------------------------------------
# Step 3.2 — search_series: field mapping, null publisher
# ---------------------------------------------------------------------------


async def test_search_series_returns_mapped_results():
    payload = {
        "status_code": 1,
        "results": [
            {
                "id": 1,
                "name": "Batman",
                "publisher": {"name": "DC Comics"},
                "start_year": "2011",
                "image": {"small_url": "https://img.example.com/bat.jpg"},
                "description": "A dark hero",
                "count_of_issues": 52,
            },
            {
                "id": 2,
                "name": "Batman: TDK",
                "publisher": {"name": "DC Comics"},
                "start_year": None,
                "image": None,
                "description": None,
                "count_of_issues": 12,
            },
        ],
    }
    client, _ = _make_client((200, payload))
    results = await client.search_series("Batman")
    await client.close()

    assert len(results) == 2
    first = results[0]
    assert first["comicvine_id"] == "1"
    assert first["title"] == "Batman"
    assert first["publisher"] == "DC Comics"
    assert first["start_year"] == "2011"
    assert first["cover_url"] == "https://img.example.com/bat.jpg"
    assert first["issue_count"] == 52


async def test_search_series_missing_publisher_is_none():
    payload = {
        "status_code": 1,
        "results": [
            {
                "id": 1,
                "name": "Indie Comic",
                "publisher": None,
                "start_year": None,
                "image": None,
                "description": None,
                "count_of_issues": 5,
            }
        ],
    }
    client, _ = _make_client((200, payload))
    results = await client.search_series("Indie")
    await client.close()

    assert results[0]["publisher"] is None


# ---------------------------------------------------------------------------
# Step 3.3 — get_issues: pagination (auto-fetches remaining pages)
# ---------------------------------------------------------------------------


async def test_get_issues_paginates_across_pages():
    def _issue(n: int) -> dict:
        return {
            "id": n,
            "issue_number": str(n),
            "name": f"Issue {n}",
            "cover_date": None,
            "store_date": None,
            "image": None,
            "description": None,
        }

    page1 = (
        200,
        {
            "status_code": 1,
            "number_of_total_results": 3,
            "limit": 2,
            "results": [_issue(1), _issue(2)],
        },
    )
    page2 = (
        200,
        {
            "status_code": 1,
            "number_of_total_results": 3,
            "limit": 2,
            "results": [_issue(3)],
        },
    )
    client, transport = _make_client(page1, page2)
    results = await client.get_issues("12345", limit=2)
    await client.close()

    assert len(results) == 3
    assert results[0]["comicvine_id"] == "1"
    assert results[1]["comicvine_id"] == "2"
    assert results[2]["comicvine_id"] == "3"
    # Confirm two HTTP requests were made (one per page)
    assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Step 3.3a — get_volume: field mapping, 404 error, prefix handling
# ---------------------------------------------------------------------------


def _volume_payload(volume_id: int = 12345) -> dict:
    return {
        "status_code": 1,
        "results": {
            "id": volume_id,
            "name": "Batman",
            "publisher": {"name": "DC Comics"},
            "start_year": "2011",
            "count_of_issues": 52,
            "image": {"small_url": "https://img.example.com/batman.jpg"},
            "description": "The Dark Knight's ongoing series.",
            "deck": "A hero of Gotham City.",
        },
    }


async def test_get_volume_returns_mapped_dict():
    client, transport = _make_client((200, _volume_payload()))
    result = await client.get_volume("12345")
    await client.close()

    assert result["comicvine_id"] == "12345"
    assert result["title"] == "Batman"
    assert result["publisher"] == "DC Comics"
    assert result["start_year"] == "2011"
    assert result["issue_count"] == 52
    assert result["cover_url"] == "https://img.example.com/batman.jpg"
    assert result["description"] == "The Dark Knight's ongoing series."
    # Confirm the 4050- prefix was used in the URL
    assert "4050-12345" in str(transport.requests[0].url)


async def test_get_volume_404_raises_not_found():
    client, _ = _make_client((404, {}))
    with pytest.raises(ComicVineError, match="not found"):
        await client.get_volume("99999")
    await client.close()


async def test_get_volume_prefix_is_idempotent():
    """Passing a bare ID or a 4050-prefixed ID must both produce the same URL."""
    client, transport = _make_client(
        (200, _volume_payload()),
        (200, _volume_payload()),
    )
    await client.get_volume("12345")
    await client.get_volume("4050-12345")
    await client.close()

    url_bare = str(transport.requests[0].url)
    url_prefixed = str(transport.requests[1].url)

    assert "4050-12345" in url_bare
    assert "4050-12345" in url_prefixed
    # The prefix must not be doubled
    assert "4050-4050-" not in url_bare
    assert "4050-4050-" not in url_prefixed


# ---------------------------------------------------------------------------
# Step 3.4 — get_weekly_releases: three issues across two volumes
# ---------------------------------------------------------------------------


async def test_get_weekly_releases_returns_all_with_series():
    payload = {
        "status_code": 1,
        "results": [
            {
                "id": 101,
                "issue_number": "1",
                "name": "Bat #1",
                "store_date": "2025-01-01",
                "image": {"small_url": "https://img.example.com/bat1.jpg"},
                "volume": {"id": 1, "name": "Batman"},
            },
            {
                "id": 102,
                "issue_number": "2",
                "name": "Bat #2",
                "store_date": "2025-01-03",
                "image": None,
                "volume": {"id": 1, "name": "Batman"},
            },
            {
                "id": 201,
                "issue_number": "1",
                "name": "Sup #1",
                "store_date": "2025-01-05",
                "image": {"small_url": "https://img.example.com/sup1.jpg"},
                "volume": {"id": 2, "name": "Superman"},
            },
        ],
    }
    client, _ = _make_client((200, payload))
    results = await client.get_weekly_releases("2025-01-01", "2025-01-07")
    await client.close()

    assert len(results) == 3

    ids = [r["comicvine_id"] for r in results]
    assert "101" in ids
    assert "102" in ids
    assert "201" in ids

    # Every result must have a populated series block
    for r in results:
        assert r["series"]["comicvine_id"], "series.comicvine_id must be non-empty"
        assert r["series"]["title"], "series.title must be non-empty"

    bat_releases = [r for r in results if r["series"]["comicvine_id"] == "1"]
    sup_releases = [r for r in results if r["series"]["comicvine_id"] == "2"]
    assert len(bat_releases) == 2
    assert len(sup_releases) == 1
