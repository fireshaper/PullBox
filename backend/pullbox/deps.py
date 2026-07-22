"""Shared FastAPI dependency functions.

Centralised here so routers can import without creating a circular
dependency on pullbox.main.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.clients.comicvine import ComicVineClient
from pullbox.clients.metadata import ComicVineProvider, CompositeProvider, MetadataProvider
from pullbox.clients.metron import MetronClient
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


def build_metadata_provider(settings: Settings) -> CompositeProvider:
    """Construct the composite metadata provider from configured credentials.

    Metron (if credentials are set) and ComicVine (if an API key is set) are wired
    into a ``CompositeProvider`` whose primary is ``settings.metadata_provider``.
    Shared by the FastAPI dependency and the scheduler jobs.
    """
    metron = None
    if settings.metron_username and settings.metron_password:
        metron = MetronClient(settings.metron_username, settings.metron_password)

    comicvine = None
    if settings.comicvine_api_key:
        comicvine = ComicVineProvider(ComicVineClient(api_key=settings.comicvine_api_key))

    if metron is None and comicvine is None:
        # Nothing configured — hand back a ComicVine-backed provider anyway so callers
        # get a clear upstream error (empty key) rather than an AttributeError.
        comicvine = ComicVineProvider(ComicVineClient(api_key=settings.comicvine_api_key))

    return CompositeProvider(
        metron=metron, comicvine=comicvine, primary=settings.metadata_provider
    )


async def get_metadata_provider(settings: SettingsDep):
    """FastAPI dependency: yields a composite provider and closes it afterwards.

    Consumers depend on this instead of the concrete clients.
    """
    provider = build_metadata_provider(settings)
    try:
        yield provider
    finally:
        await provider.close()


MetadataProviderDep = Annotated[MetadataProvider, Depends(get_metadata_provider)]
