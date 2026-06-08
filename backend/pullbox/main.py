import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

import pullbox.database as database
import pullbox.deps as deps
from pullbox.config import Settings

logger = logging.getLogger(__name__)

# backend/ directory — used to locate alembic.ini regardless of working directory
_BACKEND_DIR = Path(__file__).resolve().parent.parent


# Re-export so existing code that imports from pullbox.main still works
def get_settings() -> Settings:
    return deps.get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]

# These are re-exported for backwards compatibility and for test overrides
get_comicvine_client = deps.get_comicvine_client
ComicVineClientDep = deps.ComicVineClientDep


def _run_migrations() -> None:
    """Run Alembic migrations synchronously. Called via asyncio.to_thread from lifespan."""
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from pullbox.scheduler import build_scheduler, register_schedules

    # Route pullbox log records through uvicorn's handler after it has been configured.
    _uvicorn_handler = next(iter(logging.getLogger("uvicorn").handlers), None)
    _pullbox_log = logging.getLogger("pullbox")
    if _uvicorn_handler and not _pullbox_log.handlers:
        _pullbox_log.addHandler(_uvicorn_handler)
    _pullbox_log.setLevel(logging.DEBUG)

    deps._settings = Settings()
    database.init_db(deps._settings)
    await asyncio.to_thread(_run_migrations)

    engine = database.get_engine()
    scheduler = build_scheduler(engine)
    async with scheduler:
        await register_schedules(scheduler, deps._settings)
        await scheduler.start_in_background()
        logger.info("Database ready, scheduler started")
        yield
        logger.info("Scheduler stopping")

    if database._engine is not None:
        await database._engine.dispose()


app = FastAPI(title="PullBox", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health(settings: SettingsDep):
    return {"status": "ok", "debug": settings.debug}


# Routers — included after app is created to avoid circular imports
from pullbox.routers.download_clients import router as download_clients_router  # noqa: E402
from pullbox.routers.indexers import router as indexers_router  # noqa: E402
from pullbox.routers.issues import router as issues_router  # noqa: E402
from pullbox.routers.queue import router as queue_router  # noqa: E402
from pullbox.routers.releases import router as releases_router  # noqa: E402
from pullbox.routers.series import router as series_router  # noqa: E402

app.include_router(download_clients_router)
app.include_router(indexers_router)
app.include_router(issues_router)
app.include_router(queue_router)
app.include_router(releases_router)
app.include_router(series_router)


_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("pullbox.main:app", host="0.0.0.0", port=8585, reload=True)
