"""Tests for Phase 5: Indexer Management API (steps 5.1–5.3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app

NEWZNAB_PAYLOAD = {
    "name": "NZBGeek",
    "type": "newznab",
    "url": "https://api.nzbgeek.info",
    "api_key": "abc123",
    "priority": 10,
}

PROWLARR_PAYLOAD = {
    "name": "Prowlarr",
    "type": "prowlarr",
    "url": "http://localhost:9696",
    "priority": 50,
}

VALID_CAPS_XML = b"<caps><server version='1.1'/></caps>"
WRONG_ROOT_XML = b"<error code='100' description='Missing parameter'/>"


def _make_httpx_mock(status_code: int, content: bytes):
    """Build a mock that stands in for httpx.AsyncClient used as async context manager."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = content

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_http)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── Step 5.1 — Router registered ─────────────────────────────────────────────


def test_indexers_router_registered(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert any(p.startswith("/api/indexers") for p in paths)


# ── Step 5.2 — CRUD ──────────────────────────────────────────────────────────


def test_create_indexer_returns_201(client):
    resp = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    assert data["name"] == "NZBGeek"
    assert data["type"] == "newznab"
    assert data["enabled"] is True
    assert data["last_test_success"] is None


def test_list_indexers_ordered_by_priority(client):
    client.post("/api/indexers/", json={**NEWZNAB_PAYLOAD, "priority": 20})
    client.post("/api/indexers/", json={**PROWLARR_PAYLOAD, "priority": 5})

    resp = client.get("/api/indexers/")
    assert resp.status_code == 200
    priorities = [i["priority"] for i in resp.json()]
    assert priorities == sorted(priorities)


def test_get_indexer_returns_detail(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    resp = client.get(f"/api/indexers/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "NZBGeek"


def test_get_indexer_404(client):
    resp = client.get("/api/indexers/99999")
    assert resp.status_code == 404


def test_patch_indexer_enabled_false(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]

    resp = client.patch(f"/api/indexers/{indexer_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # Verify persisted
    get_resp = client.get(f"/api/indexers/{indexer_id}")
    assert get_resp.json()["enabled"] is False


def test_patch_indexer_404(client):
    resp = client.patch("/api/indexers/99999", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_does_not_overwrite_unset_fields(client):
    """Fields omitted from PATCH body must not be changed."""
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]
    original_url = created["url"]

    client.patch(f"/api/indexers/{indexer_id}", json={"priority": 999})

    get_resp = client.get(f"/api/indexers/{indexer_id}")
    assert get_resp.json()["url"] == original_url  # url unchanged
    assert get_resp.json()["priority"] == 999


def test_delete_indexer_returns_204(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]

    resp = client.delete(f"/api/indexers/{indexer_id}")
    assert resp.status_code == 204

    # No longer in list
    list_resp = client.get("/api/indexers/")
    ids = [i["id"] for i in list_resp.json()]
    assert indexer_id not in ids


def test_delete_indexer_404(client):
    resp = client.delete("/api/indexers/99999")
    assert resp.status_code == 404


def test_delete_reduces_list(client):
    i1 = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    i2 = client.post("/api/indexers/", json=PROWLARR_PAYLOAD).json()

    client.delete(f"/api/indexers/{i1['id']}")

    resp = client.get("/api/indexers/")
    ids = [i["id"] for i in resp.json()]
    assert i1["id"] not in ids
    assert i2["id"] in ids


# ── Step 5.3 — Newznab test endpoint ─────────────────────────────────────────


def test_test_newznab_success(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]

    with patch(
        "pullbox.routers.indexers.httpx.AsyncClient",
        _make_httpx_mock(200, VALID_CAPS_XML),
    ):
        resp = client.post(f"/api/indexers/{indexer_id}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert "successful" in resp.json()["message"].lower()

    # Verify last_test_success persisted
    get_resp = client.get(f"/api/indexers/{indexer_id}")
    assert get_resp.json()["last_test_success"] is True
    assert get_resp.json()["last_tested_at"] is not None


def test_test_newznab_failure_on_http_error(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]

    with patch(
        "pullbox.routers.indexers.httpx.AsyncClient",
        _make_httpx_mock(403, b"Forbidden"),
    ):
        resp = client.post(f"/api/indexers/{indexer_id}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "403" in resp.json()["message"]

    get_resp = client.get(f"/api/indexers/{indexer_id}")
    assert get_resp.json()["last_test_success"] is False


def test_test_newznab_failure_on_wrong_root_element(client):
    created = client.post("/api/indexers/", json=NEWZNAB_PAYLOAD).json()
    indexer_id = created["id"]

    with patch(
        "pullbox.routers.indexers.httpx.AsyncClient",
        _make_httpx_mock(200, WRONG_ROOT_XML),
    ):
        resp = client.post(f"/api/indexers/{indexer_id}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is False


def test_test_unsupported_type_returns_400(client):
    created = client.post(
        "/api/indexers/",
        json={**PROWLARR_PAYLOAD, "name": "Mystery", "type": "unknowntype"},
    ).json()
    resp = client.post(f"/api/indexers/{created['id']}/test")
    assert resp.status_code == 400
    assert "not supported" in resp.json()["detail"].lower()


def test_test_prowlarr_requires_api_key(client):
    # PROWLARR_PAYLOAD has no api_key
    created = client.post("/api/indexers/", json=PROWLARR_PAYLOAD).json()
    resp = client.post(f"/api/indexers/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "api key" in resp.json()["message"].lower()


def test_test_prowlarr_success(client):
    created = client.post(
        "/api/indexers/", json={**PROWLARR_PAYLOAD, "api_key": "prowlarr-key"}
    ).json()

    with patch(
        "pullbox.routers.indexers.httpx.AsyncClient",
        _make_httpx_mock(200, b"{}"),
    ):
        resp = client.post(f"/api/indexers/{created['id']}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert "successful" in resp.json()["message"].lower()


def test_test_jackett_success(client):
    created = client.post(
        "/api/indexers/",
        json={
            "name": "Jackett",
            "type": "jackett",
            "url": "http://localhost:9117",
            "api_key": "jackett-key",
            "priority": 60,
        },
    ).json()

    with patch(
        "pullbox.routers.indexers.httpx.AsyncClient",
        _make_httpx_mock(200, VALID_CAPS_XML),
    ):
        resp = client.post(f"/api/indexers/{created['id']}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_test_jackett_requires_api_key(client):
    created = client.post(
        "/api/indexers/",
        json={
            "name": "Jackett",
            "type": "jackett",
            "url": "http://localhost:9117",
            "priority": 60,
        },
    ).json()
    resp = client.post(f"/api/indexers/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "api key" in resp.json()["message"].lower()
