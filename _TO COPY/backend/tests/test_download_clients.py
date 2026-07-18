"""Tests for Phase 8: Download Client Integration (steps 8.1–8.4)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from pullbox.clients.download_client import BaseDownloadClient
from pullbox.clients.nzbget import NZBGetClient
from pullbox.clients.sabnzbd import SABnzbdClient
from pullbox.deps import get_comicvine_client
from pullbox.main import app

# ── Shared helpers ────────────────────────────────────────────────────────────


class _StubClient(BaseDownloadClient):
    """Minimal concrete implementation used to test the interface contract."""

    async def send_nzb(self, url: str, name: str, category: str) -> str:
        return "stub-job-id"

    async def get_job_status(self, job_id: str) -> str:
        return "downloading"

    async def get_completed_path(self, job_id: str) -> str | None:
        return None

    async def test_connection(self) -> bool:
        return True


class _NZBGetMockTransport(httpx.AsyncBaseTransport):
    """Returns preset JSON-RPC results in sequence, capturing request bodies."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self._idx = 0
        self.requests: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        self.requests.append(json.loads(body))
        result = self._results[min(self._idx, len(self._results) - 1)]
        self._idx += 1
        return httpx.Response(
            200,
            content=json.dumps({"result": result}).encode(),
        )


def _make_nzbget(transport: _NZBGetMockTransport) -> NZBGetClient:
    return NZBGetClient("localhost", 6789, "admin", "pass", transport=transport)


# Shared ComicVine mock fixture data (same as test_queue.py)
FAKE_VOLUME = {
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
        "comicvine_id": "700001",
        "issue_number": "1",
        "title": "First Issue",
        "cover_date": "2016-01-01",
        "store_date": "2016-01-06",
        "cover_url": None,
        "description": None,
    }
]


def _make_mock_cv(*, volume=None, issues=None):
    mock = AsyncMock()
    if volume is not None:
        mock.get_volume.return_value = volume
    if issues is not None:
        mock.get_issues.return_value = issues

    async def _override():
        yield mock

    return _override


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(client):
    """Seed one series + one issue marked wanted."""
    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(volume=FAKE_VOLUME)
    try:
        series_id = client.post("/api/series/", json={"comicvine_id": "77001"}).json()["id"]
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    app.dependency_overrides[get_comicvine_client] = _make_mock_cv(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_comicvine_client, None)

    issues = client.get(f"/api/series/{series_id}/issues").json()
    client.post(f"/api/issues/{issues[0]['id']}/want")
    return series_id, issues


# ── Step 8.1 — BaseDownloadClient interface ───────────────────────────────────


def test_base_client_stub_satisfies_interface():
    """A concrete subclass can be instantiated with no TypeError."""
    stub = _StubClient()
    assert isinstance(stub, BaseDownloadClient)


def test_stub_test_connection_returns_true():
    result = asyncio.run(_StubClient().test_connection())
    assert result is True


def test_stub_send_nzb_returns_string():
    result = asyncio.run(_StubClient().send_nzb("http://example.com/a.nzb", "Name", "cat"))
    assert result == "stub-job-id"


def test_stub_get_job_status_returns_known_value():
    result = asyncio.run(_StubClient().get_job_status("42"))
    assert result in ("downloading", "completed", "failed", "unknown")


# ── Step 8.2 — NZBGetClient ───────────────────────────────────────────────────


def test_send_nzb_calls_appendurl_with_url_unchanged():
    """appendurl params must include the raw URL (no intermediate fetch)."""
    transport = _NZBGetMockTransport([123])
    client = _make_nzbget(transport)

    result = asyncio.run(
        client.send_nzb("http://nzb.example/comic.nzb", "Batman 1", "pullbox-comics")
    )

    assert result == "123"
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req["method"] == "appendurl"
    # URL must appear verbatim as the 5th param (index 4)
    assert req["params"][4] == "http://nzb.example/comic.nzb"
    assert req["params"][0] == "Batman 1"
    assert req["params"][1] == "pullbox-comics"


def test_send_nzb_returns_string_job_id():
    """Return value is always a string even when NZBGet returns an integer."""
    transport = _NZBGetMockTransport([456])
    client = _make_nzbget(transport)
    result = asyncio.run(client.send_nzb("http://nzb.example/x.nzb", "Name", "cat"))
    assert result == "456"
    assert isinstance(result, str)


