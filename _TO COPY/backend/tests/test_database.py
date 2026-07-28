"""Tests for Step 2.1 — SQLAlchemy async engine and get_db() dependency."""

import pytest
from sqlalchemy import text

import pullbox.database as db_module
from pullbox.config import Settings


@pytest.fixture
async def initialized_db():
    """Initialize the database module with the temp DB URL from conftest."""
    settings = Settings()  # reads PULLBOX_DATABASE_URL set by reset_db_globals
    db_module.init_db(settings)
    yield
    if db_module._engine is not None:
        await db_module._engine.dispose()
        db_module._engine = None
        db_module.AsyncSessionLocal = None


async def test_get_db_executes_select_one(initialized_db):
    """get_db() yields a working session that can execute a raw SELECT 1."""
    results = []
    async for session in db_module.get_db():
        result = await session.execute(text("SELECT 1"))
        results.append(result.scalar())

    assert results == [1]


async def test_init_db_idempotent():
    """Calling init_db() twice with the same settings is a no-op (doesn't raise)."""
    settings = Settings()
    db_module.init_db(settings)
    engine_after_first = db_module._engine
    factory_after_first = db_module.AsyncSessionLocal

    db_module.init_db(settings)

    assert db_module._engine is engine_after_first
    assert db_module.AsyncSessionLocal is factory_after_first

    await db_module._engine.dispose()
    db_module._engine = None
    db_module.AsyncSessionLocal = None


async def test_get_db_rollback_on_exception(initialized_db):
    """get_db() rolls back the session and re-raises on exception."""
    with pytest.raises(ValueError, match="forced error"):
        async for session in db_module.get_db():
            await session.execute(text("SELECT 1"))
            raise ValueError("forced error")


async def test_get_db_asserts_without_init():
    """get_db() raises AssertionError if init_db() was never called."""
    # conftest already reset the globals to None
    with pytest.raises(AssertionError, match="init_db"):
        async for _ in db_module.get_db():
            pass
