"""Helpers for recording and reading background-sync outcomes.

A ``SyncStatus`` row is a small breadcrumb the dashboard reads to tell the user
when a ComicVine-facing background operation last ran and whether it worked.
Callers (weekly-release refresh, imported-issue backfill) invoke
``record_sync`` after each run; failures store a short human-readable reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.models import SyncStatus

# Well-known keys.
CALENDAR = "comicvine_calendar"
IMPORT_BACKFILL = "import_backfill"


async def record_sync(
    db: AsyncSession, key: str, *, success: bool, message: str | None = None
) -> None:
    """Upsert the sync-status row for ``key``. Flushes but does not commit."""
    row = (
        await db.execute(select(SyncStatus).where(SyncStatus.key == key))
    ).scalar_one_or_none()
    now = datetime.now(tz=timezone.utc)
    trimmed = (message or None) and message[:300]
    if row is None:
        db.add(
            SyncStatus(key=key, last_run_at=now, success=success, message=trimmed)
        )
    else:
        row.last_run_at = now
        row.success = success
        row.message = trimmed
        row.updated_at = now
    await db.flush()


async def get_sync(db: AsyncSession, key: str) -> SyncStatus | None:
    return (
        await db.execute(select(SyncStatus).where(SyncStatus.key == key))
    ).scalar_one_or_none()
