"""Inspect comic files on disk and report what is wrong with them.

The problem this solves: PullBox itself never opens a comic archive — it moves
and renames files (``services/postprocess.py``) and trusts the extension. So a
release that is really a RAR but was named ``.cbz`` lands in the library looking
fine, and the failure only surfaces later, in whatever reads it, as the classic
``File is not a zip file``. Same for truncated downloads, 0-byte files, and DB
rows pointing at files that were moved or deleted outside PullBox.

This module is the detector. It is deliberately synchronous, pure, and free of
DB/ORM imports so it is trivially unit-testable and safe to run in a worker
thread — the router drives it via ``asyncio.to_thread``.

Depth: the default pass reads only headers and the archive's central directory,
which is fast enough to sweep a whole library. ``deep=True`` additionally
CRC-verifies every entry (``testzip``), which catches silent bit-rot and
truncation mid-archive but reads every byte of every file.
"""

from __future__ import annotations

import logging
import os
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Problem kinds ─────────────────────────────────────────────────────────────
# Stored in FileIssue.kind. Keep these stable — the frontend maps them to labels.
MISSING = "missing"
EMPTY = "empty"
UNREADABLE = "unreadable"
WRONG_FORMAT = "wrong_format"
CORRUPT = "corrupt"
NO_IMAGES = "no_images"
UNKNOWN_FORMAT = "unknown_format"

ERROR = "error"
WARNING = "warning"

# Magic-byte signatures, longest-prefix first so RAR5 wins over the RAR4 prefix.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"Rar!\x1a\x07\x01\x00", "rar"),
    (b"Rar!\x1a\x07\x00", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty archive
    (b"PK\x07\x08", "zip"),  # spanned archive
    (b"%PDF-", "pdf"),
)

# What each extension claims the file is. Mirrors postprocess.COMIC_EXTENSIONS.
_EXPECTED_FORMAT: dict[str, str] = {
    ".cbz": "zip",
    ".zip": "zip",
    ".cbr": "rar",
    ".rar": "rar",
    ".cb7": "7z",
    ".cbt": "tar",
    ".pdf": "pdf",
}

# Human-facing names used in the detail messages.
_FORMAT_LABELS: dict[str, str] = {
    "zip": "ZIP",
    "rar": "RAR",
    "7z": "7-Zip",
    "tar": "TAR",
    "pdf": "PDF",
}

# The rename that fixes a mislabeled archive, keyed by what it actually is.
_CORRECT_EXTENSION: dict[str, str] = {
    "zip": ".cbz",
    "rar": ".cbr",
    "7z": ".cb7",
    "tar": ".cbt",
    "pdf": ".pdf",
}

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".jxl"}
)

# Bytes needed to cover the longest signature and the TAR magic at offset 257.
_HEADER_BYTES = 265
_TAR_MAGIC_OFFSET = 257


@dataclass
class FileProblem:
    """One thing wrong with one file."""

    kind: str
    severity: str
    detail: str
    size_bytes: int | None = None


def _label(fmt: str | None) -> str:
    return _FORMAT_LABELS.get(fmt or "", "an unrecognized format")


