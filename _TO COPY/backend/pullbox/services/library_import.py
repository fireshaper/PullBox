"""Scan an existing on-disk comic library into importable series candidates.

This is the filesystem/parsing half of the library-import feature. It walks a
server-side folder, parses comic filenames into ``(series, issue, year)``, and
groups the files into candidate series. It performs **no** DB or ComicVine work
— the router (``routers/library_import.py``) drives ComicVine matching and row
creation. Kept dependency-free so it is trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pullbox.services.postprocess import COMIC_EXTENSIONS

# A 4-digit year in parentheses, e.g. "(2016)".
_YEAR_RE = re.compile(r"\((\d{4})\)")
# Bracketed / parenthesised junk tags: "(Digital)", "[Empire]", scene groups, etc.
_TAG_RE = re.compile(r"[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]")
# Folder that is just "Series Name (YYYY)".
_FOLDER_WITH_YEAR_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*$")
# Issue-number token: optional '#', digits, optional decimal — as a whole word.
_ISSUE_RE = re.compile(r"(?<![\w.])#?(\d{1,5}(?:\.\d+)?)(?![\w.])")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ScannedFileInfo:
    file_path: str
    issue_number: str | None


@dataclass
class ScannedSeriesInfo:
    title: str
    year: int | None
    files: list[ScannedFileInfo] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class LibraryScan:
    root: str
    series: list[ScannedSeriesInfo]
    unparsed_count: int


def _clean_title(raw: str) -> str:
    """Collapse whitespace and separators in a candidate series title."""
    cleaned = raw.replace("_", " ").replace(".", " ")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" -")
    return cleaned


def normalize_issue_number(value: str | None) -> str:
    """Normalize an issue number for matching (strip leading zeros / spaces).

    ``"012"`` → ``"12"``, ``"1.0"`` → ``"1"``, ``"0"`` → ``"0"``. Non-numeric
    tokens (annuals, "1a") are lowercased and stripped but otherwise preserved.
    """
    if value is None:
        return ""
    token = value.strip().lstrip("#").strip()
    if not token:
        return ""
    try:
        num = float(token)
    except ValueError:
        return token.lower()
    # Render as int when it's a whole number, else drop trailing zeros.
    if num == int(num):
        return str(int(num))
    return str(num).rstrip("0").rstrip(".")


def _extract_issue_number(stem: str) -> str | None:
    """Pull the most likely issue-number token out of a filename stem.

    Removes bracketed tags and the year first, then takes the last remaining
    number token (issue numbers usually trail the series name).
    """
    without_tags = _TAG_RE.sub(" ", stem)
    matches = _ISSUE_RE.findall(without_tags)
    if not matches:
        return None
    return matches[-1]


def parse_comic_filename(path: str | Path) -> tuple[str, str | None, int | None]:
    """Parse a comic file path into ``(series_title, issue_number, year)``.

    Prefers a ``Name (YYYY)`` parent folder for the title/year; otherwise parses
    the filename stem. Any component may come back empty/None if not present.
    """
    p = Path(path)
    stem = p.stem

    title = ""
    year: int | None = None

    # Prefer a "Series (YYYY)" parent folder for title + year.
    parent_match = _FOLDER_WITH_YEAR_RE.match(p.parent.name)
    if parent_match:
        title = _clean_title(parent_match.group("title"))
        year = int(parent_match.group("year"))

    # Year from the filename (fallback / fill-in).
    file_year_match = _YEAR_RE.search(stem)
    if year is None and file_year_match:
        year = int(file_year_match.group(1))

    issue_number = _extract_issue_number(stem)

    # Derive the title from the filename when the folder didn't give us one.
    if not title:
        working = _TAG_RE.sub(" ", stem)  # drop (2016), [Digital], etc.
        if issue_number is not None:
            # Remove the issue token (and a leading '#') from the title portion.
            working = re.sub(
                r"(?<![\w.])#?" + re.escape(issue_number) + r"(?![\w.])",
                " ",
                working,
                count=1,
            )
        title = _clean_title(working)

    if not title:
        title = _clean_title(p.parent.name) or "Unknown Series"

    return title, issue_number, year


def scan_library(root: str) -> LibraryScan:
    """Walk ``root`` recursively and group comic files into candidate series.

    Raises ``FileNotFoundError`` if the path doesn't exist and
    ``NotADirectoryError`` if it isn't a directory.
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(root)
    if not root_path.is_dir():
        raise NotADirectoryError(root)

    # Group by (lowercased title, year) so casing differences merge.
    groups: dict[tuple[str, int | None], ScannedSeriesInfo] = {}
    unparsed = 0

    for file in sorted(root_path.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in COMIC_EXTENSIONS:
            continue
        title, issue_number, year = parse_comic_filename(file)
        if not title:
            unparsed += 1
            continue
        key = (title.casefold(), year)
        group = groups.get(key)
        if group is None:
            group = ScannedSeriesInfo(title=title, year=year)
            groups[key] = group
        group.files.append(ScannedFileInfo(file_path=str(file), issue_number=issue_number))

    series = sorted(groups.values(), key=lambda s: (s.title.casefold(), s.year or 0))
    return LibraryScan(root=str(root_path), series=series, unparsed_count=unparsed)
