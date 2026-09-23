"""run_scheduler must bring the scheduler back after APScheduler crashes on a DB error."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

from apscheduler import ConflictPolicy
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import create_async_engine

import pullbox.scheduler as sched
from pullbox.config import Settings

ticks: list[int] = []


async def _tick() -> None:
    ticks.append(1)


def test_scheduler_restarts_after_datastore_crash(tmp_path, monkeypatch):
    """A 'database is locked' from release_job killed the scheduler for good: the
    old watchdog could not restart it ("not initialized"), so downloads were never
    polled again. run_scheduler must rebuild it and keep running jobs."""
    ticks.clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}")
    built: list[object] = []
    real_build = sched.build_scheduler

    def build(engine_):
        scheduler = real_build(engine_)
        # Short leases so the crashed job is released in seconds, not 30.
        scheduler.lease_duration = timedelta(seconds=2)
        if not built:
            # First instance only: fail the bookkeeping write after the first job,
            # exactly as SQLite lock contention does in production.
            async def locked(*args, **kwargs):
                raise sqlite3.OperationalError("database is locked")

            scheduler.data_store.release_job = locked
        built.append(scheduler)
        return scheduler

    async def register(scheduler, _settings):
        await scheduler.add_schedule(
            _tick, IntervalTrigger(seconds=1), id="tick", conflict_policy=ConflictPolicy.replace
        )

    monkeypatch.setattr(sched, "build_scheduler", build)
    monkeypatch.setattr(sched, "register_schedules", register)
    settings = Settings(scheduler_watchdog_interval_seconds=1)

    async def _run():
        task = asyncio.create_task(sched.run_scheduler(engine, settings))
        try:
            for _ in range(100):
                await asyncio.sleep(0.1)
                if len(built) >= 2 and len(ticks) >= 3:
                    break
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await engine.dispose()
        return task

    task = asyncio.run(_run())

    assert len(built) >= 2, "scheduler was never rebuilt after the crash"
    assert len(ticks) >= 3, "jobs did not keep running after the restart"
    assert task.cancelled(), "cancelling run_scheduler must stop it cleanly"