def detect_format(header: bytes) -> str | None:
    """Identify a file's real container format from its leading bytes.

    Returns a format key (``zip``/``rar``/``7z``/``tar``/``pdf``) or None when
    the header matches nothing known.
    """
    for signature, fmt in _SIGNATURES:
        if header.startswith(signature):
            return fmt
    # TAR has no leading magic — "ustar" sits at offset 257 in the first header
    # block. GNU tar writes "ustar  \0", POSIX writes "ustar\x0000".
    if header[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == b"ustar":
        return "tar"
    return None


def _has_images(names: list[str]) -> bool:
    return any(Path(n).suffix.lower() in _IMAGE_EXTENSIONS for n in names)


def _inspect_zip(path: Path, *, deep: bool) -> FileProblem | None:
    """Validate a real ZIP: central directory, entry list, optional CRC pass."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if deep:
                bad = zf.testzip()
                if bad is not None:
                    return FileProblem(
                        CORRUPT,
                        ERROR,
                        f"Archive is damaged — failed the CRC check on entry {bad!r}.",
                    )
    except zipfile.BadZipFile as exc:
        return FileProblem(
            CORRUPT,
            ERROR,
            f"ZIP structure is damaged and could not be read ({exc}). "
            "The download was probably truncated — re-download the issue.",
        )
    except OSError as exc:
        return FileProblem(UNREADABLE, ERROR, f"Could not read the file ({exc}).")

    if not _has_images(names):
        return FileProblem(
            NO_IMAGES,
            WARNING,
            f"Archive opens but contains no image pages ({len(names)} entr"
            f"{'y' if len(names) == 1 else 'ies'} inside).",
        )
    return None


def _inspect_tar(path: Path, *, deep: bool) -> FileProblem | None:
    """Validate a real TAR (.cbt). Stdlib ``tarfile``, same shape as the ZIP path."""
    try:
        with tarfile.open(path) as tf:
            names = tf.getnames()
            if deep:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    stream = tf.extractfile(member)
                    if stream is None:
                        continue
                    with stream:
                        while stream.read(1 << 20):
                            pass
    except tarfile.TarError as exc:
        return FileProblem(
            CORRUPT,
            ERROR,
            f"TAR structure is damaged and could not be read ({exc}).",
        )
    except OSError as exc:
        return FileProblem(UNREADABLE, ERROR, f"Could not read the file ({exc}).")

    if not _has_images(names):
        return FileProblem(
            NO_IMAGES, WARNING, "Archive opens but contains no image pages."
        )
    return None


def inspect_file(path: str | os.PathLike[str], *, deep: bool = False) -> FileProblem | None:
    """Check one comic file. Returns the problem found, or None if it looks fine.

    Only the first problem is reported — once a file is a RAR named ``.cbz``
    there is no point also complaining that the ZIP reader can't open it.
    """
    p = Path(path)

    try:
        stat = p.stat()
    except FileNotFoundError:
        return FileProblem(
            MISSING,
            ERROR,
            "The file is no longer at this path. It was moved or deleted outside "
            "PullBox, or the library volume is not mounted.",
        )
    except OSError as exc:
        return FileProblem(UNREADABLE, ERROR, f"Could not read the file ({exc}).")

    if not p.is_file():
        return FileProblem(
            MISSING, ERROR, "This path is a folder, not a comic file."
        )

    size = stat.st_size
    if size == 0:
        return FileProblem(
            EMPTY,
            ERROR,
            "The file is 0 bytes — the download never wrote any data.",
            size_bytes=0,
        )

    try:
        with p.open("rb") as fh:
            header = fh.read(_HEADER_BYTES)
    except OSError as exc:
        return FileProblem(
            UNREADABLE, ERROR, f"Could not read the file ({exc}).", size_bytes=size
        )

    actual = detect_format(header)
    expected = _EXPECTED_FORMAT.get(p.suffix.lower())

    if actual is None:
        problem = FileProblem(
            UNKNOWN_FORMAT,
            ERROR,
            "The contents do not match any known comic format (ZIP, RAR, 7-Zip, "
            "TAR or PDF). It may be an HTML error page or a partial download "
            "saved with a comic extension.",
        )
        problem.size_bytes = size
        return problem

    if expected is not None and actual != expected:
        # This is the "File is not a zip file" case, caught before a reader hits it.
        suggested = _CORRECT_EXTENSION.get(actual, "")
        detail = (
            f"Named {p.suffix.lower()} but the contents are {_label(actual)}. "
            f"Readers expecting {_label(expected)} fail on this"
        )
        if expected == "zip":
            detail += ' with "File is not a zip file"'
        detail += f". Renaming it to {suggested} fixes it." if suggested else "."
        return FileProblem(WRONG_FORMAT, ERROR, detail, size_bytes=size)

    # Contents match the extension — look inside where we can do so from stdlib.
    problem: FileProblem | None = None
    if actual == "zip":
        problem = _inspect_zip(p, deep=deep)
    elif actual == "tar":
        problem = _inspect_tar(p, deep=deep)
    # rar / 7z need third-party libraries to open; the header check above is as
    # far as we go. pdf is a single document — a valid header is enough.

    if problem is not None:
        problem.size_bytes = size
    return problem


def iter_comic_files(root: str | os.PathLike[str]) -> list[Path]:
    """Every comic file under ``root``, recursively. Empty list if unreadable."""
    from pullbox.services.postprocess import COMIC_EXTENSIONS  # noqa: PLC0415

    root_path = Path(root)
    if not root_path.is_dir():
        return []
    found: list[Path] = []
    for entry in root_path.rglob("*"):
        try:
            if entry.is_file() and entry.suffix.lower() in COMIC_EXTENSIONS:
                found.append(entry)
        except OSError:  # pragma: no cover — transient FS errors mid-walk
            continue
    return sorted(found)


@dataclass
class ScanResult:
    """One scanned path and whatever was wrong with it."""

    file_path: str
    problem: FileProblem


def scan_paths(paths: list[str], *, deep: bool = False) -> list[ScanResult]:
    """Inspect every path in order, keeping only the ones with a problem.

    Blocking and CPU/IO-bound — call it from a worker thread.
    """
    results: list[ScanResult] = []
    for raw in paths:
        try:
            problem = inspect_file(raw, deep=deep)
        except Exception:  # noqa: BLE001 — one bad file must not abort the sweep
            logger.warning("file health: inspection crashed on %r", raw, exc_info=True)
            problem = FileProblem(
                UNREADABLE, ERROR, "Inspection failed unexpectedly on this file."
            )
        if problem is not None:
            results.append(ScanResult(file_path=raw, problem=problem))
    return results