def test_get_job_status_downloading_from_listgroups():
    """DOWNLOADING status in listgroups maps to 'downloading'."""
    transport = _NZBGetMockTransport([
        [{"NZBID": 42, "Status": "DOWNLOADING"}],  # listgroups response
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("42"))
    assert result == "downloading"


def test_get_job_status_success_from_listgroups_returns_completed():
    """SUCCESS status maps to 'completed' regardless of which endpoint returns it."""
    transport = _NZBGetMockTransport([
        [{"NZBID": 42, "Status": "SUCCESS"}],
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("42"))
    assert result == "completed"


def test_get_job_status_success_from_history_returns_completed():
    """If not in listgroups, check history. SUCCESS → 'completed'."""
    transport = _NZBGetMockTransport([
        [],                                          # listgroups: not found
        [{"NZBID": 42, "Status": "SUCCESS"}],        # history: found
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("42"))
    assert result == "completed"


def test_get_job_status_failure_from_history_returns_failed():
    """FAILURE in history → 'failed'."""
    transport = _NZBGetMockTransport([
        [],
        [{"NZBID": 99, "Status": "FAILURE"}],
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("99"))
    assert result == "failed"


def test_get_job_status_deleted_returns_failed():
    """DELETED in history → 'failed'."""
    transport = _NZBGetMockTransport([
        [],
        [{"NZBID": 7, "Status": "DELETED"}],
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("7"))
    assert result == "failed"


def test_get_job_status_unknown_when_not_found():
    """If NZBID is not in listgroups or history, return 'unknown'."""
    transport = _NZBGetMockTransport([[], []])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_job_status("999"))
    assert result == "unknown"


def test_nzbget_get_completed_path_returns_destdir():
    """get_completed_path() returns DestDir from the matching history item."""
    transport = _NZBGetMockTransport([
        [{"NZBID": 42, "Status": "SUCCESS", "DestDir": "/downloads/Batman 12"}],
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_completed_path("42"))
    assert result == "/downloads/Batman 12"


def test_nzbget_get_completed_path_falls_back_to_finaldir():
    """When DestDir is blank, fall back to FinalDir."""
    transport = _NZBGetMockTransport([
        [{"NZBID": 42, "Status": "SUCCESS", "DestDir": "", "FinalDir": "/final/Batman 12"}],
    ])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_completed_path("42"))
    assert result == "/final/Batman 12"


def test_nzbget_get_completed_path_none_when_not_found():
    """Missing NZBID in history → None."""
    transport = _NZBGetMockTransport([[]])
    client = _make_nzbget(transport)
    result = asyncio.run(client.get_completed_path("999"))
    assert result is None


def test_test_connection_returns_true_on_version():
    """test_connection() returns True when the version method responds."""
    transport = _NZBGetMockTransport(["20.0"])
    client = _make_nzbget(transport)
    result = asyncio.run(client.test_connection())
    assert result is True
    assert transport.requests[0]["method"] == "version"


def test_test_connection_returns_false_on_exception():
    """test_connection() returns False when the request fails."""

    async def _run():
        client = NZBGetClient("badhost", 6789, "u", "p")
        with patch.object(client, "_call", side_effect=Exception("timeout")):
            return await client.test_connection()

    assert asyncio.run(_run()) is False


# ── Step 8.2a — SABnzbdClient ────────────────────────────────────────────────


class _SABnzbdMockTransport(httpx.AsyncBaseTransport):
    """Returns preset JSON responses in sequence, capturing request query params."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.requests: list[dict[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        response = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return httpx.Response(200, content=json.dumps(response).encode())


def _make_sabnzbd(transport: _SABnzbdMockTransport) -> SABnzbdClient:
    return SABnzbdClient("localhost", 8085, "testkey", transport=transport)


def test_sabnzbd_send_nzb_returns_nzo_id():
    """send_nzb() returns the first nzo_id on a successful addurl response."""
    transport = _SABnzbdMockTransport([{"status": True, "nzo_ids": ["SABnzb+abc123"]}])
    client = _make_sabnzbd(transport)

    result = asyncio.run(
        client.send_nzb("http://nzb.example/comic.nzb", "Batman 1", "comics")
    )

    assert result == "SABnzb+abc123"
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req["mode"] == "addurl"
    assert req["name"] == "http://nzb.example/comic.nzb"
    assert req["nzbname"] == "Batman 1"
    assert req["cat"] == "comics"


def test_sabnzbd_send_nzb_raises_on_status_false():
    """send_nzb() raises RuntimeError when SABnzbd returns status=false."""
    transport = _SABnzbdMockTransport([{"status": False}])
    client = _make_sabnzbd(transport)

    with pytest.raises(RuntimeError, match="addurl failed"):
        asyncio.run(client.send_nzb("http://nzb.example/x.nzb", "Name", "cat"))


def test_sabnzbd_get_job_status_downloading_when_in_queue():
    """get_job_status() returns 'downloading' when the job is in the active queue."""
    transport = _SABnzbdMockTransport([
        {"queue": {"slots": [{"nzo_id": "SABnzb+xyz", "status": "Downloading"}]}},
    ])
    client = _make_sabnzbd(transport)

    result = asyncio.run(client.get_job_status("SABnzb+xyz"))
    assert result == "downloading"


def test_sabnzbd_get_job_status_completed_from_history():
    """get_job_status() returns 'completed' when job is in history with 'Completed' status."""
    transport = _SABnzbdMockTransport([
        {"queue": {"slots": []}},
        {"history": {"slots": [{"nzo_id": "SABnzb+xyz", "status": "Completed"}]}},
    ])
    client = _make_sabnzbd(transport)

    result = asyncio.run(client.get_job_status("SABnzb+xyz"))
    assert result == "completed"


def test_sabnzbd_get_completed_path_returns_storage():
    """get_completed_path() returns the 'storage' field from the matching history slot."""
    transport = _SABnzbdMockTransport([
        {"history": {"slots": [{"nzo_id": "SABnzb+xyz", "storage": "/downloads/Batman 12.cbz"}]}},
    ])
    client = _make_sabnzbd(transport)

    result = asyncio.run(client.get_completed_path("SABnzb+xyz"))
    assert result == "/downloads/Batman 12.cbz"


def test_sabnzbd_get_completed_path_none_when_not_found():
    """Missing nzo_id in history → None."""
    transport = _SABnzbdMockTransport([{"history": {"slots": []}}])
    client = _make_sabnzbd(transport)

    result = asyncio.run(client.get_completed_path("SABnzb+missing"))
    assert result is None


def test_sabnzbd_test_connection_false_on_timeout():
    """test_connection() returns False on timeout without raising."""

    async def _run():
        client = SABnzbdClient("badhost", 9999, "key")
        with patch.object(client, "_call", side_effect=httpx.TimeoutException("timeout")):
            return await client.test_connection()

    assert asyncio.run(_run()) is False


# ── Step 8.3 — Dispatch wired into process_job ────────────────────────────────


def test_process_job_dispatches_to_nzbget(client, seeded):
    """process_job() with results + NZBGet configured in DB: job dispatched, statuses updated."""
    from pullbox.search import SearchResult

    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadClient, DownloadJob, Issue
    from pullbox.services.queue import enqueue_issue, process_job

    fake_result = SearchResult(
        indexer_id=1,
        indexer_name="TestIndexer",
        source_type="usenet",
        title="Batman 1",
        guid="guid-abc",
        download_url="http://nzb.example/batman1.nzb",
        score=3.5,
    )

    async def _run():
        # Seed a DownloadClient row so process_job finds it
        async with AsyncSessionLocal() as db:
            dc = DownloadClient(
                name="NZBGet",
                type="nzbget",
                host="localhost",
                port=6789,
                username="admin",
                password="pass",
                category="pullbox-comics",
                enabled=True,
            )
            db.add(dc)
            await db.commit()

        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id

        settings = Settings()

        async with AsyncSessionLocal() as db:
            with (
                patch(
                    "pullbox.services.queue.fan_out_search",
                    new=AsyncMock(return_value=[fake_result]),
                ),
                patch(
                    "pullbox.services.queue.NZBGetClient",
                ) as mock_cls,
            ):
                mock_instance = AsyncMock()
                mock_instance.send_nzb.return_value = "job-123"
                mock_cls.return_value = mock_instance

                await process_job(job_id, db, settings)
                await db.commit()

        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            issue_obj = await db.get(Issue, wanted_id)
            return (
                job_obj.client_job_id,
                job_obj.status,
                job_obj.download_client_type,
                issue_obj.status,
            )

    cjid, jstatus, dctype, istatus = asyncio.run(_run())
    assert cjid == "job-123"
    assert jstatus == "downloading"
    assert dctype == "nzbget"
    assert istatus == "downloading"


def test_process_job_no_client_leaves_pending(client, seeded):
    """process_job() with results but no download client in DB: job stays 'pending'."""
    from pullbox.search import SearchResult

    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue, process_job

    fake_result = SearchResult(
        indexer_id=1,
        indexer_name="TestIndexer",
        source_type="usenet",
        title="Batman 1",
        guid="guid-xyz",
        download_url="http://nzb.example/batman1.nzb",
        score=2.0,
    )

    async def _run():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id

        settings = Settings()  # no DownloadClient rows in DB

        async with AsyncSessionLocal() as db:
            with patch(
                "pullbox.services.queue.fan_out_search",
                new=AsyncMock(return_value=[fake_result]),
            ):
                await process_job(job_id, db, settings)
                await db.commit()

        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            return job_obj.status, job_obj.client_job_id

    status, cjid = asyncio.run(_run())
    assert status == "pending"
    assert cjid is None


def test_process_job_dispatches_to_sabnzbd(client, seeded):
    """process_job() with SABnzbd client in DB: job dispatched, statuses updated."""
    from pullbox.search import SearchResult

    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadClient, DownloadJob, Issue
    from pullbox.services.queue import enqueue_issue, process_job

    fake_result = SearchResult(
        indexer_id=1,
        indexer_name="TestIndexer",
        source_type="usenet",
        title="Batman 1",
        guid="guid-sab",
        download_url="http://nzb.example/batman1.nzb",
        score=3.5,
    )

    async def _run():
        async with AsyncSessionLocal() as db:
            dc = DownloadClient(
                name="SABnzbd",
                type="sabnzbd",
                host="localhost",
                port=8085,
                api_key="testkey",
                category="comics",
                enabled=True,
            )
            db.add(dc)
            await db.commit()

        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id

        settings = Settings()

        async with AsyncSessionLocal() as db:
            with (
                patch(
                    "pullbox.services.queue.fan_out_search",
                    new=AsyncMock(return_value=[fake_result]),
                ),
                patch("pullbox.services.queue.SABnzbdClient") as mock_cls,
            ):
                mock_instance = AsyncMock()
                mock_instance.send_nzb.return_value = "SABnzb+xyz"
                mock_cls.return_value = mock_instance

                await process_job(job_id, db, settings)
                await db.commit()

        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            issue_obj = await db.get(Issue, wanted_id)
            return (
                job_obj.client_job_id,
                job_obj.status,
                job_obj.download_client_type,
                issue_obj.status,
            )

    cjid, jstatus, dctype, istatus = asyncio.run(_run())
    assert cjid == "SABnzb+xyz"
    assert jstatus == "downloading"
    assert dctype == "sabnzbd"
    assert istatus == "downloading"


def test_process_job_unsupported_client_leaves_pending(client, seeded):
    """process_job() with unsupported client type: job stays 'pending' with a warning logged."""
    from pullbox.search import SearchResult

    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadClient, DownloadJob
    from pullbox.services.queue import enqueue_issue, process_job

    fake_result = SearchResult(
        indexer_id=1,
        indexer_name="TestIndexer",
        source_type="usenet",
        title="Batman 1",
        guid="guid-deluge",
        download_url="http://nzb.example/batman1.nzb",
        score=3.0,
    )

    async def _run():
        async with AsyncSessionLocal() as db:
            dc = DownloadClient(
                name="Deluge",
                type="deluge",
                host="localhost",
                port=8112,
                category="pullbox-comics",
                enabled=True,
            )
            db.add(dc)
            await db.commit()

        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id

        settings = Settings()

        async with AsyncSessionLocal() as db:
            with patch(
                "pullbox.services.queue.fan_out_search",
                new=AsyncMock(return_value=[fake_result]),
            ):
                await process_job(job_id, db, settings)
                await db.commit()

        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            return job_obj.status, job_obj.client_job_id

    status, cjid = asyncio.run(_run())
    assert status == "pending"
    assert cjid is None


# ── Step 8.4 — poll_download_clients ─────────────────────────────────────────


def _seed_nzbget_client():
    """Create an enabled NZBGet DownloadClient row in the test DB."""
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadClient

    async def _create():
        async with AsyncSessionLocal() as db:
            dc = DownloadClient(
                name="NZBGet",
                type="nzbget",
                host="localhost",
                port=6789,
                username="admin",
                password="pass",
                category="pullbox-comics",
                enabled=True,
            )
            db.add(dc)
            await db.commit()

    asyncio.run(_create())


def test_poll_marks_completed_job_and_issue(client, seeded):
    """poll_download_clients() sets job=completed, issue=downloaded on SUCCESS."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue
    from pullbox.services.queue import enqueue_issue

    _seed_nzbget_client()

    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id
        # Force job into 'downloading' state with a client_job_id
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            job_obj.status = "downloading"
            job_obj.client_job_id = "nzb-completed-999"
            job_obj.download_client_type = "nzbget"
            job_obj.attempts = 1
            await db.commit()
        return job_id

    job_id = asyncio.run(_setup())

    from pullbox.scheduler import poll_download_clients

    with patch(
        "pullbox.clients.nzbget.NZBGetClient.get_job_status",
        new=AsyncMock(return_value="completed"),
    ):
        asyncio.run(poll_download_clients())

    async def _check():
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            issue_obj = await db.get(Issue, wanted_id)
            return job_obj.status, issue_obj.status

    jstatus, istatus = asyncio.run(_check())
    assert jstatus == "completed"
    assert istatus == "downloaded"


def test_poll_runs_post_processing_on_completion(client, seeded, tmp_path):
    """When post-processing is enabled, a completed job's file is moved and file_path set."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue, PostProcessingSettings
    from pullbox.services.queue import enqueue_issue

    _seed_nzbget_client()

    # Source download directory containing a comic file.
    src_dir = tmp_path / "downloads" / "batman.raw"
    src_dir.mkdir(parents=True)
    (src_dir / "payload.cbz").write_bytes(b"comic")
    library = tmp_path / "comics"

    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            db.add(
                PostProcessingSettings(
                    enabled=True,
                    operation="move",
                    destination_root=str(library),
                    folder_pattern="{publisher}/{series} ({year})",
                    file_pattern="{series} #{issue} - {title}",
                )
            )
            await db.commit()
            job_id = job.id
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            job_obj.status = "downloading"
            job_obj.client_job_id = "nzb-pp-777"
            job_obj.download_client_type = "nzbget"
            job_obj.attempts = 1
            await db.commit()
        return job_id

    asyncio.run(_setup())

    from pullbox.scheduler import poll_download_clients

    with (
        patch(
            "pullbox.clients.nzbget.NZBGetClient.get_job_status",
            new=AsyncMock(return_value="completed"),
        ),
        patch(
            "pullbox.clients.nzbget.NZBGetClient.get_completed_path",
            new=AsyncMock(return_value=str(src_dir)),
        ),
    ):
        asyncio.run(poll_download_clients())

    async def _check():
        async with AsyncSessionLocal() as db:
            issue_obj = await db.get(Issue, wanted_id)
            return issue_obj.status, issue_obj.file_path

    istatus, file_path = asyncio.run(_check())
    assert istatus == "downloaded"
    assert file_path is not None
    moved = Path(file_path)
    assert moved.exists()
    assert moved.name == "Batman #1 - First Issue.cbz"
    assert not (src_dir / "payload.cbz").exists()  # moved out


def test_poll_marks_failed_job_and_requeues_issue(client, seeded):
    """poll_download_clients() sets job=failed, issue=wanted on FAILURE."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue
    from pullbox.services.queue import enqueue_issue

    _seed_nzbget_client()

    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            job_obj.status = "downloading"
            job_obj.client_job_id = "nzb-failed-888"
            job_obj.download_client_type = "nzbget"
            job_obj.attempts = 1
            await db.commit()
        return job_id

    job_id = asyncio.run(_setup())

    from pullbox.scheduler import poll_download_clients

    with patch(
        "pullbox.clients.nzbget.NZBGetClient.get_job_status",
        new=AsyncMock(return_value="failed"),
    ):
        asyncio.run(poll_download_clients())

    async def _check():
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            issue_obj = await db.get(Issue, wanted_id)
            return job_obj.status, issue_obj.status, job_obj.next_attempt_at

    jstatus, istatus, naa = asyncio.run(_check())
    assert jstatus == "failed"
    assert istatus == "wanted"
    # next_attempt_at should be ~1 day from now (attempt 1 → min(2^0, 7) = 1 day)
    now = datetime.now(tz=timezone.utc)
    expected = now + timedelta(days=1)
    assert naa is not None
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 30


def test_poll_skips_still_downloading_jobs(client, seeded):
    """poll_download_clients() does not modify jobs still actively downloading."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue

    _seed_nzbget_client()

    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            job_obj.status = "downloading"
            job_obj.client_job_id = "nzb-active-777"
            job_obj.download_client_type = "nzbget"
            job_obj.attempts = 1
            await db.commit()
        return job_id

    job_id = asyncio.run(_setup())

    from pullbox.scheduler import poll_download_clients

    with patch(
        "pullbox.clients.nzbget.NZBGetClient.get_job_status",
        new=AsyncMock(return_value="downloading"),
    ):
        asyncio.run(poll_download_clients())

    async def _check():
        async with AsyncSessionLocal() as db:
            job_obj = await db.get(DownloadJob, job_id)
            return job_obj.status

    assert asyncio.run(_check()) == "downloading"


def test_poll_schedule_registered(client):
    """poll_download_clients schedule must be registered in the APScheduler store."""
    from sqlalchemy import text

    async def _check():
        from pullbox.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT id FROM schedules"))
            return {row[0] for row in result.fetchall()}

    schedule_ids = asyncio.run(_check())
    assert "poll_download_clients" in schedule_ids
