"""Tests for Phase 6: Search Engine (steps 6.1–6.5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from pullbox.search import SearchResult, build_search_queries, fan_out_search, score_results

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_indexer(
    id: int,
    name: str,
    type: str = "newznab",
    enabled: bool = True,
    priority: int = 100,
    url: str = "http://indexer.test",
    api_key: str | None = "key",
) -> MagicMock:
    idx = MagicMock()
    idx.id = id
    idx.name = name
    idx.type = type
    idx.enabled = enabled
    idx.priority = priority
    idx.url = url
    idx.api_key = api_key
    return idx


def _make_issue(issue_number: str = "1") -> MagicMock:
    issue = MagicMock()
    issue.issue_number = issue_number
    return issue


def _make_series(title: str = "Batman") -> MagicMock:
    series = MagicMock()
    series.title = title
    return series


def _make_result(
    guid: str,
    title: str = "Batman 1",
    source_type: str = "usenet",
    published_at: datetime | None = None,
    indexer_id: int = 1,
    indexer_name: str = "Test",
) -> SearchResult:
    return SearchResult(
        indexer_id=indexer_id,
        indexer_name=indexer_name,
        source_type=source_type,
        title=title,
        guid=guid,
        download_url=f"http://example.com/{guid}.nzb",
        published_at=published_at,
    )


# ── Step 6.1 — SearchResult schema ───────────────────────────────────────────


def test_search_result_defaults():
    r = SearchResult(
        indexer_id=1,
        indexer_name="Test",
        source_type="usenet",
        title="Batman 1",
        guid="abc123",
        download_url="http://example.com/1.nzb",
    )
    assert r.seeders is None
    assert r.score == 0.0
    assert r.size_bytes is None
    assert r.published_at is None


def test_search_result_serializes_to_dict():
    r = SearchResult(
        indexer_id=2,
        indexer_name="MyIndexer",
        source_type="torrent",
        title="X-Men 5",
        guid="xyz",
        download_url="magnet:?xt=urn:...",
        size_bytes=1024,
        seeders=10,
        score=3.5,
    )
    d = r.as_dict()
    assert d["indexer_id"] == 2
    assert d["source_type"] == "torrent"
    assert d["seeders"] == 10
    assert d["score"] == 3.5


# ── Step 6.2 — NewznabClient ──────────────────────────────────────────────────

RSS_3_ITEMS = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>NZBGeek</title>
    <item>
      <title>Batman 001 (2024)</title>
      <guid>guid-001</guid>
      <enclosure url="http://dl.test/1.nzb" length="102400" type="application/x-nzb"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Batman 001 CBZ (2024)</title>
      <guid>guid-002</guid>
      <enclosure url="http://dl.test/2.nzb" length="204800" type="application/x-nzb"/>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Batman 001 PDF (2024)</title>
      <guid>guid-003</guid>
      <enclosure url="http://dl.test/3.nzb" length="51200" type="application/x-nzb"/>
      <pubDate>Wed, 03 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

RSS_EMPTY = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel><title>NZBGeek</title></channel>
</rss>
"""

RSS_2_ITEMS = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Batman Annual 001</title>
      <guid>guid-annual-1</guid>
      <enclosure url="http://dl.test/a1.nzb" length="102400" type="application/x-nzb"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Batman Annual 002</title>
      <guid>guid-annual-2</guid>
      <enclosure url="http://dl.test/a2.nzb" length="102400" type="application/x-nzb"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def _mock_http_response(status: int, content: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.content = content

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_client)


def test_newznab_search_returns_three_results():
    from pullbox.clients.newznab import NewznabClient

    indexer = _make_indexer(1, "NZBGeek")

    with patch("pullbox.clients.newznab.httpx.AsyncClient", _mock_http_response(200, RSS_3_ITEMS)):
        results = asyncio.run(NewznabClient(indexer).search("batman 1"))

    assert len(results) == 3
    assert all(r.source_type == "usenet" for r in results)
    assert all(r.indexer_id == 1 for r in results)


