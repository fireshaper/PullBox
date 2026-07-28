"""Post-download processing: locate, move/copy/hardlink and rename comic files.

Runs after a download client reports a job completed (see
``scheduler.poll_download_clients``). Given the client's completed output path
and a ``PostProcessingSettings`` row, it relocates the comic file into an
organized library folder and renames it using configurable patterns.

Requires PullBox to share a filesystem with the download client — the path the
client reports must be readable at the same location inside PullBox.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# File extensions we consider a "comic" when picking the payload out of a
# completed download directory.
COMIC_EXTENSIONS: frozenset[str] = frozenset(
    {".cbz", ".cbr", ".cbt", ".cb7", ".pdf", ".zip", ".rar"}
)

# Characters that are illegal in Windows path components (superset of POSIX).
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
# Matches {issue:3} style padded tokens.
_PADDED_ISSUE = re.compile(r"\{issue:(\d+)\}")


def sanitize_component(value: str) -> str:
    """Make a single path component safe: strip illegal chars, collapse spaces.

    Applied per-component so a pattern's own ``/`` separators still nest folders.
    """
    cleaned = _ILLEGAL_CHARS.sub("", value)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # Trailing dots/spaces are illegal on Windows directory names.
    return cleaned.rstrip(". ")


def build_tokens(issue, series, ext: str = "") -> dict[str, str]:
    """Build the substitution token map from an Issue + Series (or sample data).

    ``issue``/``series`` may be ORM models or any object exposing the same
    attributes. None values render as empty strings.
    """
    return {
        "series": str(getattr(series, "title", "") or ""),
        "publisher": str(getattr(series, "publisher", "") or ""),
        "year": str(getattr(series, "start_year", "") or ""),
        "issue": str(getattr(issue, "issue_number", "") or ""),
        "title": str(getattr(issue, "title", "") or ""),
        "ext": ext.lstrip(".") if ext else "",
    }


def render_pattern(pattern: str, tokens: dict[str, str]) -> str:
    """Render a pattern string, substituting ``{token}`` and ``{issue:N}``.

    ``{issue:N}`` zero-pads a purely-numeric issue number to width N; a
    non-numeric issue number is left unchanged. Unknown tokens are left as-is.
    """

    def _pad(match: re.Match[str]) -> str:
        width = int(match.group(1))
        issue = tokens.get("issue", "")
        return issue.zfill(width) if issue.isdigit() else issue

    result = _PADDED_ISSUE.sub(_pad, pattern)
    for key, value in tokens.items():
        result = result.replace("{" + key + "}", value)
    return result


def render_relative_path(folder_pattern: str, file_pattern: str, tokens: dict[str, str]) -> Path:
    """Render folder + file patterns into a sanitized relative Path (no extension)."""
    parts: list[str] = []
    for raw in render_pattern(folder_pattern, tokens).split("/"):
        component = sanitize_component(raw)
        if component:
            parts.append(component)
    filename = sanitize_component(render_pattern(file_pattern, tokens))
    parts.append(filename or "unnamed")
    return Path(*parts)


def find_comic_file(dest_path: str | os.PathLike[str]) -> Path | None:
    """Return the comic file at ``dest_path``.

    If ``dest_path`` is a file with a comic extension, return it. If it is a
    directory, return the largest comic file found recursively. Returns None if
    nothing matches or the path does not exist.
    """
    path = Path(dest_path)
    if path.is_file():
        return path if path.suffix.lower() in COMIC_EXTENSIONS else None
    if not path.is_dir():
        return None

    candidates = [
        p
        for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in COMIC_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _dedupe_target(target: Path) -> Path:
    """If ``target`` exists, append ' (1)', ' (2)'… before the extension."""
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _is_safe_to_remove(directory: Path, destination_root: Path) -> bool:
    """Guard against deleting anything that is not a disposable job folder.

    Refuses a drive/filesystem root (``G:\\``, ``/``) and the library
    destination root or any of its ancestors — a single-file download stored
    directly in the client's shared completed folder would otherwise make that
    shared folder the deletion candidate.
    """
    if directory.parent == directory:  # drive letter or filesystem root
        return False
    try:
        if destination_root == directory or destination_root.is_relative_to(directory):
            return False
    except (ValueError, OSError):  # unrelated roots / unresolvable path
        pass
    return True


def cleanup_empty_source_dirs(
    start: Path, top: Path, destination_root: Path
) -> list[Path]:
    """Remove ``start`` and its now-empty parents, up to and including ``top``.

    Walks upward from the directory that held the comic file so nested
    extraction folders collapse too. Stops at the first directory that is
    non-empty, unsafe to remove, outside ``top``, or that the OS refuses to
    delete. Returns the directories actually removed.

    ``top`` bounds the walk to what the download client actually reported, so a
    file report never deletes above its own folder. The one ambiguous case is a
    client configured *without* per-job folders: the reported file's parent is
    then the shared completed folder, which is removed if this move empties it.
    Any other download sitting there keeps it (the non-empty check), and the
    client recreates it on the next job.
    """
    removed: list[Path] = []
    current = start
    while True:
        if current != top:
            try:
                if not current.is_relative_to(top):
                    break
            except ValueError:  # different drives — not under top at all
                break
        if not _is_safe_to_remove(current, destination_root):
            break
        try:
            if any(current.iterdir()):
                break  # still holds files (nfo, par2, sample…) — leave it alone
            current.rmdir()
        except OSError:
            logger.debug("post-processing: could not remove %s", current, exc_info=True)
            break
        removed.append(current)
        if current == top:
            break
        current = current.parent
    return removed


def _perform(operation: str, source: Path, target: Path) -> None:
    """Move / copy / hardlink ``source`` to ``target``.

    Hardlink falls back to copy across filesystem boundaries (EXDEV).
    """
    if operation == "copy":
        shutil.copy2(source, target)
    elif operation == "hardlink":
        try:
            os.link(source, target)
        except OSError:
            logger.warning(
                "post-processing: hardlink failed (cross-device?), copying %s", source
            )
            shutil.copy2(source, target)
    else:  # move (default)
        shutil.move(str(source), str(target))


def apply_post_processing(issue, series, completed_path: str, cfg, library_root: str) -> str | None:
    """Relocate + rename the completed download's comic file.

    Returns the final absolute path on success, or None if no comic file was
    found. Raises on filesystem errors (caller decides how to handle).
    """
    comic = find_comic_file(completed_path)
    if comic is None:
        exists = Path(completed_path).exists()
        if not exists:
            logger.warning(
                "post-processing: path %r is not visible to PullBox (issue %s) — the "
                "download client's completed files must be reachable at the same path on "
                "the host running PullBox (shared drive / same machine / matching mount)",
                completed_path,
                getattr(issue, "id", "?"),
            )
        else:
            logger.warning(
                "post-processing: %r exists but contains no comic file (issue %s); "
                "recognized extensions: %s",
                completed_path,
                getattr(issue, "id", "?"),
                ", ".join(sorted(COMIC_EXTENSIONS)),
            )
        return None

    ext = comic.suffix
    tokens = build_tokens(issue, series, ext=ext)
    root = Path((cfg.destination_root or "").strip() or library_root)
    relative = render_relative_path(cfg.folder_pattern, cfg.file_pattern, tokens)

    target = root / relative
    # Append the source extension unless the pattern already produced one.
    if target.suffix.lower() not in COMIC_EXTENSIONS:
        target = target.with_name(target.name + ext)

    target = _dedupe_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the client's job folder before the move, while the source still
    # exists: a file path's folder is its parent, a directory is itself.
    source_root = Path(completed_path)
    job_folder = source_root if source_root.is_dir() else source_root.parent

    _perform(cfg.operation, comic, target)

    logger.info("post-processing: %s → %s (%s)", comic, target, cfg.operation)

    # Copy/hardlink leave the original behind, so the folder is never empty.
    if cfg.operation == "move" and getattr(cfg, "delete_empty_folder", False):
        removed = cleanup_empty_source_dirs(comic.parent, job_folder, root)
        if removed:
            logger.info(
                "post-processing: removed %d empty source folder(s): %s",
                len(removed),
                ", ".join(str(p) for p in removed),
            )

    return str(target)
