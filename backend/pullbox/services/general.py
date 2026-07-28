"""General (app-wide) settings stored in the database.

The library root lives in two places: the config file (``Settings.library_path``,
read once at startup and not editable at runtime) and an optional DB override set
from Settings → General. Anything that needs the library root should call
``resolve_library_path`` so the precedence — DB override first, config value as
fallback — is applied the same way everywhere.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pullbox.models import GeneralSettings


async def get_or_create_general_settings(db: AsyncSession) -> GeneralSettings:
    """Return the singleton general-settings row, creating an empty one once."""
    row = (await db.execute(select(GeneralSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = GeneralSettings()
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


async def resolve_library_path(db: AsyncSession, fallback: str) -> str:
    """Return the effective library root: the DB override, else ``fallback``.

    Read-only — unlike ``get_or_create_general_settings`` this never inserts a
    row, so it is safe to call from the scheduler's polling loop.
    """
    row = (await db.execute(select(GeneralSettings).limit(1))).scalar_one_or_none()
    if row is not None:
        override = (row.library_path or "").strip()
        if override:
            return override
    return fallback


def is_absolute_path(value: str) -> bool:
    """True for POSIX (``/comics``), Windows (``C:\\comics``) and UNC roots.

    ``Path.is_absolute`` is platform-specific: on Windows it rejects ``/comics``,
    which is exactly the path a Docker deployment uses. Accept either flavor so
    the same value validates on a Windows dev box and a Linux server.
    """
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def normalize_path(value: str) -> str:
    """Collapse a path to forward slashes with no trailing separator.

    Purely textual — the path need not exist, and may use the *other* platform's
    separators (a Windows dev box reading a Docker-written ``/comics/...`` value,
    or vice versa), which is exactly why ``os.path`` is not used here.
    """
    return value.replace("\\", "/").rstrip("/")


def relative_to_library(file_path: str, library_path: str) -> str | None:
    """Return ``file_path`` relative to ``library_path``, or None if it's outside.

    This is the join key companion apps (Thwip) match on: both scan the same
    folder, but each may see it under a different mount point (``/comics`` in a
    container vs ``D:\\Comics`` on the host), so only the tail below the library
    root is portable. Comparison is case-insensitive because the same library on
    Windows can be reached through differently-cased paths; the returned value
    keeps its original casing.
    """
    root = normalize_path(library_path)
    full = normalize_path(file_path)
    if not root:
        return None
    prefix = root + "/"
    if not full.casefold().startswith(prefix.casefold()):
        return None
    return full[len(prefix) :] or None


def describe_path(path: str) -> tuple[bool, bool]:
    """Return ``(exists, writable)`` for ``path`` — advisory only, never raises.

    Shown in the UI so a typo or an unmounted volume is visible immediately
    instead of surfacing later as a failed post-download move.
    """
    try:
        exists = os.path.isdir(path)
        writable = exists and os.access(path, os.W_OK)
    except OSError:
        return False, False
    return exists, writable
