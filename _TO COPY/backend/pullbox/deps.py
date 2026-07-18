"""Shared FastAPI dependency functions.

Centralised here so routers can import without creating a circular
dependency on pullbox.main.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.clients.comicvine import ComicVineClient
from pullbox.config import Settings
from pullbox.database import get_db

_settings: Settings | None = None


def get_settings() -> Settings:
    assert _settings is not None, "Settings not initialized"
    return _settings


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_comicvine_client(settings: SettingsDep):
    client = ComicVineClient(api_key=settings.comicvine_api_key)
    try:
        yield client
    finally:
        await client.close()


ComicVineClientDep = Annotated[ComicVineClient, Depends(get_comicvine_client)]
