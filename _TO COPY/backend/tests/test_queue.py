"""Tests for Phase 7: Download Queue & Retry Engine (steps 7.1–7.5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pullbox.deps import get_metadata_provider
from pullbox.main import app

# ── Shared test data ──────────────────────────────────────────────────────────

FAKE_VOLUME = {
    "metron_id": "m77001",
    "comicvine_id": "77001",
    "title": "Batman",
    "publisher": "DC Comics",
    "start_year": 2016,
    "cover_url": None,
    "description": None,
    "issue_count": 2,
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
    },
    {
        "metron_id": "m700002",
        "comicvine_id": "700002",
        "issue_number": "2",
        "title": "Second Issue",
        "cover_date": "2016-02-01",
        "store_date": None,
        "cover_url": None,
        "description": None,
    },
]


def _make_mock_provider(*, volume=None, issues=None):
    mock = AsyncMock()
    mock.get_volume.return_value = volume if volume is not None else FAKE_VOLUME
    if issues is not None:
        mock.get_issues.return_value = issues
    mock.get_issue.return_value = {"metron_id": None, "comicvine_id": None, "story_arcs": []}

    async def _override():
        yield mock

    return _override


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(client):
    """Add a series with two synced issues. Returns (series_id, issues) where
    issues[0] has been marked 'wanted'."""
    app.dependency_overrides[get_metadata_provider] = _make_mock_provider(volume=FAKE_VOLUME)
    try:
        series_id = client.post("/api/series/", json={"comicvine_id": "77001"}).json()["id"]
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    app.dependency_overrides[get_metadata_provider] = _make_mock_provider(issues=FAKE_ISSUES)
    try:
        client.post(f"/api/series/{series_id}/sync-issues")
    finally:
        app.dependency_overrides.pop(get_metadata_provider, None)

    issues = client.get(f"/api/series/{series_id}/issues").json()

    # Mark first issue as wanted
    client.post(f"/api/issues/{issues[0]['id']}/want")

    return series_id, issues


# ── Step 7.1 — enqueue_issue() service ───────────────────────────────────────


def test_enqueue_creates_queued_job(client, seeded):
    """enqueue_issue() creates a DownloadJob with status='queued'."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_issue

    async def _run():
        async with AsyncSessionLocal() as db:
            job, created = await enqueue_issue(wanted_id, db)
            await db.commit()
            return job.id, job.status, created

    job_id, status, created = asyncio.run(_run())
    assert created is True
    assert status == "queued"
    assert job_id is not None


