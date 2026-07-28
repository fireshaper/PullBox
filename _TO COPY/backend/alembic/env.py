from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from pullbox.config import Settings
from pullbox.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url() -> str:
    """Return a synchronous SQLAlchemy URL for migrations.

    Alembic only runs plain DDL, so it has no need for the app's async driver.
    Using the async driver here was actively harmful: the app runs migrations in a
    worker thread (``asyncio.to_thread`` in the lifespan) whose env.py then did a
    *nested* ``asyncio.run``. On some platforms aiosqlite's own worker thread cannot
    coordinate with that nested event loop and the first query deadlocks — startup
    hangs forever right after "Will assume non-transactional DDL". Stripping the
    async driver (``sqlite+aiosqlite`` → ``sqlite``) runs migrations synchronously
    on the stdlib sqlite3 driver, sidestepping asyncio/threads entirely.
    """
    return Settings().database_url.replace("+aiosqlite", "")


def run_migrations_online():
    engine = create_engine(_sync_database_url())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline migrations are not supported. Run: alembic upgrade head"
    )
else:
    run_migrations_online()
