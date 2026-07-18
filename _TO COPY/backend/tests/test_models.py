"""Tests for Step 2.2 — ORM models and the updated_at auto-update event."""

import asyncio

import pytest
from sqlalchemy import text

import pullbox.database as db_module
from pullbox.config import Settings
from pullbox.models import Base, Issue, Series


@pytest.fixture
async def db_session():
    """Provide an initialized session with all tables created.

    Uses AsyncSessionLocal (which has the before_flush event registered) so the
    updated_at auto-update behaviour can be tested.
    """
    settings = Settings()  # reads PULLBOX_DATABASE_URL from conftest
    db_module.init_db(settings)

    # Create all tables directly (no Alembic needed for unit tests)
    async with db_module._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db_module.AsyncSessionLocal() as session:
        yield session

    await db_module._engine.dispose()
    db_module._engine = None
    db_module.AsyncSessionLocal = None


async def test_insert_and_read_series(db_session):
    """Insert a Series row and read it back; confirm defaults are applied."""
    series = Series(
        comicvine_id="12345",
        title="Batman",
        publisher="DC Comics",
    )
    db_session.add(series)
    await db_session.flush()
    await db_session.refresh(series)

    assert series.id is not None
    assert series.subscribed is False
    assert series.auto_download is False
    assert series.status == "ongoing"
    assert series.title == "Batman"


async def test_all_five_tables_exist(db_session):
    """Confirm all five tables are present in the database."""
    result = await db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    )
    tables = {row[0] for row in result.fetchall()}
    assert tables >= {"series", "issues", "indexers", "download_jobs", "weekly_releases"}


async def _make_issue(db_session, comicvine_id: str, title: str) -> Issue:
    """Helper: insert a parent Series and a child Issue, return the Issue."""
    series = Series(comicvine_id=f"s-{comicvine_id}", title=f"Series for {comicvine_id}")
    db_session.add(series)
    await db_session.flush()

    issue = Issue(
        series_id=series.id,
        comicvine_id=comicvine_id,
        issue_number="1",
        title=title,
    )
    db_session.add(issue)
    await db_session.flush()
    await db_session.refresh(issue)
    return issue


async def test_updated_at_advances_on_flush(db_session):
    """The before_flush event must advance updated_at when a row is dirtied."""
    issue = await _make_issue(db_session, "upd-001", "Original Title")
    original_updated_at = issue.updated_at

    # Allow clock to advance (important on Windows where datetime resolution is ~10ms)
    await asyncio.sleep(0.05)

    issue.title = "Updated Title"
    await db_session.flush()
    await db_session.refresh(issue)

    assert issue.updated_at > original_updated_at, (
        "updated_at should have advanced after flush"
    )


async def test_updated_at_unchanged_for_clean_row(db_session):
    """The before_flush event must NOT touch rows that were not modified."""
    issue_a = await _make_issue(db_session, "clean-001", "Issue A")
    issue_b = await _make_issue(db_session, "clean-002", "Issue B")

    original_b_updated_at = issue_b.updated_at

    await asyncio.sleep(0.05)

    # Only mutate issue_a
    issue_a.title = "Issue A (modified)"
    await db_session.flush()
    await db_session.refresh(issue_b)

    assert issue_b.updated_at == original_b_updated_at, (
        "issue_b was not modified; its updated_at should be unchanged"
    )
