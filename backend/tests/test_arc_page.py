"""Tests for the Story Arcs page: the /api/arcs endpoints and arc member resolution.

The mocked provider mirrors the dual-id record shapes ``CompositeProvider`` returns:
Metron ids are primary, ComicVine ids are cross-references, and a single-issue detail
now carries the ``series`` block that lets an unowned arc member become a real row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pullbox.clients.metron import MetronRateLimitError
from pullbox.deps import get_metadata_provider
from pullbox.main import app

# ── Fixture data ─────────────────────────────────────────────────────────────

FAKE_VOLUME = {
    "metron_id": "m99001",
    "comicvine_id": "99001",
    "title": "Batman",
    "publisher": "DC Comics",
    "start_year": 2016,
    "cover_url": None,
    "description": "desc",
    "issue_count": 1,
}

FAKE_ISSUES = [
    {
        "metron_id": "m555001",
        "comicvine_id": "555001",
        "issue_number": "1",
        "title": "One",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "description": None,
    },
]

# Detail for the owned issue (drives arc enrichment on series sync) plus the two
# arc members PullBox does not own.
ISSUE_DETAILS = {
    "m555001": {
        "metron_id": "m555001",
        "comicvine_id": "555001",
        "issue_number": "1",
        "title": "One",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "series": {
            "metron_id": "m99001",
            "comicvine_id": "99001",
            "title": "Batman",
            "publisher": "DC Comics",
            "start_year": 2016,
        },
        "story_arcs": [
            {"metron_id": "m4045111", "comicvine_id": "4045111", "name": "Dark Nights"}
        ],
    },
    "m888999": {
        "metron_id": "m888999",
        "comicvine_id": "888999",
        "issue_number": "4",
        "title": "Elsewhere",
        "cover_date": "2016-03-01",
        "store_date": "2016-03-02",
        "cover_url": "https://example.com/i4.jpg",
        "series": {
            "metron_id": "m77000",
            "comicvine_id": "77000",
            "title": "Detective Comics",
            "publisher": "DC Comics",
            "start_year": 2016,
        },
        "story_arcs": [
            {"metron_id": "m4045111", "comicvine_id": "4045111", "name": "Dark Nights"}
        ],
    },
    "m888777": {
        "metron_id": "m888777",
        "comicvine_id": "888777",
        "issue_number": "9",
        "title": "Nowhere",
        "cover_date": "2016-04-01",
        "store_date": "2016-04-06",
        "cover_url": None,
        "series": {
            "metron_id": "m77000",
            "comicvine_id": "77000",
            "title": "Detective Comics",
            "publisher": "DC Comics",
            "start_year": 2016,
        },
        "story_arcs": [],
    },
}

ARC_DETAIL = {
    "metron_id": "m4045111",
    "comicvine_id": "4045111",
    "name": "Dark Nights",
    "publisher": "DC Comics",
    "cover_url": "https://example.com/arc.jpg",
    "description": "A crossover.",
    "count_of_issue_appearances": 3,
    "issues": [
        {"metron_id": "m555001", "comicvine_id": "555001", "name": "One",
         "site_detail_url": "https://metron/i/1"},
        {"metron_id": "m888999", "comicvine_id": "888999", "name": "Elsewhere",
         "site_detail_url": "https://metron/i/2"},
        {"metron_id": "m888777", "comicvine_id": "888777", "name": "Nowhere",
         "site_detail_url": "https://metron/i/3"},
    ],
}


@pytest.fixture
def provider():
    mock = AsyncMock()
    mock.get_volume.return_value = FAKE_VOLUME
    mock.get_issues.return_value = FAKE_ISSUES
    mock.get_issue.side_effect = lambda **kw: ISSUE_DETAILS[
        kw.get("metron_id") or kw.get("comicvine_id")
    ]
    mock.get_story_arc.return_value = ARC_DETAIL

    async def _override():
        yield mock

    app.dependency_overrides[get_metadata_provider] = _override
    try:
        yield mock
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    """Stop enqueued jobs from actually running a search during these tests."""
    monkeypatch.setattr("pullbox.routers.arcs.run_job_now", AsyncMock())


def _seed_arc(client) -> tuple[int, int]:
    """Add + sync a series so its issue's arc is discovered. Returns (series_id, arc_id)."""
    add = client.post("/api/series/", json={"metron_id": "m99001"})
    assert add.status_code == 201
    series_id = add.json()["id"]
    assert client.post(f"/api/series/{series_id}/sync-issues").status_code == 200

    arcs = client.get("/api/arcs").json()
    assert len(arcs) == 1
    return series_id, arcs[0]["id"]


