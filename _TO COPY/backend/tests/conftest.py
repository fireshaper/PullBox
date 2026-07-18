"""Shared pytest fixtures for all backend tests."""

import pytest

import pullbox.database as db_module


@pytest.fixture(autouse=True)
def reset_db_globals(tmp_path, monkeypatch):
    """Reset database module globals and redirect DB to a temp file before each test.

    This ensures test isolation: each test gets a fresh SQLite file so migrations
    and data never bleed between tests. Works for both sync (TestClient) and async tests.
    """
    # Reset engine state so the next Settings() + init_db() creates a fresh engine
    db_module._engine = None
    db_module.AsyncSessionLocal = None

    # Reset the process-global ComicVine rate limiter so throttle state (and the
    # min-interval sleep) doesn't leak between tests. Tests that exercise the
    # limiter configure it explicitly; everything else runs un-throttled.
    import pullbox.clients.comicvine as cv_module

    cv_module.reset_rate_limiter()

    # Point all database operations at a per-test temp file instead of /config/pullbox.db
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("PULLBOX_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    yield

    # Reset after test — don't await dispose here (sync fixture); engine is GC'd
    db_module._engine = None
    db_module.AsyncSessionLocal = None