def test_enqueue_duplicate_returns_existing_job(client, seeded):
    """enqueue_issue() returns existing job (created=False) when active job exists."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_issue

    async def _run():
        async with AsyncSessionLocal() as db:
            job1, created1 = await enqueue_issue(wanted_id, db)
            await db.commit()
        async with AsyncSessionLocal() as db:
            job2, created2 = await enqueue_issue(wanted_id, db)
            return job1.id, created1, job2.id, created2

    id1, c1, id2, c2 = asyncio.run(_run())
    assert c1 is True
    assert c2 is False
    assert id1 == id2  # same job returned


def test_enqueue_non_wanted_raises_value_error(client, seeded):
    """enqueue_issue() raises ValueError for issues in terminal status (skipped)."""
    _, issues = seeded
    non_wanted_id = issues[1]["id"]
    # Mark the issue as skipped so enqueue_issue() rejects it
    client.post(f"/api/issues/{non_wanted_id}/skip")

    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_issue

    async def _run():
        async with AsyncSessionLocal() as db:
            await enqueue_issue(non_wanted_id, db)

    with pytest.raises(ValueError, match="skipped"):
        asyncio.run(_run())


def test_enqueue_missing_issue_raises_value_error(client):
    """enqueue_issue() raises ValueError for a non-existent issue_id."""
    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_issue

    async def _run():
        async with AsyncSessionLocal() as db:
            await enqueue_issue(99999, db)

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(_run())


# ── Step 7.2 — process_job() backoff and pending logic ───────────────────────


def test_process_job_no_results_sets_failed_with_backoff(client, seeded):
    """process_job() with no search results: status='failed' and backoff schedule.

    Backoff: min(2**(attempts-1), 7) days after each failure.
    """
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue, process_job

    # Create the job
    async def _enqueue():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            return job.id

    job_id = asyncio.run(_enqueue())
    settings = Settings()

    def _process_and_check():
        async def _inner():
            async with AsyncSessionLocal() as db:
                with patch(
                    "pullbox.services.queue.fan_out_search", new=AsyncMock(return_value=[])
                ):
                    await process_job(job_id, db, settings)
                    await db.commit()
            async with AsyncSessionLocal() as db:
                job = await db.get(DownloadJob, job_id)
                return job.attempts, job.next_attempt_at, job.status

        return asyncio.run(_inner())

    # Attempt 1 → 1 day
    attempts, naa, status = _process_and_check()
    assert attempts == 1
    assert status == "failed"
    now = datetime.now(tz=timezone.utc)
    expected = now + timedelta(days=1)
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 10

    # Attempt 2 → 2 days
    attempts, naa, _ = _process_and_check()
    assert attempts == 2
    expected = now + timedelta(days=2)
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 10

    # Attempt 3 → 4 days
    attempts, naa, _ = _process_and_check()
    assert attempts == 3
    expected = now + timedelta(days=4)
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 10

    # Attempt 4 → 7 days (cap)
    attempts, naa, _ = _process_and_check()
    assert attempts == 4
    expected = now + timedelta(days=7)
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 10

    # Attempt 5 → still 7 days (cap holds)
    attempts, naa, _ = _process_and_check()
    assert attempts == 5
    expected = now + timedelta(days=7)
    assert abs((naa.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 10


def test_process_job_at_max_retries_sets_failed_permanently(client, seeded):
    """After max_retries attempts, next_attempt_at=None (permanent failure)."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.config import Settings
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue, process_job

    # Use a small max_retries for speed
    settings = Settings(max_retries=2)

    async def _enqueue():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            return job.id

    job_id = asyncio.run(_enqueue())

    for _ in range(settings.max_retries):
        async def _process():
            async with AsyncSessionLocal() as db:
                with patch(
                    "pullbox.services.queue.fan_out_search", new=AsyncMock(return_value=[])
                ):
                    await process_job(job_id, db, settings)
                    await db.commit()

        asyncio.run(_process())

    async def _check():
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            return job.next_attempt_at, job.status

    naa, status = asyncio.run(_check())
    assert naa is None
    assert status == "failed"


def test_process_job_with_results_sets_pending(client, seeded):
    """process_job() with search results: status='pending', result_guid populated."""
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
        guid="unique-guid-abc",
        download_url="http://example.com/batman1.nzb",
        score=3.5,
    )

    async def _run():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id

        async with AsyncSessionLocal() as db:
            with patch(
                "pullbox.services.queue.fan_out_search",
                new=AsyncMock(return_value=[fake_result]),
            ):
                await process_job(job_id, db, Settings())
                await db.commit()

        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            return job.status, job.result_guid, job.result_title

    status, guid, title = asyncio.run(_run())
    assert status == "pending"
    assert guid == "unique-guid-abc"
    assert title == "Batman 1"


# ── Step 7.3 — Queue API endpoints ───────────────────────────────────────────


def test_queue_router_registered(client):
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    assert any(p.startswith("/api/queue") for p in paths)