# ── Listing ──────────────────────────────────────────────────────────────────


def test_list_arcs_reports_local_holdings(client, provider):
    series_id, arc_id = _seed_arc(client)

    arcs = client.get("/api/arcs").json()
    arc = arcs[0]
    assert arc["name"] == "Dark Nights"
    assert arc["subscribed"] is False
    # One issue tracked, from one series; nothing downloaded yet.
    assert (arc["owned"], arc["series_count"], arc["downloaded"]) == (1, 1, 0)
    # The arc's true size is unknown until its detail has been fetched.
    assert arc["total"] is None
    assert arc["detail_synced_at"] is None
    assert series_id  # seeded series is the source of the arc


def test_list_arcs_filters_by_name_and_subscription(client, provider):
    _, arc_id = _seed_arc(client)

    assert len(client.get("/api/arcs?q=dark").json()) == 1
    assert client.get("/api/arcs?q=zzz").json() == []
    assert client.get("/api/arcs?subscribed=true").json() == []

    client.patch(f"/api/arcs/{arc_id}", json={"subscribed": True})
    assert len(client.get("/api/arcs?subscribed=true").json()) == 1


def test_get_arc_lists_tracked_members(client, provider):
    series_id, arc_id = _seed_arc(client)

    detail = client.get(f"/api/arcs/{arc_id}").json()
    assert detail["name"] == "Dark Nights"
    assert len(detail["issues"]) == 1
    member = detail["issues"][0]
    assert member["series_title"] == "Batman"
    assert member["issue_number"] == "1"
    assert member["series_id"] == series_id
    assert member["has_file"] is False


def test_get_arc_404s_for_unknown_id(client, provider):
    assert client.get("/api/arcs/9999").status_code == 404


def test_reading_an_arc_costs_no_provider_call(client, provider):
    _, arc_id = _seed_arc(client)
    provider.get_story_arc.reset_mock()

    client.get("/api/arcs")
    client.get(f"/api/arcs/{arc_id}")

    assert provider.get_story_arc.call_count == 0


# ── Subscription ─────────────────────────────────────────────────────────────


def test_patch_toggles_subscription_flags(client, provider):
    _, arc_id = _seed_arc(client)

    body = client.patch(
        f"/api/arcs/{arc_id}", json={"subscribed": True, "auto_download": True}
    ).json()
    assert (body["subscribed"], body["auto_download"]) == (True, True)

    # Partial update leaves the untouched flag alone.
    body = client.patch(f"/api/arcs/{arc_id}", json={"subscribed": False}).json()
    assert (body["subscribed"], body["auto_download"]) == (False, True)


def test_subscribing_does_not_call_the_provider(client, provider):
    _, arc_id = _seed_arc(client)
    provider.get_story_arc.reset_mock()

    client.patch(f"/api/arcs/{arc_id}", json={"subscribed": True})

    assert provider.get_story_arc.call_count == 0


# ── Sync: resolving missing members into local rows ──────────────────────────


def test_sync_creates_wanted_rows_for_missing_members(client, provider):
    _, arc_id = _seed_arc(client)

    result = client.post(f"/api/arcs/{arc_id}/sync").json()
    assert result["members"] == 3
    assert result["in_library"] == 1
    assert result["added"] == 2
    assert result["enqueued"] == 0  # download not requested
    assert result["remaining"] == 0

    detail = client.get(f"/api/arcs/{arc_id}").json()
    assert len(detail["issues"]) == 3
    assert detail["total"] == 3
    # The two new issues are wanted and hang off a newly-created series.
    added = [i for i in detail["issues"] if i["series_title"] == "Detective Comics"]
    assert len(added) == 2
    assert {i["status"] for i in added} == {"wanted"}
    assert {i["issue_number"] for i in added} == {"4", "9"}