def test_newznab_403_returns_empty_list():
    from pullbox.clients.newznab import NewznabClient

    indexer = _make_indexer(1, "NZBGeek")

    with patch(
        "pullbox.clients.newznab.httpx.AsyncClient", _mock_http_response(403, b"Forbidden")
    ):
        results = asyncio.run(NewznabClient(indexer).search("batman 1"))

    assert results == []


def test_newznab_fallback_to_cat_7000():
    """Empty cat=7030 response triggers retry with cat=7000 returning 2 items."""
    from pullbox.clients.newznab import NewznabClient

    indexer = _make_indexer(1, "NZBGeek")

    responses = [
        MagicMock(status_code=200, content=RSS_EMPTY),
        MagicMock(status_code=200, content=RSS_2_ITEMS),
    ]
    call_count = 0

    async def fake_get(url, params=None):
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    mock_client = AsyncMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "pullbox.clients.newznab.httpx.AsyncClient", MagicMock(return_value=mock_client)
    ):
        results = asyncio.run(NewznabClient(indexer).search("batman annual"))

    assert len(results) == 2
    assert call_count == 2


# ── ProwlarrClient (JSON search API) ─────────────────────────────────────────

PROWLARR_JSON = [
    {
        "title": "Batman 001",
        "guid": "p-1",
        "downloadUrl": "http://dl.test/1.nzb",
        "protocol": "usenet",
        "size": 1024,
        "publishDate": "2024-01-01T00:00:00Z",
    },
    {
        "title": "Batman 001 (torrent)",
        "guid": "p-2",
        "magnetUrl": "magnet:?xt=urn:btih:abc",
        "protocol": "torrent",
        "size": 2048,
        "seeders": 12,
        "publishDate": "2024-01-02T00:00:00Z",
    },
    {"title": "No download url", "guid": "p-3", "protocol": "torrent"},  # skipped
]


def _mock_json_response(status: int, payload: object) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json = MagicMock(return_value=payload)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_client)


def test_prowlarr_search_parses_json():
    from pullbox.clients.prowlarr import ProwlarrClient

    indexer = _make_indexer(1, "Prowlarr", type="prowlarr")

    with patch(
        "pullbox.clients.prowlarr.httpx.AsyncClient", _mock_json_response(200, PROWLARR_JSON)
    ):
        results = asyncio.run(ProwlarrClient(indexer).search("batman 1"))

    # The release with no download URL is dropped.
    assert len(results) == 2
    by_guid = {r.guid: r for r in results}
    assert by_guid["p-1"].source_type == "usenet"
    assert by_guid["p-1"].download_url == "http://dl.test/1.nzb"
    assert by_guid["p-2"].source_type == "torrent"
    assert by_guid["p-2"].download_url == "magnet:?xt=urn:btih:abc"
    assert by_guid["p-2"].seeders == 12


def test_prowlarr_403_returns_empty_list():
    from pullbox.clients.prowlarr import ProwlarrClient

    indexer = _make_indexer(1, "Prowlarr", type="prowlarr")

    with patch(
        "pullbox.clients.prowlarr.httpx.AsyncClient", _mock_json_response(403, {})
    ):
        results = asyncio.run(ProwlarrClient(indexer).search("batman 1"))

    assert results == []


# ── JackettClient (Torznab XML) ──────────────────────────────────────────────

TORZNAB_2_ITEMS = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed" version="2.0">
  <channel>
    <item>
      <title>Batman 001</title>
      <guid>t-1</guid>
      <enclosure url="http://dl.test/1.torrent" length="1024" type="application/x-bittorrent"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="42"/>
    </item>
    <item>
      <title>Batman 001 (magnet, no guid)</title>
      <enclosure url="magnet:?xt=urn:btih:def" length="2048" type="application/x-bittorrent"/>
      <torznab:attr name="seeders" value="7"/>
    </item>
  </channel>
