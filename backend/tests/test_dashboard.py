"""Tests for the Dashboard API (activity, overview, pull) and bulk retry."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _add_series(
    title: str = "Test Series",
    *,
    publisher: str | None = "Test Publisher",
    subscribed: bool = False,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        s = Series(
            comicvine_id=f"cv-{title}-{subscribed}",
            title=title,
            publisher=publisher,
            subscribed=subscribed,
        )
        db.add(s)
        await db.commit()
        return s.id


async def _add_issue(
    series_id: int,
    *,
    issue_number: str = "1",
    status: str = "wanted",
    file_path: str | None = None,
    cover_url: str | None = None,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Issue

    async with db_module.AsyncSessionLocal() as db:
        i = Issue(
            series_id=series_id,
            issue_number=issue_number,
            status=status,
            file_path=file_path,
            cover_url=cover_url,
        )
        db.add(i)
        await db.commit()
        return i.id


async def _add_job(
    issue_id: int,
    *,
    status: str = "queued",
    attempts: int = 0,
    source_type: str = "usenet",
) -> int:
    import pullbox.database as db_module
    from pullbox.models import DownloadJob

    async with db_module.AsyncSessionLocal() as db:
        j = DownloadJob(
            issue_id=issue_id,
            source_type=source_type,
            status=status,
            attempts=attempts,
        )
        db.add(j)
        await db.commit()
        return j.id


async def _add_weekly(series_id: int, issue_id: int, release_date: date) -> None:
    import pullbox.database as db_module
    from pullbox.models import WeeklyRelease

    async with db_module.AsyncSessionLocal() as db:
        db.add(WeeklyRelease(issue_id=issue_id, release_date=release_date, source="comicvine"))
        await db.commit()


async def _add_sync_status(key: str, *, success: bool, message: str) -> None:
    import pullbox.database as db_module
    from pullbox.services.sync_status import record_sync

    async with db_module.AsyncSessionLocal() as db:
        await record_sync(db, key, success=success, message=message)
        await db.commit()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── /api/dashboard/activity ───────────────────────────────────────────────────


def test_activity_empty(client):
    resp = client.get("/api/dashboard/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queue_health"] == {
        "queued": 0,
        "searching": 0,
        "pending": 0,
        "downloading": 0,
        "failed": 0,
    }
    assert data["active_downloads"] == []
    assert data["recent_completed"] == []
    assert data["recent_failed"] == []


def test_activity_counts_and_lists(client):
    sid = asyncio.run(_add_series("Batman"))
    downloading_issue = asyncio.run(_add_issue(sid, issue_number="1", status="downloading"))
    completed_issue = asyncio.run(_add_issue(sid, issue_number="2", status="downloaded"))
    failed_issue = asyncio.run(_add_issue(sid, issue_number="3", status="wanted"))
    asyncio.run(_add_job(downloading_issue, status="downloading"))
    asyncio.run(_add_job(completed_issue, status="completed"))
    asyncio.run(_add_job(failed_issue, status="failed", attempts=4))
    asyncio.run(_add_job(asyncio.run(_add_issue(sid, issue_number="4")), status="queued"))

    resp = client.get("/api/dashboard/activity")
    data = resp.json()

    assert data["queue_health"]["downloading"] == 1
    assert data["queue_health"]["failed"] == 1
    assert data["queue_health"]["queued"] == 1

    assert len(data["active_downloads"]) == 1
    assert data["active_downloads"][0]["issue"]["series_title"] == "Batman"
    assert len(data["recent_completed"]) == 1
    assert len(data["recent_failed"]) == 1
    assert data["recent_failed"][0]["attempts"] == 4


# ── /api/dashboard/overview ───────────────────────────────────────────────────


def test_overview_library_stats(client, tmp_path):
    comic = tmp_path / "issue.cbz"
    comic.write_bytes(b"x" * 2048)

    sid = asyncio.run(_add_series("Saga"))
    asyncio.run(_add_issue(sid, issue_number="1", status="downloaded", file_path=str(comic)))
    asyncio.run(_add_issue(sid, issue_number="2", status="wanted"))

    resp = client.get("/api/dashboard/overview")
    data = resp.json()
    stats = data["library_stats"]
    assert stats["total_series"] == 1
    assert stats["total_issues"] == 2
    assert stats["downloaded_issues"] == 1
    assert stats["storage_bytes"] == 2048
    assert len(data["recent_library"]) == 1
    assert data["recent_library"][0]["series_title"] == "Saga"


def test_overview_stuck_series_only_subscribed_over_threshold(client):
    # Subscribed series with a wanted issue that has retried 3+ times → stuck.
    stuck_sid = asyncio.run(_add_series("Stuck", subscribed=True))
    stuck_issue = asyncio.run(_add_issue(stuck_sid, status="wanted"))
    asyncio.run(_add_job(stuck_issue, status="failed", attempts=5))

    # Subscribed but only 1 attempt → not stuck.
    fresh_sid = asyncio.run(_add_series("Fresh", subscribed=True))
    fresh_issue = asyncio.run(_add_issue(fresh_sid, status="wanted"))
    asyncio.run(_add_job(fresh_issue, status="failed", attempts=1))

    # Not subscribed but many attempts → excluded.
    unsub_sid = asyncio.run(_add_series("Unsub", subscribed=False))
    unsub_issue = asyncio.run(_add_issue(unsub_sid, status="wanted"))
    asyncio.run(_add_job(unsub_issue, status="failed", attempts=9))

    resp = client.get("/api/dashboard/overview")
    stuck = resp.json()["stuck_series"]
    assert len(stuck) == 1
    assert stuck[0]["series_title"] == "Stuck"
    assert stuck[0]["wanted_count"] == 1
    assert stuck[0]["max_attempts"] == 5


def test_overview_sync_status(client):
    asyncio.run(_add_sync_status("comicvine_calendar", success=True, message="Synced week 2025-20"))
    asyncio.run(
        _add_sync_status("import_backfill", success=False, message="Rate limited by ComicVine")
    )

    resp = client.get("/api/dashboard/overview")
    sync = resp.json()["sync_status"]
    assert sync["calendar"]["success"] is True
    assert sync["calendar"]["message"] == "Synced week 2025-20"
    assert sync["backfill"]["success"] is False
    assert "Rate limited" in sync["backfill"]["message"]


# ── /api/dashboard/pull ───────────────────────────────────────────────────────


def test_pull_this_week_all_upcoming_subscribed(client):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    next_week = monday + timedelta(days=8)

    # This week: unsubscribed series still shows.
    unsub = asyncio.run(_add_series("This Week Unsub", subscribed=False))
    unsub_issue = asyncio.run(_add_issue(unsub, issue_number="1", status="wanted"))
    asyncio.run(_add_weekly(unsub, unsub_issue, monday))

    # Upcoming: only subscribed series show.
    sub = asyncio.run(_add_series("Upcoming Sub", subscribed=True))
    sub_issue = asyncio.run(_add_issue(sub, issue_number="7", status="unknown"))
    asyncio.run(_add_weekly(sub, sub_issue, next_week))

    unsub2 = asyncio.run(_add_series("Upcoming Unsub", subscribed=False))
    unsub2_issue = asyncio.run(_add_issue(unsub2, issue_number="7", status="unknown"))
    asyncio.run(_add_weekly(unsub2, unsub2_issue, next_week))

    resp = client.get("/api/dashboard/pull")
    assert resp.status_code == 200
    data = resp.json()
    this_week_titles = {r["series_title"] for r in data["this_week"]}
    upcoming_titles = {r["series_title"] for r in data["upcoming"]}
    assert "This Week Unsub" in this_week_titles
    assert upcoming_titles == {"Upcoming Sub"}


# ── /api/queue/retry-failed ───────────────────────────────────────────────────


def test_retry_failed_requeues_all(client):
    sid = asyncio.run(_add_series("Retry Me"))
    i1 = asyncio.run(_add_issue(sid, issue_number="1", status="wanted"))
    i2 = asyncio.run(_add_issue(sid, issue_number="2", status="wanted"))
    asyncio.run(_add_job(i1, status="failed", attempts=3))
    asyncio.run(_add_job(i2, status="failed", attempts=2))
    asyncio.run(_add_job(asyncio.run(_add_issue(sid, issue_number="3")), status="queued"))

    with patch("pullbox.routers.queue.run_job_now", new=AsyncMock()):
        resp = client.post("/api/queue/retry-failed")
    assert resp.status_code == 200
    assert resp.json() == {"retried": 2}

    # Both failed jobs are now queued.
    activity = client.get("/api/dashboard/activity").json()
    assert activity["queue_health"]["failed"] == 0
    assert activity["queue_health"]["queued"] == 3