def test_sync_is_idempotent(client, provider):
    _, arc_id = _seed_arc(client)
    client.post(f"/api/arcs/{arc_id}/sync")
    provider.get_issue.reset_mock()

    second = client.post(f"/api/arcs/{arc_id}/sync").json()
    assert second["added"] == 0
    assert second["in_library"] == 3
    # Everything is local now, so no per-member lookups are spent.
    assert provider.get_issue.call_count == 0


def test_sync_respects_the_lookup_budget(client, provider):
    _, arc_id = _seed_arc(client)

    result = client.post(f"/api/arcs/{arc_id}/sync?budget=1").json()
    assert result["added"] == 1
    assert result["remaining"] == 1
    assert "left for the next sync" in result["message"]

    # The leftover member is picked up by the next run.
    assert client.post(f"/api/arcs/{arc_id}/sync?budget=5").json()["added"] == 1


def test_sync_with_download_enqueues_new_issues(client, provider):
    _, arc_id = _seed_arc(client)

    result = client.post(f"/api/arcs/{arc_id}/sync?download=true").json()
    assert result["added"] == 2
    assert result["enqueued"] == 2

    queue = client.get("/api/queue/").json()
    assert len(queue) == 2


def test_sync_stops_on_a_rate_limit_and_keeps_what_it_got(client, provider):
    _, arc_id = _seed_arc(client)

    calls = {"n": 0}

    def _fail_after_one(**kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise MetronRateLimitError("burst budget exhausted")
        return ISSUE_DETAILS[kw.get("metron_id") or kw.get("comicvine_id")]

    provider.get_issue.side_effect = _fail_after_one

    result = client.post(f"/api/arcs/{arc_id}/sync").json()
    assert result["rate_limited"] is True
    assert result["added"] == 1  # the one that resolved before the limit is kept
    assert client.get(f"/api/arcs/{arc_id}").json()["issues"].__len__() == 2


def test_sync_survives_a_member_that_cannot_be_stored(client, provider):
    """A duplicate id would trip the issues' UNIQUE constraint; the savepoint per
    member means the rest of the arc still resolves instead of the flush error
    poisoning the session."""
    _, arc_id = _seed_arc(client)
    # Both unowned members now claim the same ids — the second insert must fail.
    provider.get_issue.side_effect = lambda **kw: {
        **ISSUE_DETAILS["m888999"],
        "issue_number": ISSUE_DETAILS[kw.get("metron_id") or kw.get("comicvine_id")][
            "issue_number"
        ],
    }

    result = client.post(f"/api/arcs/{arc_id}/sync").json()
    assert result["added"] == 1
    assert result["failed"] == 1
    # The one that worked is persisted and readable.
    assert len(client.get(f"/api/arcs/{arc_id}").json()["issues"]) == 2


def test_sync_skips_a_member_with_no_series(client, provider):
    _, arc_id = _seed_arc(client)
    provider.get_issue.side_effect = lambda **kw: {
        **ISSUE_DETAILS[kw.get("metron_id") or kw.get("comicvine_id")],
        "series": None,
    }

    result = client.post(f"/api/arcs/{arc_id}/sync").json()
    assert result["added"] == 0
    assert result["failed"] == 2


# ── Download missing ─────────────────────────────────────────────────────────


def test_download_missing_queues_every_undownloaded_member(client, provider):
    _, arc_id = _seed_arc(client)
    client.post(f"/api/arcs/{arc_id}/sync")

    result = client.post(f"/api/arcs/{arc_id}/download-missing").json()
    # All three tracked members are un-downloaded (the seeded one included).
    assert result["enqueued"] == 3
    assert len(client.get("/api/queue/").json()) == 3


def test_download_missing_skips_skipped_issues(client, provider):
    series_id, arc_id = _seed_arc(client)
    issues = client.get(f"/api/series/{series_id}/issues").json()
    client.post(f"/api/issues/{issues[0]['id']}/skip")

    client.post(f"/api/arcs/{arc_id}/sync")
    result = client.post(f"/api/arcs/{arc_id}/download-missing").json()

    # The skipped issue is left alone; only the two discovered members queue up.
    assert result["enqueued"] == 2


def test_download_missing_needs_no_provider_call(client, provider):
    _, arc_id = _seed_arc(client)
    provider.get_story_arc.reset_mock()
    provider.get_issue.reset_mock()

    client.post(f"/api/arcs/{arc_id}/download-missing")

    assert provider.get_story_arc.call_count == 0
    assert provider.get_issue.call_count == 0
