"""Re-scan one series' folder on disk and report what is there.

This is the filesystem half of the series "Re-scan" action. It answers two
questions the database cannot: which comic files sit in the series' folder that
PullBox has never linked to an issue (a file dropped in by hand, or renamed
outside PullBox), and which tracked ``Issue.file_path`` values no longer point at
anything (a file deleted or moved away).

Deliberately DB-free and synchronous — the router drives it via
``asyncio.to_thread`` and owns all the row updates.

Where it looks: the folder post-processing *would* write this series into
(``destination_root`` + rendered ``folder_pattern``), plus the parent folder of
every file already tracked for the series — which covers libraries that were
imported rather than downloaded. The library root itself is never scanned as a
series folder: matching is by issue number alone, so sweeping a root shared with
every other series would happily attach some other title's ``#1`` to this series.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pullbox.services.file_health import iter_comic_files
from pullbox.services.general import normalize_path
from pullbox.services.library_import import normalize_issue_number, parse_comic_filename
from pullbox.services.postprocess import build_tokens, render_pattern, sanitize_component


def path_key(path: str) -> str:
    """Comparison key for a path: separators collapsed, case-folded.

    The same file can be spelled ``D:\\Comics\\a.cbz`` or ``D:/Comics/a.cbz``
    depending on who wrote the value; both must compare equal.
    """
    return normalize_path(path).casefold()


@dataclass
class ScannedFile:
    """One comic file found on disk, with its parsed issue number."""

    path: str
    issue_number: str  # normalized; "" when the filename has no number


@dataclass
class SeriesScan:
    folders: list[str] = field(default_factory=list)
    files: list[ScannedFile] = field(default_factory=list)
    # Keys (see ``path_key``) of the tracked paths that still exist on disk.
    present: set[str] = field(default_factory=set)


def render_series_folder(folder_pattern: str, series) -> str | None:
    """Render ``folder_pattern`` for ``series`` into a relative folder path.

    Returns None when the pattern renders empty (which would mean "the root").
    Mirrors ``postprocess.render_relative_path`` minus the filename component so
    the folder we scan is the folder post-processing writes to.
    """
    tokens = build_tokens(None, series)
    parts = [
        component
        for raw in render_pattern(folder_pattern, tokens).split("/")
        if (component := sanitize_component(raw))
    ]
    if not parts:
        return None
    return str(Path(*parts))


def series_folders(
    *, root: str, folder_pattern: str, series, tracked_paths: list[str]
) -> list[str]:
    """Folders worth scanning for ``series``, de-duplicated, root excluded.

    Order is stable: the pattern-derived folder first, then the folders holding
    files already tracked for this series.
    """
    root_key = path_key(root)
    candidates: list[str] = []

    rendered = render_series_folder(folder_pattern, series)
    if rendered:
        candidates.append(str(Path(root) / rendered))

    for tracked in tracked_paths:
        if tracked and tracked.strip():
            candidates.append(str(Path(tracked).parent))

    seen: set[str] = set()
    folders: list[str] = []
    for candidate in candidates:
        key = path_key(candidate)
        if not key or key == root_key or key in seen:
            continue
        seen.add(key)
        folders.append(candidate)
    return folders


def inspect(folders: list[str], tracked_paths: list[str]) -> SeriesScan:
    """Walk ``folders`` for comic files and check which tracked paths still exist."""
    scan = SeriesScan(folders=list(folders))

    seen: set[str] = set()
    for folder in folders:
        for file in iter_comic_files(folder):
            key = path_key(str(file))
            if key in seen:
                continue
            seen.add(key)
            _, issue_number, _ = parse_comic_filename(file)
            scan.files.append(
                ScannedFile(path=str(file), issue_number=normalize_issue_number(issue_number))
            )

    for tracked in tracked_paths:
        if not tracked or not tracked.strip():
            continue
        try:
            if os.path.isfile(tracked):
                scan.present.add(path_key(tracked))
        except OSError:  # pragma: no cover — unreadable mount, treat as missing
            continue

    return scan
