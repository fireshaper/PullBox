"""Tests for the general settings page: the UI-editable library path override."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app
from pullbox.services.general import is_absolute_path

# ── is_absolute_path ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["/comics", "/mnt/media/comics", "C:\\Comics", "C:/Comics", "\\\\nas\\comics"],
)
def test_is_absolute_path_accepts_posix_and_windows_roots(value):
    assert is_absolute_path(value) is True


@pytest.mark.parametrize("value", ["comics", "./comics", "../comics"])
def test_is_absolute_path_rejects_relative(value):
    assert is_absolute_path(value) is False


# ── API endpoints ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_get_general_creates_default_with_no_override(client):
    resp = client.get("/api/settings/general")
    assert resp.status_code == 200
    data = resp.json()
    assert data["library_path"] is None
    # With no override, the effective path is the config file's value.
    assert data["effective_path"] == data["config_library_path"]


def test_patch_general_sets_library_path(client, tmp_path):
    resp = client.patch("/api/settings/general", json={"library_path": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["library_path"] == str(tmp_path)
    assert data["effective_path"] == str(tmp_path)
    # tmp_path is a real writable directory, so both probes pass.
    assert data["exists"] is True
    assert data["writable"] is True


def test_patch_general_reports_missing_directory(client, tmp_path):
    missing = tmp_path / "not-there"
    resp = client.patch("/api/settings/general", json={"library_path": str(missing)})
    assert resp.status_code == 200
    data = resp.json()
    # Saving still succeeds — the volume may not be mounted yet — but it is flagged.
    assert data["exists"] is False
    assert data["writable"] is False


def test_patch_general_rejects_relative_path(client):
    resp = client.patch("/api/settings/general", json={"library_path": "comics"})
    assert resp.status_code == 400


def test_patch_general_blank_clears_override(client, tmp_path):
    client.patch("/api/settings/general", json={"library_path": str(tmp_path)})
    resp = client.patch("/api/settings/general", json={"library_path": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["library_path"] is None
    assert data["effective_path"] == data["config_library_path"]


def test_general_override_drives_post_processing_preview(client, tmp_path):
    """The preview's root falls back to the resolved library path, not raw config."""
    client.patch("/api/settings/general", json={"library_path": "/tank/comics"})
    resp = client.post(
        "/api/settings/post-processing/preview",
        json={
            "folder_pattern": "{series}",
            "file_pattern": "{series} #{issue}",
            "destination_root": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["path"].startswith("/tank/comics/")


def test_destination_root_still_wins_over_library_path(client):
    client.patch("/api/settings/general", json={"library_path": "/tank/comics"})
    resp = client.post(
        "/api/settings/post-processing/preview",
        json={
            "folder_pattern": "{series}",
            "file_pattern": "{series} #{issue}",
            "destination_root": "/other/root",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["path"].startswith("/other/root/")
