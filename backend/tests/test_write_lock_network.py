"""Background syncs must not hold SQLite's write lock across provider HTTP.

SQLite allows one writer, and a flush holds that lock until the transaction
commits. A sync that flushed and then made rate-limited provider calls kept it
for tens of seconds, so every other writer failed past busy_timeout (5s) with
"database is locked" — APScheduler's own bookkeeping included, which crashed the
scheduler and silently stopped download polling.

Each test runs the sync with one deliberately slow (10s) lookup and lands an
unrelated write mid-lookup; the write raises OperationalError if the lock is held.
Same shape as test_queue.test_process_job_releases_write_lock_during_search and
test_releases.test_refresh_week_releases_write_lock_during_publisher_lookup.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

SLOW = 10  # write lands at 2s, so a held lock makes it wait 8s — well past busy_timeout (5s)


@pytest.fixture
async def session_factory():
    """Real on-disk SQLite (busy_timeout and all) with tables created directly."""
    import pullbox.database as db_module
    from pullbox.config import Settings
    from pullbox.models import Base

    db_module.init_db(Settings())
    async with db_module._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db_module.AsyncSessionLocal
    await db_module._engine.dispose()
    db_module._engine = None
    db_module.AsyncSessionLocal = None


async def _write_mid_lookup(session_factory, sql: str, params: dict) -> bool:
    await asyncio.sleep(2)  # land while the slow lookup is in flight
    async with session_factory() as db:
        await db.execute(text(sql), params)
        await db.commit()
    return True


async def test_arc_sync_releases_write_lock_during_member_lookups(session_factory):
    from sqlalchemy import select

    from pullbox.models import Issue, StoryArc
    from pullbox.services.arc_sync import resolve_arc_members

    async with session_factory() as db:
        arc = StoryArc(comicvine_id="arc-1", name="Crossover")
        db.add(arc)
        await db.commit()
        arc_id = arc.id

    def _detail(n: str) -> dict:
        return {
            "comicvine_id": f"iss-{n}",
            "issue_number": n,
            "title": f"Part {n}",
            "series": {"comicvine_id": "ser-1", "title": "Crossover Series"},
            "story_arcs": [],
        }

    async def get_issue(*, metron_id=None, comicvine_id=None):
        # The first member resolves at once and is stored; the second is slow.
        # The old loop flushed the first before fetching the second.
        if comicvine_id == "iss-2":
            await asyncio.sleep(SLOW)
        return _detail(comicvine_id.removeprefix("iss-"))

    provider = AsyncMock()
    provider.get_story_arc.return_value = {
        "name": "Crossover (renamed)",
        "issues": [{"comicvine_id": "iss-1"}, {"comicvine_id": "iss-2"}],
    }
    provider.get_issue.side_effect = get_issue

    async def _sync():
        async with session_factory() as db:
            arc = await db.get(StoryArc, arc_id)
            result = await resolve_arc_members(db, provider, arc, budget=10)
            await db.commit()
            return result

    wrote, result = await asyncio.gather(
        _write_mid_lookup(
            session_factory,
            "UPDATE story_arcs SET description=:d WHERE id=:i",
            {"d": "written mid-lookup", "i": arc_id},
        ),
        _sync(),
    )

    assert wrote is True
    assert result.added == 2
    async with session_factory() as db:
        numbers = sorted((await db.execute(select(Issue.issue_number))).scalars().all())
        arc = await db.get(StoryArc, arc_id)
        assert numbers == ["1", "2"]
        assert arc.name == "Crossover (renamed)"


async def test_import_backfill_releases_write_lock_during_get_issues(session_factory):
    from pullbox.models import ImportFile, Issue, Series
    from pullbox.services.import_sync import resolve_series_for_import

    async with session_factory() as db:
        series = Series(title="Saga", start_year=2012)
        db.add(series)
        await db.flush()
        issue = Issue(series_id=series.id, issue_number="1", status="downloaded")
        db.add(issue)
        await db.flush()
        db.add(ImportFile(issue_id=issue.id, series_id=series.id, status="pending"))
        await db.commit()
        series_id = series.id

    async def get_issues(**_ids):
        await asyncio.sleep(SLOW)
        return [{"comicvine_id": "saga-1", "issue_number": "1", "title": "Chapter One"}]

    provider = AsyncMock()
    provider.search_series.return_value = [
        {"comicvine_id": "saga", "title": "Saga", "start_year": 2012, "publisher": "Image"}
    ]
    provider.get_issues.side_effect = get_issues

    async def _backfill():
        from sqlalchemy import select

        async with session_factory() as db:
            series = await db.get(Series, series_id)
            rows = (await db.execute(select(ImportFile))).scalars().all()
            counts = await resolve_series_for_import(db, provider, series, rows)
            await db.commit()
            return counts

    wrote, counts = await asyncio.gather(
        _write_mid_lookup(
            session_factory,
            "UPDATE issues SET title=:t WHERE series_id=:s",
            {"t": "written mid-lookup", "s": series_id},
        ),
        _backfill(),
    )

    assert wrote is True
    assert counts == (1, 0, 0)
    async with session_factory() as db:
        series = await db.get(Series, series_id)
        assert series.comicvine_id == "saga"
    provider.get_issues.assert_awaited_once_with(metron_id=None, comicvine_id="saga")
