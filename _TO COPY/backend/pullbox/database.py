from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from pullbox.config import Settings

_engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


# async_sessionmaker does not propagate ORM session events — register on the
# underlying sync Session class so the listener fires for all AsyncSession calls.
@event.listens_for(Session, "before_flush")
def _set_updated_at(session, _flush_ctx, _instances):
    for obj in session.dirty:
        if hasattr(obj, "updated_at"):
            obj.updated_at = datetime.utcnow()


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return (or create) the shared async SQLAlchemy engine.

    On first call, creates the engine from settings and caches it.
    Subsequent calls return the cached engine.
    Alembic's env.py imports this function to ensure consistent configuration.
    """
    global _engine
    if _engine is not None:
        return _engine
    if settings is None:
        settings = Settings()
    engine = create_async_engine(settings.database_url, echo=settings.debug)
    if "sqlite" in settings.database_url:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_wal(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait up to 5s for a write lock instead of failing immediately —
            # the API and the APScheduler datastore both write concurrently.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _engine = engine
    return engine


def init_db(settings: Settings) -> None:
    """Initialize the engine and session factory. Called once during app lifespan startup."""
    global AsyncSessionLocal
    engine = get_engine(settings)

    if AsyncSessionLocal is not None:
        return  # Already initialized — idempotent

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    AsyncSessionLocal = factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    assert AsyncSessionLocal is not None, "Database not initialized — call init_db() first"
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
