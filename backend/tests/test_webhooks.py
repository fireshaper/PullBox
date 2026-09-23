"""Tests for outbound webhooks: payload shaping, delivery, dispatch and the API."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from pullbox.deps import get_metadata_provider
from pullbox.main import app
from pullbox.services import webhooks as svc

# ── Fixtures & helpers ────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Returns preset responses in sequence and records every request."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200)
        return self._responses.pop(0)


class _ErrorTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise httpx.ConnectError("refused", request=request)


def _target(fmt: str = "generic", secret: str | None = None, id_: int | None = None):
    return svc.WebhookTarget(
        id=id_, name="hook", url="http://hook.test/in", format=fmt, secret=secret
    )


def _fake_rows():
    series = SimpleNamespace(
        id=7,
        metron_id="m1",
        comicvine_id="cv1",
        title="Batman",
        publisher="DC Comics",
        start_year=2016,
        subscribed=True,
        auto_download=True,
        cover_url="http://img/series.jpg",
    )
    issue = SimpleNamespace(
        id=42,
        series_id=7,
        metron_id="mi1",
        comicvine_id="cvi1",
        issue_number="12",
        title="Twelve",
        cover_date=date(2024, 5, 1),
        store_date=None,
        cover_url="http://img/issue.jpg",
        status="downloaded",
        file_path="/comics/DC Comics/Batman (2016)/Batman #012 - Twelve.cbz",
    )
    job = SimpleNamespace(
        id=99,
        status="completed",
        attempts=2,
        result_title="Batman 012 (2024) (Digital).cbz",
        source_type="usenet",
        download_client_type="nzbget",
        next_attempt_at=datetime(2024, 5, 3, 6, 0),
    )
    return issue, series, job


TS = datetime(2024, 5, 2, 12, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


# ── Payload builders ──────────────────────────────────────────────────────────


def test_issue_payload_includes_path_rel_and_job():
    issue, series, job = _fake_rows()
    payload = svc.build_issue_payload(issue, series, "/comics", job=job, reason="why")

    assert payload["issue"]["id"] == 42
    assert payload["issue"]["path_rel"] == "DC Comics/Batman (2016)/Batman #012 - Twelve.cbz"
    assert payload["issue"]["cover_date"] == "2024-05-01"
    assert payload["series"]["title"] == "Batman"
    assert payload["job"]["id"] == 99
    # Naive datetimes are stamped UTC so receivers never guess the zone.
    assert payload["job"]["next_attempt_at"] == "2024-05-03T06:00:00+00:00"
    assert payload["reason"] == "why"


def test_issue_payload_path_rel_none_outside_library_or_without_file():
    issue, series, _ = _fake_rows()
    assert svc.build_issue_payload(issue, series, "/elsewhere")["issue"]["path_rel"] is None
    issue.file_path = None
    out = svc.build_issue_payload(issue, series, "/comics")
    assert out["issue"]["file_path"] is None
    assert out["issue"]["path_rel"] is None
    assert out["job"] is None
    assert "reason" not in out


def test_generic_body_is_enveloped():
    issue, series, job = _fake_rows()
    payload = svc.build_issue_payload(issue, series, "/comics", job=job)
    body = svc.build_body("generic", "download.completed", payload, TS)

    assert body["event"] == "download.completed"
    assert body["source"] == "pullbox"
    assert body["timestamp"] == "2024-05-02T12:00:00+00:00"
    assert body["issue"]["issue_number"] == "12"
    # The whole thing must be JSON-serialisable as-is.
    json.dumps(body)


@pytest.mark.parametrize(
    "event, expected_title",
    [
        ("download.completed", "Downloaded: Batman #12"),
        ("download.started", "Downloading: Batman #12"),
        ("download.failed", "Download failed: Batman #12"),
        ("download.exhausted", "Gave up on: Batman #12"),
    ],
)
def test_discord_body_issue_events(event, expected_title):
    issue, series, job = _fake_rows()
    payload = svc.build_issue_payload(issue, series, "/comics", job=job, reason="boom")
    body = svc.build_body("discord", event, payload, TS)

    assert body["username"] == "PullBox"
    (embed,) = body["embeds"]
    assert embed["title"] == expected_title
    assert embed["thumbnail"] == {"url": "http://img/issue.jpg"}
    assert embed["footer"]["text"].endswith(event)
    # No empty fields leak through.
    assert all(f["value"] for f in embed["fields"])
    json.dumps(body)


def test_discord_body_series_added_and_test():
    _, series, _ = _fake_rows()
    body = svc.build_body(
        "discord", "series.added", {"series": svc.build_series_payload(series)}, TS
    )
    (embed,) = body["embeds"]
    assert embed["title"] == "Added series: Batman (2016)"
    assert {"name": "Subscribed", "value": "Yes", "inline": True} in embed["fields"]

    body = svc.build_body("discord", "test", svc.build_test_payload("x"), TS)
    assert body["embeds"][0]["title"] == "PullBox webhook test"


def test_discord_body_unknown_event_still_renders():
    body = svc.build_body("discord", "something.new", {}, TS)
    assert body["embeds"][0]["title"] == "PullBox: something.new"


# ── deliver() ─────────────────────────────────────────────────────────────────


def test_deliver_success_sets_headers_and_signature():
    transport = _CapturingTransport([httpx.Response(204)])
    target = _target(secret="s3cret")

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(target, "test", {"message": "hi"}, client=c, timestamp=TS)

    result = _run(_go())
    assert result.success is True
    assert result.status_code == 204
    assert result.message == "HTTP 204"

    (req,) = transport.requests
    assert req.method == "POST"
    assert str(req.url) == "http://hook.test/in"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["X-PullBox-Event"] == "test"
    assert len(req.headers["X-PullBox-Delivery"]) == 32
    assert req.headers["User-Agent"].startswith("PullBox/")

    expected = hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-PullBox-Signature"] == f"sha256={expected}"
    assert json.loads(req.content)["event"] == "test"


def test_deliver_without_secret_has_no_signature():
    transport = _CapturingTransport([httpx.Response(200)])

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    assert _run(_go()).success
    assert "X-PullBox-Signature" not in transport.requests[0].headers


def test_deliver_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(svc, "RETRY_DELAYS", (0.0, 0.0))
    transport = _CapturingTransport([httpx.Response(503, text="down"), httpx.Response(200)])

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    result = _run(_go())
    assert result.success
    assert len(transport.requests) == 2


def test_deliver_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(svc, "RETRY_DELAYS", (0.0, 0.0))
    transport = _CapturingTransport([httpx.Response(500, text="a")] * 5)

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    result = _run(_go())
    assert result.success is False
    assert result.status_code == 500
    assert result.message == "HTTP 500: a"
    assert len(transport.requests) == svc.MAX_ATTEMPTS


def test_deliver_does_not_retry_4xx():
    transport = _CapturingTransport([httpx.Response(404, text="nope"), httpx.Response(200)])

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    result = _run(_go())
    assert result.success is False
    assert result.message == "HTTP 404: nope"
    assert len(transport.requests) == 1


def test_deliver_retries_429_honouring_retry_after(monkeypatch):
    slept: list[float] = []

    async def _fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(svc.asyncio, "sleep", _fake_sleep)
    transport = _CapturingTransport(
        [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200)]
    )

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    assert _run(_go()).success
    assert slept == [2.0]


def test_deliver_swallows_connection_errors(monkeypatch):
    monkeypatch.setattr(svc, "RETRY_DELAYS", (0.0, 0.0))
    transport = _ErrorTransport()

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await svc.deliver(_target(), "test", {}, client=c)

    result = _run(_go())
    assert result.success is False
    assert result.message.startswith("ConnectError")
    assert transport.calls == svc.MAX_ATTEMPTS


# ── emit() / dispatch() ───────────────────────────────────────────────────────


def test_emit_unknown_event_is_ignored():
    async def _go():
        return svc.emit("nope.nope", {})

    assert _run(_go()) is None


def test_emit_without_running_loop_returns_none():
    assert svc.emit("test", {}) is None


def test_emit_schedules_dispatch_and_tracks_task():
    seen = []

    async def _fake_dispatch(event, payload):
        seen.append((event, payload))
        return []

    async def _go():
        with patch.object(svc, "dispatch", _fake_dispatch):
            task = svc.emit("test", {"a": 1})
            assert task in svc._inflight
            await task
            assert task not in svc._inflight

    _run(_go())
    assert seen == [("test", {"a": 1})]


def test_dispatch_filters_by_event_and_enabled(client):
    """Only enabled hooks subscribed to the event receive it; results are recorded."""
    a = client.post(
        "/api/webhooks/",
        json={"name": "A", "url": "http://a.test/", "events": ["test", "download.completed"]},
    ).json()
    client.post(
        "/api/webhooks/",
        json={"name": "B", "url": "http://b.test/", "events": ["download.failed"]},
    )
    client.post(
        "/api/webhooks/",
        json={"name": "C", "url": "http://c.test/", "events": ["test"], "enabled": False},
    )

    delivered = []

    async def _fake_deliver(target, event, payload, *, client=None, timestamp=None):
        delivered.append((target.name, event))
        return svc.DeliveryResult(False, "HTTP 500")

    with patch.object(svc, "deliver", _fake_deliver):
        results = _run(svc.dispatch("test", {"message": "x"}))

    assert delivered == [("A", "test")]
    assert [r.success for r in results] == [False]

    row = client.get(f"/api/webhooks/{a['id']}").json()
    assert row["last_delivery_success"] is False
    assert row["last_delivery_error"] == "HTTP 500"
    assert row["last_delivery_at"] is not None


def test_dispatch_with_no_targets_is_noop(client):
    with patch.object(svc, "deliver", AsyncMock()) as mocked:
        assert _run(svc.dispatch("test", {})) == []
    mocked.assert_not_called()


# ── API ───────────────────────────────────────────────────────────────────────


def test_events_endpoint_lists_registry(client):
    resp = client.get("/api/webhooks/events")
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()]
    assert names == list(svc.EVENTS)
    assert all(e["description"] for e in resp.json())


def test_crud_roundtrip(client):
    created = client.post(
        "/api/webhooks/",
        json={
            "name": "  Thwip ",
            "url": "http://thwip.local:9000/hooks/pullbox",
            "format": "generic",
            "events": ["download.completed", "download.completed", "series.added"],
            "secret": "abc",
        },
    )
    assert created.status_code == 201, created.text
    hook = created.json()
    assert hook["name"] == "Thwip"
    assert hook["events"] == ["download.completed", "series.added"]  # deduped, order kept
    assert hook["enabled"] is True
    assert hook["last_delivery_at"] is None

    assert [h["id"] for h in client.get("/api/webhooks/").json()] == [hook["id"]]

    patched = client.patch(
        f"/api/webhooks/{hook['id']}", json={"enabled": False, "events": ["test"]}
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["events"] == ["test"]
    assert patched.json()["secret"] == "abc"  # untouched

    assert client.delete(f"/api/webhooks/{hook['id']}").status_code == 204
    assert client.get(f"/api/webhooks/{hook['id']}").status_code == 404
    assert client.patch(f"/api/webhooks/{hook['id']}", json={"name": "x"}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "url": "ftp://nope", "events": []},
        {"name": "x", "url": "http://ok/", "format": "slack", "events": []},
        {"name": "x", "url": "http://ok/", "events": ["download.teleported"]},
        {"name": "   ", "url": "http://ok/", "events": []},
    ],
)
def test_create_validation(client, body):
    assert client.post("/api/webhooks/", json=body).status_code == 422


def test_patch_validation(client):
    hook = client.post("/api/webhooks/", json={"name": "x", "url": "http://ok/"}).json()
    assert client.patch(f"/api/webhooks/{hook['id']}", json={"url": "nope"}).status_code == 422
    assert client.patch(f"/api/webhooks/{hook['id']}", json={"events": ["zz"]}).status_code == 422


def test_test_unsaved_endpoint(client):
    calls = []

    async def _fake_deliver(target, event, payload, **kw):
        calls.append((target, event, payload))
        return svc.DeliveryResult(True, "HTTP 200", 200)

    with patch.object(svc, "deliver", _fake_deliver):
        resp = client.post(
            "/api/webhooks/test",
            json={"url": "http://x.test/", "format": "discord", "secret": "k"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "HTTP 200", "status_code": 200}
    (target, event, payload) = calls[0]
    assert target.id is None
    assert target.format == "discord"
    assert target.secret == "k"
    assert event == "test"
    assert "message" in payload


def test_test_saved_endpoint_records_outcome(client):
    hook = client.post(
        "/api/webhooks/", json={"name": "H", "url": "http://x.test/", "events": ["test"]}
    ).json()

    async def _fake_deliver(target, event, payload, **kw):
        assert target.id == hook["id"]
        return svc.DeliveryResult(False, "HTTP 502: bad", 502)

    with patch.object(svc, "deliver", _fake_deliver):
        resp = client.post(f"/api/webhooks/{hook['id']}/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is False

    row = client.get(f"/api/webhooks/{hook['id']}").json()
    assert row["last_delivery_success"] is False
    assert row["last_delivery_error"] == "HTTP 502: bad"
    assert row["last_delivery_at"] is not None

    assert client.post("/api/webhooks/9999/test").status_code == 404


# ── Emission from the series add path ─────────────────────────────────────────


def test_add_series_emits_series_added(client):
    mock = AsyncMock()
    mock.get_volume.return_value = {
        "metron_id": "m1",
        "comicvine_id": "1",
        "title": "Saga",
        "publisher": "Image",
        "start_year": 2012,
        "cover_url": None,
        "description": None,
        "issue_count": 1,
    }

    async def _override():
        yield mock

    app.dependency_overrides[get_metadata_provider] = _override
    emitted = []
    try:
        with patch(
            "pullbox.routers.series.webhooks.emit", side_effect=lambda e, p: emitted.append((e, p))
        ):
            resp = client.post("/api/series/", json={"comicvine_id": "1", "subscribed": True})
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    assert resp.status_code == 201
    assert len(emitted) == 1
    event, payload = emitted[0]
    assert event == "series.added"
    assert payload["series"]["title"] == "Saga"
    assert payload["series"]["id"] == resp.json()["id"]
    assert payload["series"]["subscribed"] is True


# ── Emission from process_job ─────────────────────────────────────────────────

FAKE_VOLUME = {
    "metron_id": "m77001",
    "comicvine_id": "77001",
    "title": "Batman",
    "publisher": "DC Comics",
    "start_year": 2016,
    "cover_url": None,
    "description": None,
    "issue_count": 1,
}

FAKE_ISSUES = [
    {
        "metron_id": "m700001",
        "comicvine_id": "700001",
        "issue_number": "1",
        "title": "First Issue",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "description": None,
    }
]


def _provider_override(*, volume=None, issues=None):
    mock = AsyncMock()
    mock.get_volume.return_value = volume if volume is not None else FAKE_VOLUME
    if issues is not None:
        mock.get_issues.return_value = issues
    mock.get_issue.return_value = {"metron_id": None, "comicvine_id": None, "story_arcs": []}

    async def _override():
        yield mock

    return _override


@pytest.fixture
def wanted_issue(client):
    """One series with one issue marked 'wanted'; returns the issue id."""
    app.dependency_overrides[get_metadata_provider] = _provider_override(volume=FAKE_VOLUME)
    try:
        series_id = client.post("/api/series/", json={"comicvine_id": "77001"}).json()["id"]
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)
    app.dependency_overrides[get_metadata_provider] = _provider_override(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)
    issue = client.get(f"/api/series/{series_id}/issues").json()[0]
    # Deliberately not POST /want: that auto-enqueues *and* fires a background
    # run_job_now, which would race the explicit process_job calls below.
    # enqueue_issue promotes the issue to 'wanted' and creates the job quietly.
    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_issue

    async def _enqueue():
        async with AsyncSessionLocal() as db:
            await enqueue_issue(issue["id"], db)
            await db.commit()

    asyncio.run(_enqueue())
    return issue["id"]


def test_process_job_emits_started_when_dispatched(client, wanted_issue):
    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.search import SearchResult
    from pullbox.services.queue import process_job

    client.post(
        "/api/download-clients/",
        json={"name": "nzb", "type": "nzbget", "host": "localhost", "port": 6789},
    )
    fake_result = SearchResult(
        indexer_id=1,
        indexer_name="Idx",
        source_type="usenet",
        title="Batman 001 (2016)",
        guid="g1",
        download_url="http://example.com/b1.nzb",
        score=3.0,
    )
    emitted: list[tuple[str, dict]] = []

    async def _run():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            job = (
                (await db.execute(select(DownloadJob).where(DownloadJob.issue_id == wanted_issue)))
                .scalars()
                .first()
            )
            job.status = "queued"
            await db.commit()
            job_id = job.id

        async with AsyncSessionLocal() as db:
            with (
                patch(
                    "pullbox.services.queue.fan_out_search",
                    new=AsyncMock(return_value=[fake_result]),
                ),
                patch(
                    "pullbox.services.queue.NZBGetClient.send_nzb",
                    new=AsyncMock(return_value="nzb-123"),
                ),
                patch(
                    "pullbox.services.queue.webhooks.emit",
                    side_effect=lambda e, p: emitted.append((e, p)),
                ),
            ):
                await process_job(job_id, db, Settings())
                await db.commit()

    asyncio.run(_run())

    assert [e for e, _ in emitted] == ["download.started"]
    payload = emitted[0][1]
    assert payload["issue"]["id"] == wanted_issue
    assert payload["issue"]["status"] == "downloading"
    assert payload["series"]["title"] == "Batman"
    assert payload["job"]["result_title"] == "Batman 001 (2016)"
    assert payload["job"]["download_client_type"] == "nzbget"


def test_process_job_emits_exhausted_only_at_retry_cap(client, wanted_issue):
    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import process_job

    settings = Settings(max_retries=2)
    emitted: list[tuple[str, dict]] = []

    async def _reset():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            job = (
                (await db.execute(select(DownloadJob).where(DownloadJob.issue_id == wanted_issue)))
                .scalars()
                .first()
            )
            job.status = "queued"
            job.attempts = 0
            await db.commit()
            return job.id

    job_id = asyncio.run(_reset())

    async def _process():
        async with AsyncSessionLocal() as db:
            with (
                patch("pullbox.services.queue.fan_out_search", new=AsyncMock(return_value=[])),
                patch(
                    "pullbox.services.queue.webhooks.emit",
                    side_effect=lambda e, p: emitted.append((e, p)),
                ),
            ):
                await process_job(job_id, db, settings)
                await db.commit()

    asyncio.run(_process())
    assert emitted == []  # first miss: routine, no notification

    asyncio.run(_process())
    assert [e for e, _ in emitted] == ["download.exhausted"]
    payload = emitted[0][1]
    assert payload["job"]["attempts"] == 2
    assert payload["job"]["next_attempt_at"] is None
    assert payload["reason"] == "No results found on any indexer (attempt 2 of 2)"
