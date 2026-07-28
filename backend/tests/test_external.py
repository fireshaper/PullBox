"""Tests for the read-only companion feed at /api/external/*.

Covers the token gate, the library-relative path key companions join on, the
`since` filter's arc-enrichment case, and the invariant that none of these
handlers call the metadata provider.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import pullbox.deps as deps
from pullbox.main import app
from pullbox.models import GeneralSettings, Issue, Series, StoryArc
from pullbox.services.general import relative_to_library

TOKEN = "test-token-value"
AUTH = {"X-PullBox-Token": TOKEN}


# ── relative_to_library ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("file_path", "library", "expected"),
    [
        ("/comics/DC/Batman/Batman 001.cbz", "/comics", "DC/Batman/Batman 001.cbz"),
        # Trailing separator on the root must not produce a leading slash.
        ("/comics/DC/x.cbz", "/comics/", "DC/x.cbz"),
        # Windows separators normalise to forward slashes both sides.
        ("C:\\Comics\\DC\\x.cbz", "C:\\Comics", "DC/x.cbz"),
        # Mixed flavours: a Docker-written value read on a Windows box.
        ("C:/Comics/DC/x.cbz", "C:\\Comics", "DC/x.cbz"),
        # Case-insensitive root match, original casing preserved in the result.
        ("c:\\comics\\DC\\X.cbz", "C:\\Comics", "DC/X.cbz"),
    ],
)
def test_relative_to_library_normalizes(file_path, library, expected):
    assert relative_to_library(file_path, library) == expected


@pytest.mark.parametrize(
    ("file_path", "library"),
    [
        ("/elsewhere/x.cbz", "/comics"),
        # A sibling folder sharing a prefix is not inside the library.
        ("/comics-old/x.cbz", "/comics"),
        # The root itself has no tail below it.
        ("/comics", "/comics"),
    ],
)
def test_relative_to_library_rejects_outside_paths(file_path, library):
    assert relative_to_library(file_path, library) is None


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PULLBOX_EXTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("PULLBOX_LIBRARY_PATH", "/comics")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_token_client(monkeypatch):
    monkeypatch.setenv("PULLBOX_EXTERNAL_API_TOKEN", "")
    with TestClient(app) as c:
        yield c


def _seed(*, arcs_synced_at=None, updated_at=None):
    """Create one series, one arc, and two issues (one with a file, one without).

    Sync, driving async SQLAlchemy through ``asyncio.run`` on a fresh loop — the
    same pattern the other router tests use, so seeding and the TestClient share
    one SQLite file rather than one event loop. ``AsyncSessionLocal`` is imported
    inside the function because ``init_db`` only assigns it once the app's
    lifespan has run.
    """

    async def _run():
        from pullbox.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            series = Series(
                metron_id="s1",
                comicvine_id="4050-1",
                title="Batman",
                publisher="DC",
                start_year=2016,
            )
            db.add(series)
            await db.flush()

            arc = StoryArc(
                metron_id="a1",
                comicvine_id="4045-1",
                name="Court of Owls",
                publisher="DC",
                count_of_issue_appearances=12,
            )
            db.add(arc)
            await db.flush()

            held = Issue(
                series_id=series.id,
                metron_id="i1",
                comicvine_id="4000-1",
                issue_number="1",
                title="Knife Trick",
                status="downloaded",
                file_path="/comics/DC/Batman (2016)/Batman 001.cbz",
                arcs_synced_at=arcs_synced_at,
            )
            if updated_at is not None:
                held.updated_at = updated_at
            held.arcs.append(arc)
            db.add(held)

            # No file on disk — nothing for a companion to match, so it must not appear.
            db.add(
                Issue(
                    series_id=series.id,
                    metron_id="i2",
                    issue_number="2",
                    status="wanted",
                    file_path=None,
                )
            )
            await db.commit()
            return series.id, arc.id

    return asyncio.run(_run())


def _set_library_override(path: str) -> None:
    async def _run():
        from pullbox.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            db.add(GeneralSettings(library_path=path))
            await db.commit()

    asyncio.run(_run())


# ── token gate ────────────────────────────────────────────────────────────────


def test_library_requires_token(client):
    assert client.get("/api/external/library").status_code == 401


def test_library_rejects_wrong_token(client):
    resp = client.get("/api/external/library", headers={"X-PullBox-Token": "nope"})
    assert resp.status_code == 401


def test_routes_disabled_when_no_token_configured(no_token_client):
    """Fail closed: an unconfigured token disables the feed rather than opening it."""
    resp = no_token_client.get("/api/external/library", headers=AUTH)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


# ── library feed ──────────────────────────────────────────────────────────────


def test_library_feed_returns_issues_with_files(client):
    _seed()
    resp = client.get("/api/external/library", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()

    # Only the issue with a file_path is included.
    assert data["total"] == 1
    assert data["library_path"] == "/comics"

    item = data["items"][0]
    assert item["path_rel"] == "DC/Batman (2016)/Batman 001.cbz"
    assert item["metron_id"] == "i1"
    assert item["comicvine_id"] == "4000-1"
    assert item["series"]["title"] == "Batman"
    assert item["series"]["metron_id"] == "s1"

    assert len(item["arcs"]) == 1
    arc = item["arcs"][0]
    assert arc["name"] == "Court of Owls"
    assert arc["metron_id"] == "a1"
    # The arc's true cross-series size, not the count PullBox owns — this is what
    # a companion classifies one-shots/minis on.
    assert arc["count_of_issue_appearances"] == 12


def test_path_rel_is_null_for_files_outside_the_library(client):
    _seed()
    _set_library_override("/somewhere/else")

    data = client.get("/api/external/library", headers=AUTH).json()
    # The DB override wins over the config value, and the file now falls outside it.
    assert data["library_path"] == "/somewhere/else"
    assert data["items"][0]["path_rel"] is None
    # file_path is still there so a companion can fall back to absolute matching.
    assert data["items"][0]["file_path"].endswith("Batman 001.cbz")


def test_since_matches_arc_enrichment_not_just_updated_at(client):
    """Arc enrichment stamps arcs_synced_at only; filtering on updated_at alone
    would drop exactly the issues that just gained their arcs."""
    old = datetime(2026, 1, 1, 12, 0, 0)
    recent = datetime(2026, 6, 1, 12, 0, 0)
    _seed(updated_at=old, arcs_synced_at=recent)

    cutoff = (recent - timedelta(days=1)).isoformat()
    data = client.get(f"/api/external/library?since={cutoff}", headers=AUTH).json()
    assert data["total"] == 1

    after = (recent + timedelta(days=1)).isoformat()
    data = client.get(f"/api/external/library?since={after}", headers=AUTH).json()
    assert data["total"] == 0


def test_library_feed_paginates(client):
    _seed()
    data = client.get("/api/external/library?limit=1&offset=1", headers=AUTH).json()
    assert data["total"] == 1  # total is the unpaged count
    assert data["items"] == []


# ── arc detail ────────────────────────────────────────────────────────────────


def test_arc_detail_returns_local_members(client):
    _, arc_id = _seed()
    resp = client.get(f"/api/external/arcs/{arc_id}", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()

    assert data["name"] == "Court of Owls"
    assert data["count_of_issue_appearances"] == 12
    assert len(data["members"]) == 1
    member = data["members"][0]
    assert member["series_title"] == "Batman"
    assert member["issue_number"] == "1"
    assert member["path_rel"] == "DC/Batman (2016)/Batman 001.cbz"


def test_arc_detail_404s_for_unknown_arc(client):
    _seed()
    assert client.get("/api/external/arcs/9999", headers=AUTH).status_code == 404


def test_external_routes_never_call_the_metadata_provider(client, monkeypatch):
    """The load-bearing invariant: a companion re-syncing on every scan must not
    be able to spend the shared Metron budget."""
    _, arc_id = _seed()

    def explode(_settings):
        raise AssertionError("external routes must not build a metadata provider")

    monkeypatch.setattr(deps, "build_metadata_provider", explode)

    assert client.get("/api/external/library", headers=AUTH).status_code == 200
    assert client.get(f"/api/external/arcs/{arc_id}", headers=AUTH).status_code == 200
