"""Tests for the Metron API client."""

from __future__ import annotations

import base64

import httpx
import pytest

from pullbox.clients.metron import (
    MetronClient,
    MetronError,
    MetronRateLimitError,
    _RateLimiter,
)


class SequentialMockTransport(httpx.AsyncBaseTransport):
    """Returns pre-configured responses in sequence; repeats last entry if exhausted.

    Each response is ``(status_code, json_data, headers?)``.
    """

    def __init__(self, responses: list[tuple]) -> None:
        self._responses = responses
        self._index = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        entry = self._responses[idx]
        status_code, json_data = entry[0], entry[1]
        headers = entry[2] if len(entry) > 2 else None
        return httpx.Response(status_code, json=json_data, headers=headers)


def _make_client(*responses: tuple) -> tuple[MetronClient, SequentialMockTransport]:
    transport = SequentialMockTransport(list(responses))
    client = MetronClient(
        "user",
        "pass",
        base_url="https://fake.metron.test/api",
        transport=transport,
    )
    return client, transport


# ── Base request behaviour ────────────────────────────────────────────────────


async def test_basic_auth_header_is_sent():
    client, transport = _make_client((200, {"results": []}))
    await client._get("/series/")
    await client.close()
    auth = transport.requests[0].headers["Authorization"]
    assert auth == "Basic " + base64.b64encode(b"user:pass").decode()


async def test_get_raises_on_http_error():
    client, _ = _make_client((500, {}))
    with pytest.raises(MetronError):
        await client._get("/series/")
    await client.close()


async def test_get_raises_rate_limit_on_429():
    client, _ = _make_client((429, {}))
    with pytest.raises(MetronRateLimitError):
        await client._get("/series/")
    await client.close()


# ── search_series ─────────────────────────────────────────────────────────────


async def test_search_series_maps_fields_and_uses_name_param():
    payload = {
        "count": 1,
        "next": None,
        "results": [
            {
                "id": 100,
                "series": "Batman (2016)",
                "year_began": 2016,
                "issue_count": 3,
            }
        ],
    }
    client, transport = _make_client((200, payload))
    results = await client.search_series("batman")
    await client.close()

    assert len(results) == 1
    r = results[0]
    assert r["metron_id"] == "100"
    assert r["comicvine_id"] is None  # list endpoint has no cv_id
    assert r["title"] == "Batman (2016)"
    assert r["start_year"] == 2016
    assert r["issue_count"] == 3
    assert r["source"] == "metron"
    assert "name=batman" in str(transport.requests[0].url)
    assert "/series/" in str(transport.requests[0].url)


# ── find_series_by_cv_id ──────────────────────────────────────────────────────


async def test_find_series_by_cv_id_filters_and_maps():
    payload = {"count": 1, "next": None, "results": [{"id": 100, "series": "Batman (2016)"}]}
    client, transport = _make_client((200, payload))
    record = await client.find_series_by_cv_id("99001")
    await client.close()

    assert record["metron_id"] == "100"
    assert "cv_id=99001" in str(transport.requests[0].url)


async def test_find_series_by_cv_id_returns_none_when_unknown():
    client, _ = _make_client((200, {"count": 0, "next": None, "results": []}))
    assert await client.find_series_by_cv_id("99001") is None
    await client.close()


# ── get_volume (series detail) ────────────────────────────────────────────────


async def test_get_volume_maps_detail_including_cv_id():
    payload = {
        "id": 100,
        "name": "Batman",
        "publisher": {"id": 1, "name": "DC Comics"},
        "year_began": 2016,
        "issue_count": 3,
        "desc": "The Dark Knight.",
        "image": "https://img.metron.test/batman.jpg",
        "cv_id": 99001,
    }
    client, transport = _make_client((200, payload))
    volume = await client.get_volume("100")
    await client.close()

    assert volume["metron_id"] == "100"
    assert volume["comicvine_id"] == "99001"
    assert volume["title"] == "Batman"
    assert volume["publisher"] == "DC Comics"
    assert volume["start_year"] == 2016
    assert volume["cover_url"] == "https://img.metron.test/batman.jpg"
    assert volume["description"] == "The Dark Knight."
    assert "/series/100/" in str(transport.requests[0].url)


async def test_get_volume_raises_on_missing():
    client, _ = _make_client((404, {}))
    with pytest.raises(MetronError):
        await client.get_volume("999")
    await client.close()


# ── get_issues (paginated) ────────────────────────────────────────────────────


async def test_get_issues_follows_pagination():
    page1 = {
        "count": 2,
        "next": "https://fake.metron.test/api/issue/?series_id=100&page=2",
        "results": [
            {"id": 5001, "number": "1", "issue_name": "One", "cover_date": "2016-01-01",
             "store_date": "2016-01-06", "image": None},
        ],
    }
    page2 = {
        "count": 2,
        "next": None,
        "results": [
            {"id": 5002, "number": "2", "issue_name": "Two", "cover_date": "2016-02-01",
             "store_date": "2016-02-03", "image": None},
        ],
    }
    client, transport = _make_client((200, page1), (200, page2))
    issues = await client.get_issues("100")
    await client.close()

    assert [i["metron_id"] for i in issues] == ["5001", "5002"]
    assert issues[0]["issue_number"] == "1"
    assert issues[0]["title"] == "One"
    assert issues[0]["store_date"] == "2016-01-06"
    # First request carries the series_id filter; second follows the `next` link.
    assert "series_id=100" in str(transport.requests[0].url)
    assert "page=2" in str(transport.requests[1].url)


