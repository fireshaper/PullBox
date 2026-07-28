import pytest
from fastapi.testclient import TestClient

from pullbox.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client, monkeypatch):
    monkeypatch.delenv("PULLBOX_DEBUG", raising=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["debug"] is False


def test_health_debug_true(monkeypatch):
    monkeypatch.setenv("PULLBOX_DEBUG", "true")
    with TestClient(app) as c:
        resp = c.get("/api/health")
    assert resp.json()["debug"] is True