def test_enqueue_via_api_returns_201(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    resp = client.post(f"/api/queue/enqueue/{wanted_id}")
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "queued"
    assert data["issue_id"] == wanted_id
    assert data["id"] is not None


def test_enqueue_via_api_returns_409_on_duplicate(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    r1 = client.post(f"/api/queue/enqueue/{wanted_id}")
    assert r1.status_code == 201
    r2 = client.post(f"/api/queue/enqueue/{wanted_id}")
    assert r2.status_code == 409


def test_enqueue_via_api_returns_400_for_non_wanted(client, seeded):
    _, issues = seeded
    non_wanted_id = issues[1]["id"]
    # Mark as skipped so enqueue endpoint rejects it with 400
    client.post(f"/api/issues/{non_wanted_id}/skip")
    resp = client.post(f"/api/queue/enqueue/{non_wanted_id}")
    assert resp.status_code == 400


def test_list_queue_returns_active_jobs(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    client.post(f"/api/queue/enqueue/{wanted_id}")

    resp = client.get("/api/queue/")
    assert resp.status_code == 200
    ids = [j["issue_id"] for j in resp.json()]
    assert wanted_id in ids


def test_list_queue_excludes_completed_jobs(client, seeded):
    """Completed jobs must not appear in the queue list."""
    _, issues = seeded
    wanted_id = issues[0]["id"]
    enqueue_resp = client.post(f"/api/queue/enqueue/{wanted_id}")
    job_id = enqueue_resp.json()["id"]

    # Force job to 'completed'
    async def _complete():
        from pullbox.database import AsyncSessionLocal
        from pullbox.models import DownloadJob

        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            job.status = "completed"
            await db.commit()

    asyncio.run(_complete())

    resp = client.get("/api/queue/")
    ids = [j["id"] for j in resp.json()]
    assert job_id not in ids


def test_list_queue_status_filter(client, seeded):
    """?status= filter restricts results to the given status."""
    _, issues = seeded
    wanted_id = issues[0]["id"]
    client.post(f"/api/queue/enqueue/{wanted_id}")

    resp = client.get("/api/queue/?status=queued")
    assert resp.status_code == 200
    assert all(j["status"] == "queued" for j in resp.json())

    resp2 = client.get("/api/queue/?status=failed")
    assert resp2.status_code == 200
    assert resp2.json() == []


def test_retry_failed_job_resets_to_queued(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    enqueue_resp = client.post(f"/api/queue/enqueue/{wanted_id}")
    job_id = enqueue_resp.json()["id"]

    # Force to failed
    async def _fail():
        from pullbox.database import AsyncSessionLocal
        from pullbox.models import DownloadJob

        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            job.status = "failed"
            await db.commit()

    asyncio.run(_fail())

    resp = client.post(f"/api/queue/retry/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    # next_attempt_at should be within the last 5 seconds
    naa = datetime.fromisoformat(data["next_attempt_at"].replace("Z", "+00:00"))
    now = datetime.now(tz=timezone.utc)
    assert abs((now - naa).total_seconds()) < 10


def test_retry_non_failed_job_returns_400(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    enqueue_resp = client.post(f"/api/queue/enqueue/{wanted_id}")
    job_id = enqueue_resp.json()["id"]

    # Force job into 'completed' so retry correctly rejects it with 400
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob

    async def _set_completed():
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            job.status = "completed"
            await db.commit()

    asyncio.run(_set_completed())

    resp = client.post(f"/api/queue/retry/{job_id}")
    assert resp.status_code == 400


def test_retry_missing_job_returns_404(client):
    resp = client.post("/api/queue/retry/99999")
    assert resp.status_code == 404


def test_delete_job_returns_204(client, seeded):
    _, issues = seeded
    wanted_id = issues[0]["id"]
    enqueue_resp = client.post(f"/api/queue/enqueue/{wanted_id}")
    job_id = enqueue_resp.json()["id"]

    resp = client.delete(f"/api/queue/{job_id}")
    assert resp.status_code == 204

    list_resp = client.get("/api/queue/")
    ids = [j["id"] for j in list_resp.json()]
    assert job_id not in ids


def test_delete_missing_job_returns_404(client):
    resp = client.delete("/api/queue/99999")
    assert resp.status_code == 404


# ── Step 7.4 — APScheduler integration ───────────────────────────────────────


def test_scheduler_tables_created_on_startup(client):
    """APScheduler 'schedules' table must exist in the DB after the app starts."""
    from sqlalchemy import text

    async def _check():
        from pullbox.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            return {row[0] for row in result.fetchall()}

    tables = asyncio.run(_check())
    # APScheduler 4.x creates: schedules, jobs, job_results, tasks, metadata
    assert "schedules" in tables
    assert "jobs" in tables


def test_scheduler_both_jobs_registered(client):
    """Both 'daily_queue_sweep' and 'nightly_calendar_refresh' schedules exist."""
    from sqlalchemy import text

    async def _check():
        from pullbox.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT id FROM schedules"))
            return {row[0] for row in result.fetchall()}

    schedule_ids = asyncio.run(_check())
    assert "daily_queue_sweep" in schedule_ids
    assert "nightly_calendar_refresh" in schedule_ids


# ── Step 7.5 — daily_queue_sweep ─────────────────────────────────────────────


def test_daily_sweep_processes_due_job(client, seeded):
    """daily_queue_sweep() processes jobs whose next_attempt_at is past."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue

    # Enqueue and backdate next_attempt_at
    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            job.next_attempt_at = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
            await db.commit()
        return job_id

    job_id = asyncio.run(_setup())

    from pullbox.scheduler import daily_queue_sweep

    with patch("pullbox.services.queue.fan_out_search", new=AsyncMock(return_value=[])):
        asyncio.run(daily_queue_sweep())

    async def _check():
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            return job.status, job.attempts

    status, attempts = asyncio.run(_check())
    assert status == "failed"
    assert attempts == 1


def test_daily_sweep_skips_future_jobs(client, seeded):
    """daily_queue_sweep() does not process jobs with future next_attempt_at."""
    _, issues = seeded
    wanted_id = issues[0]["id"]

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob
    from pullbox.services.queue import enqueue_issue

    async def _setup():
        async with AsyncSessionLocal() as db:
            job, _ = await enqueue_issue(wanted_id, db)
            await db.commit()
            job_id = job.id
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            job.next_attempt_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
            await db.commit()
        return job_id

    job_id = asyncio.run(_setup())

    from pullbox.scheduler import daily_queue_sweep

    with patch("pullbox.services.queue.fan_out_search", new=AsyncMock(return_value=[])):
        asyncio.run(daily_queue_sweep())

    async def _check():
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, job_id)
            return job.status, job.attempts

    status, attempts = asyncio.run(_check())
    assert status == "queued"   # unchanged
    assert attempts == 0         # not processed


# ── Reconciling 'wanted' issues that have no job ─────────────────────────────


def _seed_issue_with_jobs(client, *, issue_status, job_specs):
    """Create one series + one issue with the given jobs. Returns the issue id.

    job_specs is a list of (status, next_attempt_at) tuples; an empty list leaves
    the issue with no jobs at all.
    """
    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob, Issue, Series

    async def _run():
        async with AsyncSessionLocal() as db:
            series = Series(title="Reconcile Test", comicvine_id="99001")
            db.add(series)
            await db.flush()
            issue = Issue(series_id=series.id, issue_number="1", status=issue_status)
            db.add(issue)
            await db.flush()
            for status, next_attempt_at in job_specs:
                db.add(
                    DownloadJob(
                        issue_id=issue.id,
                        source_type="usenet",
                        status=status,
                        attempts=1,
                        next_attempt_at=next_attempt_at,
                    )
                )
            await db.commit()
            return issue.id

    return asyncio.run(_run())


def _reconcile():
    from pullbox.database import AsyncSessionLocal
    from pullbox.services.queue import enqueue_orphaned_wanted

    async def _run():
        async with AsyncSessionLocal() as db:
            created = await enqueue_orphaned_wanted(db)
            await db.commit()
            return created

    return asyncio.run(_run())


def test_reconcile_enqueues_wanted_issue_with_no_job(client):
    """A 'wanted' issue that was never enqueued gets a job.

    This is the arc-sync / library-rescan case: those paths set status='wanted'
    without creating a DownloadJob, and the sweep only walks existing jobs.
    """
    issue_id = _seed_issue_with_jobs(client, issue_status="wanted", job_specs=[])

    created = _reconcile()

    assert len(created) == 1

    from pullbox.database import AsyncSessionLocal
    from pullbox.models import DownloadJob

    async def _check():
        async with AsyncSessionLocal() as db:
            job = await db.get(DownloadJob, created[0])
            return job.issue_id, job.status

    assert asyncio.run(_check()) == (issue_id, "queued")


def test_reconcile_skips_issue_with_backing_off_job(client):
    """A 'failed' job mid-backoff is still the queue's work — don't duplicate it.

    'failed' is not in ACTIVE_STATUSES, so a naive active-job check would create a
    second job for an issue that is simply waiting for its next attempt.
    """
    future = datetime.now(tz=timezone.utc) + timedelta(days=3)
    _seed_issue_with_jobs(client, issue_status="wanted", job_specs=[("failed", future)])

    assert _reconcile() == []


def test_reconcile_skips_issue_that_exhausted_retries(client):
    """A 'failed' job with next_attempt_at=None gave up deliberately; leave it alone.

    Re-enqueuing would reset attempts to 0 and silently defeat max_retries.
    """
    _seed_issue_with_jobs(client, issue_status="wanted", job_specs=[("failed", None)])

    assert _reconcile() == []


def test_reconcile_enqueues_when_only_completed_jobs_exist(client):
    """A previously-downloaded issue flipped back to 'wanted' is re-enqueued.

    This is the library-rescan case: the file went missing, so the completed job is
    history and the issue needs fetching again.
    """
    _seed_issue_with_jobs(client, issue_status="wanted", job_specs=[("completed", None)])

    assert len(_reconcile()) == 1


def test_reconcile_ignores_non_wanted_issues(client):
    """Only 'wanted' issues are reconciled — skipped/downloaded are left alone."""
    _seed_issue_with_jobs(client, issue_status="skipped", job_specs=[])

    assert _reconcile() == []


def test_reconcile_is_idempotent(client):
    """Running twice does not create a second job for the same issue."""
    _seed_issue_with_jobs(client, issue_status="wanted", job_specs=[])

    assert len(_reconcile()) == 1
    assert _reconcile() == []