</rss>
"""


def test_jackett_search_parses_torznab():
    from pullbox.clients.jackett import JackettClient

    indexer = _make_indexer(1, "Jackett", type="jackett")

    with patch(
        "pullbox.clients.jackett.httpx.AsyncClient", _mock_http_response(200, TORZNAB_2_ITEMS)
    ):
        results = asyncio.run(JackettClient(indexer).search("batman 1"))

    assert len(results) == 2
    assert all(r.source_type == "torrent" for r in results)

    first, second = results
    assert first.guid == "t-1"
    assert first.seeders == 42
    # Item without <guid> falls back to the download URL.
    assert second.guid == "magnet:?xt=urn:btih:def"
    assert second.seeders == 7


# ── Step 6.3 — build_search_queries ──────────────────────────────────────────


def test_build_queries_numeric_issue():
    result = build_search_queries("Teenage Mutant Ninja Turtles", "2")
    assert result == [
        "teenage mutant ninja turtles 2",
        "teenage mutant ninja turtles 02",
        "teenage mutant ninja turtles 002",
    ]


def test_build_queries_alphanumeric_issue():
    result = build_search_queries("Amazing Spider-Man", "1A")
    assert result == ["amazing spider-man 1a"]


def test_build_queries_deduplicates_2digit_pad():
    """issue_number='02' already equals the 2-digit pad — must not appear twice."""
    result = build_search_queries("Batman", "02")
    assert result.count("batman 02") == 1
    assert "batman 002" in result
    assert len(result) == 2


def test_build_queries_deduplicates_3digit_pad():
    """issue_number='150' — both zfill(2) and zfill(3) equal bare, so 1 unique entry."""
    result = build_search_queries("X-Men", "150")
    assert result == ["x-men 150"]


# ── Step 6.4 — fan_out_search ─────────────────────────────────────────────────


def _make_client_returning(results: list[SearchResult]) -> MagicMock:
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=results)
    return mock_client


def test_fan_out_returns_four_distinct_results():
    """Two enabled + one disabled newznab indexer; each enabled one returns 2 results."""
    idx1 = _make_indexer(1, "A", priority=10)
    idx2 = _make_indexer(2, "B", priority=20)
    idx3 = _make_indexer(3, "C", enabled=False)

    r1 = _make_result("guid-1", indexer_id=1)
    r2 = _make_result("guid-2", indexer_id=1)
    r3 = _make_result("guid-3", indexer_id=2)
    r4 = _make_result("guid-4", indexer_id=2)

    clients = {1: _make_client_returning([r1, r2]), 2: _make_client_returning([r3, r4])}

    def _fake_newznab(indexer):
        return clients[indexer.id]

    with patch("pullbox.clients.newznab.NewznabClient", side_effect=_fake_newznab):
        results = asyncio.run(fan_out_search(_make_issue(), _make_series(), [idx1, idx2, idx3]))

    assert len(results) == 4
    # Disabled indexer (id=3) never had a client built for it
    returned_indexer_ids = {r.indexer_id for r in results}
    assert 3 not in returned_indexer_ids


def test_fan_out_deduplicates_by_guid():
    """5 total results across two clients with one shared GUID → 4 unique after dedup."""
    idx1 = _make_indexer(1, "A", priority=10)
    idx2 = _make_indexer(2, "B", priority=20)

    r1 = _make_result("guid-1")
    r2 = _make_result("guid-2")
    r3 = _make_result("guid-3")
    r4 = _make_result("guid-4")
    r_dup = _make_result("guid-1")  # duplicate of r1

    clients = {
        1: _make_client_returning([r1, r2, r3]),
        2: _make_client_returning([r4, r_dup]),
    }

    def _fake_newznab(indexer):
        return clients[indexer.id]

    with patch("pullbox.clients.newznab.NewznabClient", side_effect=_fake_newznab):
        results = asyncio.run(fan_out_search(_make_issue(), _make_series(), [idx1, idx2]))

    guids = [r.guid for r in results]
    assert len(guids) == 4
    assert guids.count("guid-1") == 1


def test_fan_out_dispatches_each_type_to_its_client():
    """Each indexer type is routed to the matching client; unknown types are skipped."""
    idxs = [
        _make_indexer(1, "P", type="prowlarr"),
        _make_indexer(2, "J", type="jackett"),
        _make_indexer(3, "H", type="nzbhydra2"),
        _make_indexer(4, "N", type="newznab"),
        _make_indexer(5, "X", type="unknowntype"),
    ]

    def _fake_newznab(indexer):
        assert indexer.type in ("newznab", "nzbhydra2")
        return _make_client_returning([_make_result(f"guid-{indexer.type}")])

    def _fake_prowlarr(indexer):
        assert indexer.type == "prowlarr"
        return _make_client_returning([_make_result("guid-prowlarr")])

    def _fake_jackett(indexer):
        assert indexer.type == "jackett"
        return _make_client_returning([_make_result("guid-jackett")])

    with (
        patch("pullbox.clients.newznab.NewznabClient", side_effect=_fake_newznab) as mock_nz,
        patch("pullbox.clients.prowlarr.ProwlarrClient", side_effect=_fake_prowlarr) as mock_pr,
        patch("pullbox.clients.jackett.JackettClient", side_effect=_fake_jackett) as mock_ja,
    ):
        results = asyncio.run(fan_out_search(_make_issue(), _make_series(), idxs))

    # newznab + nzbhydra2 share NewznabClient; prowlarr/jackett get their own; unknown skipped
    assert mock_nz.call_count == 2
    assert mock_pr.call_count == 1
    assert mock_ja.call_count == 1
    guids = {r.guid for r in results}
    assert guids == {"guid-newznab", "guid-nzbhydra2", "guid-prowlarr", "guid-jackett"}


# ── Step 6.5 — score_results ─────────────────────────────────────────────────


def test_score_results_sorted_descending():
    now = datetime.now(tz=timezone.utc)

    r_high = _make_result(
        "g1",
        title="Batman 5 CBZ (2024)",
        source_type="usenet",
        published_at=now - timedelta(days=5),
    )
    r_low = _make_result(
        "g2",
        title="Some Other Comic",
        source_type="torrent",
        published_at=now - timedelta(days=200),
    )
    r_mid = _make_result(
        "g3",
        title="Batman 5 PDF",
        source_type="torrent",
        published_at=now - timedelta(days=60),
    )

    scored = score_results([r_high, r_low, r_mid], "Batman", "5")

    assert scored[0].guid == "g1"
    scores = [r.score for r in scored]
    assert scores == sorted(scores, reverse=True)


def test_score_usenet_beats_no_title_match():
    """A result with series title + CBZ + usenet + recent beats one missing the title."""
    now = datetime.now(tz=timezone.utc)

    best = _make_result(
        "g1",
        title="Batman 1 CBZ",
        source_type="usenet",
        published_at=now - timedelta(days=1),
    )
    worse = _make_result(
        "g2",
        title="Unrelated Comic 1",
        source_type="torrent",
        published_at=now - timedelta(days=1),
    )

    scored = score_results([worse, best], "Batman", "1")
    assert scored[0].guid == "g1"
    assert scored[0].score > scored[1].score


def test_score_age_buckets():
    now = datetime.now(tz=timezone.utc)

    recent = _make_result("g1", title="Batman 1", published_at=now - timedelta(days=10))
    mid_age = _make_result("g2", title="Batman 1", published_at=now - timedelta(days=60))
    old = _make_result("g3", title="Batman 1", published_at=now - timedelta(days=200))
    no_date = _make_result("g4", title="Batman 1", published_at=None)

    scored = score_results([old, mid_age, recent, no_date], "Batman", "1")
    score_map = {r.guid: r.score for r in scored}

    assert score_map["g1"] > score_map["g2"] > score_map["g3"]
    assert score_map["g3"] == score_map["g4"]


def test_score_sets_score_on_result_objects():
    r = _make_result("g1", title="Batman 1 CBZ", source_type="usenet")
    score_results([r], "Batman", "1")
    assert r.score > 0
