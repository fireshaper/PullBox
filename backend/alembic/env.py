import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from pullbox.config import Settings
from pullbox.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    # Create a fresh engine from Settings for each migration run.
    # This avoids sharing the app's singleton engine across event loops,
    # which is unsafe with SQLAlchemy async when migrations run in a thread.
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise RuntimeError(
        "Offline migrations are not supported. Run: alembic upgrade head"
    )
else:
    run_migrations_online()
