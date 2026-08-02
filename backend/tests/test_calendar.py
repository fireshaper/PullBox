"""Tests for the Calendar API (GET /api/calendar)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _add_series(
    title: str,
    *,
    publisher: str | None = "Test Publisher",
    subscribed: bool = False,
    auto_download: bool = False,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        s = Series(
            comicvine_id=f"cv-{title}",
            title=title,
            publisher=publisher,
            subscribed=subscribed,
            auto_download=auto_download,
        )
        db.add(s)
        await db.commit()
        return s.id


async def _add_issue(
    series_id: int,
    *,
    issue_number: str = "1",
    status: str = "unknown",
    store_date: date | None = None,
    cover_date: date | None = None,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Issue

    async with db_module.AsyncSessionLocal() as db:
        i = Issue(
            series_id=series_id,
            issue_number=issue_number,
            status=status,
            store_date=store_date,
            cover_date=cover_date,
        )
        db.add(i)
        await db.commit()
        return i.id


async def _add_job(issue_id: int, *, status: str) -> int:
    import pullbox.database as db_module
    from pullbox.models import DownloadJob

    async with db_module.AsyncSessionLocal() as db:
        j = DownloadJob(issue_id=issue_id, source_type="usenet", status=status)
        db.add(j)
        await db.commit()
        return j.id


async def _add_arc(name: str, *, subscribed: bool, issue_ids: list[int]) -> int:
    import pullbox.database as db_module
    from pullbox.models import Issue, StoryArc

    async with db_module.AsyncSessionLocal() as db:
        issues = [await db.get(Issue, iid) for iid in issue_ids]
        arc = StoryArc(
            comicvine_id=f"cv-arc-{name}",
            name=name,
            subscribed=subscribed,
            issues=issues,
        )
        db.add(arc)
        await db.commit()
        return arc.id


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _fetch(client, start="2026-08-01", end="2026-08-31", scope=None):
    url = f"/api/calendar?start={start}&end={end}"
    if scope:
        url += f"&scope={scope}"
    return client.get(url)


# ── Range and validation ──────────────────────────────────────────────────────


def test_empty_range(client):
    resp = _fetch(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["summary"] == {"total": 0, "pending": 0, "by_status": {}}
    assert data["scope"] == "subscribed"


def test_end_before_start_is_422(client):
    resp = _fetch(client, start="2026-08-31", end="2026-08-01")
    assert resp.status_code == 422


def test_oversized_range_is_422(client):
    resp = _fetch(client, start="2020-01-01", end="2026-01-01")
    assert resp.status_code == 422


def test_bad_scope_is_422(client):
    resp = _fetch(client, scope="everything")
    assert resp.status_code == 422


def test_dates_outside_range_are_excluded(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(_add_issue(sid, issue_number="1", store_date=date(2026, 7, 31)))
    asyncio.run(_add_issue(sid, issue_number="2", store_date=date(2026, 8, 15)))
    asyncio.run(_add_issue(sid, issue_number="3", store_date=date(2026, 9, 1)))

    data = _fetch(client).json()
    assert [e["issue_number"] for e in data["entries"]] == ["2"]


def test_undated_issues_never_appear(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(_add_issue(sid, issue_number="1"))

    data = _fetch(client).json()
    assert data["entries"] == []


# ── Date source ───────────────────────────────────────────────────────────────


def test_cover_date_is_the_fallback_when_no_store_date(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(_add_issue(sid, issue_number="1", cover_date=date(2026, 8, 10)))

    entry = _fetch(client).json()["entries"][0]
    assert entry["release_date"] == "2026-08-10"
    assert entry["date_source"] == "cover"


def test_store_date_wins_over_cover_date(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(
        _add_issue(
            sid,
            issue_number="1",
            store_date=date(2026, 8, 5),
            cover_date=date(2026, 8, 20),
        )
    )

    entry = _fetch(client).json()["entries"][0]
    assert entry["release_date"] == "2026-08-05"
    assert entry["date_source"] == "store"


# ── Scope ─────────────────────────────────────────────────────────────────────


def test_subscribed_scope_excludes_unsubscribed_series(client):
    sub = asyncio.run(_add_series("Batman", subscribed=True))
    unsub = asyncio.run(_add_series("Superman", subscribed=False))
    asyncio.run(_add_issue(sub, issue_number="1", store_date=date(2026, 8, 5)))
    asyncio.run(_add_issue(unsub, issue_number="1", store_date=date(2026, 8, 6)))

    data = _fetch(client).json()
    assert [e["series_title"] for e in data["entries"]] == ["Batman"]


def test_all_scope_includes_unsubscribed_series(client):
    sub = asyncio.run(_add_series("Batman", subscribed=True))
    unsub = asyncio.run(_add_series("Superman", subscribed=False))
    asyncio.run(_add_issue(sub, issue_number="1", store_date=date(2026, 8, 5)))
    asyncio.run(_add_issue(unsub, issue_number="1", store_date=date(2026, 8, 6)))

    data = _fetch(client, scope="all").json()
    assert sorted(e["series_title"] for e in data["entries"]) == ["Batman", "Superman"]


def test_subscribed_arc_pulls_in_an_unsubscribed_series_issue(client):
    unsub = asyncio.run(_add_series("Superman", subscribed=False))
    iid = asyncio.run(_add_issue(unsub, issue_number="7", store_date=date(2026, 8, 12)))
    asyncio.run(_add_arc("Crisis", subscribed=True, issue_ids=[iid]))

    entry = _fetch(client).json()["entries"][0]
    assert entry["issue_number"] == "7"
    assert entry["sources"] == ["arc"]
    assert entry["subscribed"] is False


def test_unsubscribed_arc_does_not_pull_in_an_issue(client):
    unsub = asyncio.run(_add_series("Superman", subscribed=False))
    iid = asyncio.run(_add_issue(unsub, issue_number="7", store_date=date(2026, 8, 12)))
    asyncio.run(_add_arc("Crisis", subscribed=False, issue_ids=[iid]))

    assert _fetch(client).json()["entries"] == []


def test_series_and_arc_sources_are_both_reported(client):
    sub = asyncio.run(_add_series("Batman", subscribed=True))
    iid = asyncio.run(_add_issue(sub, issue_number="1", store_date=date(2026, 8, 5)))
    asyncio.run(_add_arc("Crisis", subscribed=True, issue_ids=[iid]))

    entry = _fetch(client).json()["entries"][0]
    assert entry["sources"] == ["series", "arc"]


# ── Status and job annotation ─────────────────────────────────────────────────


def test_latest_job_status_is_attached(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    iid = asyncio.run(
        _add_issue(sid, issue_number="1", status="wanted", store_date=date(2026, 8, 5))
    )
    asyncio.run(_add_job(iid, status="queued"))
    asyncio.run(_add_job(iid, status="failed"))

    entry = _fetch(client).json()["entries"][0]
    assert entry["status"] == "wanted"
    assert entry["job_status"] == "failed"


def test_issue_with_no_job_has_null_job_status(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(_add_issue(sid, issue_number="1", store_date=date(2026, 8, 5)))

    assert _fetch(client).json()["entries"][0]["job_status"] is None


def test_summary_counts_outstanding_issues_only(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    for n, status in [
        ("1", "downloaded"),
        ("2", "downloading"),
        ("3", "skipped"),
        ("4", "wanted"),
        ("5", "unknown"),
    ]:
        asyncio.run(_add_issue(sid, issue_number=n, status=status, store_date=date(2026, 8, 5)))

    summary = _fetch(client).json()["summary"]
    assert summary["total"] == 5
    # downloaded / downloading / skipped are settled; wanted and unknown are not.
    assert summary["pending"] == 2
    assert summary["by_status"]["downloaded"] == 1


def test_entries_are_ordered_by_release_date(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True))
    asyncio.run(_add_issue(sid, issue_number="2", store_date=date(2026, 8, 20)))
    asyncio.run(_add_issue(sid, issue_number="1", store_date=date(2026, 8, 5)))

    data = _fetch(client).json()
    assert [e["release_date"] for e in data["entries"]] == ["2026-08-05", "2026-08-20"]


def test_series_flags_are_exposed(client):
    sid = asyncio.run(_add_series("Batman", subscribed=True, auto_download=True))
    asyncio.run(_add_issue(sid, issue_number="1", store_date=date(2026, 8, 5)))

    entry = _fetch(client).json()["entries"][0]
    assert entry["subscribed"] is True
    assert entry["auto_download"] is True
    assert entry["publisher"] == "Test Publisher"
    assert entry["series_id"] == sid
