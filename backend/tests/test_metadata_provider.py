"""Tests for the provider abstraction: CompositeProvider routing/fallback and the
ComicVineProvider normalization adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pullbox.clients.comicvine import ComicVineError
from pullbox.clients.metadata import ComicVineProvider, CompositeProvider, ids_for
from pullbox.clients.metron import MetronError


def _fake_source(name: str) -> AsyncMock:
    src = AsyncMock()
    src.source = name
    return src


# ── ids_for helper ────────────────────────────────────────────────────────────


def test_ids_for_reads_dict_and_object():
    assert ids_for({"metron_id": "m1", "comicvine_id": "c1"}) == {
        "metron_id": "m1",
        "comicvine_id": "c1",
    }

    class Row:
        metron_id = "m2"
        comicvine_id = None

    assert ids_for(Row()) == {"metron_id": "m2", "comicvine_id": None}


# ── search_series fallback ────────────────────────────────────────────────────


async def test_search_falls_back_on_empty_primary():
    metron = _fake_source("metron")
    metron.search_series.return_value = []
    comicvine = _fake_source("comicvine")
    comicvine.search_series.return_value = [{"metron_id": None, "comicvine_id": "c1"}]

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    results = await provider.search_series("x")

    assert results == [{"metron_id": None, "comicvine_id": "c1"}]
    metron.search_series.assert_awaited_once()
    comicvine.search_series.assert_awaited_once()


async def test_search_falls_back_on_primary_error():
    metron = _fake_source("metron")
    metron.search_series.side_effect = MetronError("boom")
    comicvine = _fake_source("comicvine")
    comicvine.search_series.return_value = [{"metron_id": None, "comicvine_id": "c1"}]

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    results = await provider.search_series("x")
    assert results[0]["comicvine_id"] == "c1"


async def test_search_returns_primary_when_non_empty():
    metron = _fake_source("metron")
    metron.search_series.return_value = [{"metron_id": "m1", "comicvine_id": None}]
    comicvine = _fake_source("comicvine")

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    results = await provider.search_series("x")
    assert results[0]["metron_id"] == "m1"
    comicvine.search_series.assert_not_awaited()


# ── get_weekly_releases fallback ──────────────────────────────────────────────


async def test_weekly_releases_falls_back_on_error():
    metron = _fake_source("metron")
    metron.get_weekly_releases.side_effect = MetronError("down")
    comicvine = _fake_source("comicvine")
    comicvine.get_weekly_releases.return_value = [{"metron_id": None}]

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    releases = await provider.get_weekly_releases("a", "b")
    assert releases == [{"metron_id": None}]


# ── id-based routing ──────────────────────────────────────────────────────────


async def test_get_volume_routes_to_metron_by_metron_id():
    metron = _fake_source("metron")
    metron.get_volume.return_value = {"metron_id": "m1"}
    comicvine = _fake_source("comicvine")

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    result = await provider.get_volume(metron_id="m1", comicvine_id="c1")

    assert result["metron_id"] == "m1"
    metron.get_volume.assert_awaited_once_with("m1")
    comicvine.get_volume.assert_not_awaited()


async def test_get_volume_routes_to_comicvine_when_only_cv_id():
    metron = _fake_source("metron")
    comicvine = _fake_source("comicvine")
    comicvine.get_volume.return_value = {"comicvine_id": "c1"}

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    result = await provider.get_volume(metron_id=None, comicvine_id="c1")

    assert result["comicvine_id"] == "c1"
    comicvine.get_volume.assert_awaited_once_with("c1")
    metron.get_volume.assert_not_awaited()


async def test_get_volume_falls_back_to_other_id_on_error():
    metron = _fake_source("metron")
    metron.get_volume.side_effect = MetronError("missing")
    comicvine = _fake_source("comicvine")
    comicvine.get_volume.return_value = {"comicvine_id": "c1"}

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    result = await provider.get_volume(metron_id="m1", comicvine_id="c1")

    assert result["comicvine_id"] == "c1"
    metron.get_volume.assert_awaited_once_with("m1")
    comicvine.get_volume.assert_awaited_once_with("c1")


async def test_id_based_raises_when_no_usable_id():
    metron = _fake_source("metron")
    provider = CompositeProvider(metron=metron, primary="metron")
    with pytest.raises(ValueError):
        await provider.get_volume(metron_id=None, comicvine_id=None)


async def test_close_closes_both_sources():
    metron = _fake_source("metron")
    comicvine = _fake_source("comicvine")
    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    await provider.close()
    metron.close.assert_awaited_once()
    comicvine.close.assert_awaited_once()


def test_requires_at_least_one_source():
    with pytest.raises(ValueError):
        CompositeProvider(metron=None, comicvine=None)


# ── ComicVineProvider normalization ───────────────────────────────────────────


async def test_comicvine_provider_normalizes_series_and_issue():
    cv_client = AsyncMock()
    cv_client.search_series.return_value = [{"comicvine_id": "c1", "title": "X"}]
    cv_client.get_volume.return_value = {"comicvine_id": "c1", "title": "X"}
    cv_client.get_issues.return_value = [{"comicvine_id": "i1", "issue_number": "1"}]

    provider = ComicVineProvider(cv_client)

    series = (await provider.search_series("x"))[0]
    assert series["metron_id"] is None and series["source"] == "comicvine"

    volume = await provider.get_volume("c1")
    assert volume["metron_id"] is None and volume["comicvine_id"] == "c1"

    issue = (await provider.get_issues("c1"))[0]
    assert issue["metron_id"] is None and issue["comicvine_id"] == "i1"


async def test_comicvine_provider_normalizes_nested_records():
    cv_client = AsyncMock()
    cv_client.get_issue.return_value = {
        "comicvine_id": "i1",
        "story_arcs": [{"comicvine_id": "a1", "name": "Arc"}],
    }
    cv_client.get_story_arc.return_value = {
        "comicvine_id": "a1",
        "issues": [{"comicvine_id": "i1", "name": "One"}],
    }
    cv_client.get_weekly_releases.return_value = [
        {"comicvine_id": "i1", "series": {"comicvine_id": "s1", "title": "S"}}
    ]

    provider = ComicVineProvider(cv_client)

    issue = await provider.get_issue("i1")
    assert issue["metron_id"] is None
    assert issue["story_arcs"][0]["metron_id"] is None

    arc = await provider.get_story_arc("a1")
    assert arc["metron_id"] is None
    assert arc["issues"][0]["metron_id"] is None

    rel = (await provider.get_weekly_releases("a", "b"))[0]
    assert rel["metron_id"] is None
    assert rel["series"]["metron_id"] is None


async def test_composite_falls_back_on_comicvine_error_from_metron_primary():
    """A ComicVineError raised by the fallback source is also handled by the tuple."""
    metron = _fake_source("metron")
    metron.search_series.side_effect = MetronError("x")
    comicvine = _fake_source("comicvine")
    comicvine.search_series.side_effect = ComicVineError("y")

    provider = CompositeProvider(metron=metron, comicvine=comicvine, primary="metron")
    with pytest.raises(ComicVineError):
        await provider.search_series("q")