# ── get_issue (detail with inline arcs) ───────────────────────────────────────


async def test_get_issue_reads_inline_arcs():
    payload = {
        "id": 5001,
        "number": "1",
        "cv_id": 555001,
        "story_titles": ["Chapter One"],
        "arcs": [
            {"id": 811, "name": "Dark Nights", "cv_id": 4045111},
            {"id": 812, "name": "Metal"},
        ],
    }
    client, transport = _make_client((200, payload))
    result = await client.get_issue("5001")
    await client.close()

    assert result["metron_id"] == "5001"
    assert result["comicvine_id"] == "555001"
    assert result["issue_number"] == "1"
    assert result["title"] == "Chapter One"
    assert result["story_arcs"] == [
        {"metron_id": "811", "comicvine_id": "4045111", "name": "Dark Nights"},
        {"metron_id": "812", "comicvine_id": None, "name": "Metal"},
    ]
    assert "/issue/5001/" in str(transport.requests[0].url)


async def test_get_issue_handles_no_arcs():
    payload = {"id": 1, "number": "1", "arcs": []}
    client, _ = _make_client((200, payload))
    result = await client.get_issue("1")
    await client.close()
    assert result["story_arcs"] == []


# ── get_story_arc (detail + issue_list) ───────────────────────────────────────


async def test_get_story_arc_combines_detail_and_members():
    arc_detail = {
        "id": 811,
        "name": "Dark Nights",
        "desc": "A crossover.",
        "image": "https://img.metron.test/arc.jpg",
        "cv_id": 4045111,
    }
    members = {
        "count": 2,
        "next": None,
        "results": [
            {"id": 5001, "issue_name": "One", "resource_url": "https://metron/i/1"},
            {"id": 9999, "issue_name": "Elsewhere", "resource_url": "https://metron/i/2"},
        ],
    }
    client, transport = _make_client((200, arc_detail), (200, members))
    result = await client.get_story_arc("811")
    await client.close()

    assert result["metron_id"] == "811"
    assert result["comicvine_id"] == "4045111"
    assert result["name"] == "Dark Nights"
    assert result["cover_url"] == "https://img.metron.test/arc.jpg"
    assert result["count_of_issue_appearances"] == 2  # derived from member count
    assert result["issues"][0] == {
        "metron_id": "5001",
        "comicvine_id": None,
        "name": "One",
        "site_detail_url": "https://metron/i/1",
    }
    assert "/arc/811/" in str(transport.requests[0].url)
    assert "/arc/811/issue_list/" in str(transport.requests[1].url)


# ── get_weekly_releases ───────────────────────────────────────────────────────


async def test_get_weekly_releases_uses_store_date_range_and_nests_series():
    payload = {
        "count": 1,
        "next": None,
        "results": [
            {
                "id": 5001,
                "number": "1",
                "issue_name": "One",
                "store_date": "2025-05-07",
                "image": None,
                "series": {"id": 100, "name": "Batman"},
            }
        ],
    }
    client, transport = _make_client((200, payload))
    releases = await client.get_weekly_releases("2025-05-05", "2025-05-11")
    await client.close()

    assert len(releases) == 1
    rel = releases[0]
    assert rel["metron_id"] == "5001"
    assert rel["store_date"] == "2025-05-07"
    assert rel["series"] == {"metron_id": "100", "comicvine_id": None, "title": "Batman"}
    url = str(transport.requests[0].url)
    assert "store_date_range_after=2025-05-05" in url
    assert "store_date_range_before=2025-05-11" in url


# ── Rate limiter ──────────────────────────────────────────────────────────────


async def test_rate_limiter_per_minute_cap():
    lim = _RateLimiter(per_min=2, per_day=100)
    await lim.acquire()
    await lim.acquire()
    with pytest.raises(MetronRateLimitError):
        await lim.acquire()


async def test_rate_limiter_per_day_cap():
    lim = _RateLimiter(per_min=100, per_day=2)
    await lim.acquire()
    await lim.acquire()
    with pytest.raises(MetronRateLimitError):
        await lim.acquire()


async def test_rate_limiter_cooldown_after_throttle():
    lim = _RateLimiter(per_min=100, per_day=100)
    lim.note_throttle()
    with pytest.raises(MetronRateLimitError):
        await lim.acquire()


async def test_rate_limiter_burst_header_triggers_cooldown():
    lim = _RateLimiter(per_min=100, per_day=100)
    lim.note_headers(burst_remaining=0)
    with pytest.raises(MetronRateLimitError):
        await lim.acquire()
