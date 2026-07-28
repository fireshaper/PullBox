"""Tests for the series folder re-scan helpers (services/series_scan.py)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pullbox.services import series_scan


@dataclass
class FakeSeries:
    title: str = "Batman"
    publisher: str | None = "DC Comics"
    start_year: int | None = 2016


DEFAULT_PATTERN = "{publisher}/{series} ({year})"


# ── render_series_folder ──────────────────────────────────────────────────────


def test_render_series_folder_uses_series_tokens():
    rendered = series_scan.render_series_folder(DEFAULT_PATTERN, FakeSeries())
    assert Path(rendered).parts == ("DC Comics", "Batman (2016)")


def test_render_series_folder_sanitizes_illegal_characters():
    rendered = series_scan.render_series_folder("{series}", FakeSeries(title="Hawkeye: Freefall"))
    assert rendered == "Hawkeye Freefall"


def test_render_series_folder_nests_on_a_slash_like_post_processing_does():
    """A '/' inside a token becomes a folder separator — matching what
    ``postprocess.render_relative_path`` did when it wrote the file."""
    rendered = series_scan.render_series_folder("{series}", FakeSeries(title="My Life/Weapon"))
    assert Path(rendered).parts == ("My Life", "Weapon")


def test_render_series_folder_is_none_when_pattern_renders_empty():
    assert series_scan.render_series_folder("{publisher}", FakeSeries(publisher=None)) is None


# ── series_folders ────────────────────────────────────────────────────────────


def test_series_folders_includes_pattern_folder_and_tracked_parents():
    folders = series_scan.series_folders(
        root="/comics",
        folder_pattern=DEFAULT_PATTERN,
        series=FakeSeries(),
        tracked_paths=["/elsewhere/Batman (2016)/Batman 001.cbz"],
    )
    keys = [series_scan.path_key(f) for f in folders]
    assert keys == ["/comics/dc comics/batman (2016)", "/elsewhere/batman (2016)"]


def test_series_folders_deduplicates_repeated_parents():
    folders = series_scan.series_folders(
        root="/comics",
        folder_pattern="{series}",
        series=FakeSeries(),
        tracked_paths=["/comics/Batman/a.cbz", "/comics/Batman/b.cbz"],
    )
    assert len(folders) == 1


def test_series_folders_never_scans_the_library_root():
    """Matching is by issue number alone — sweeping the shared root would let some
    other series' #1 be attached to this one."""
    folders = series_scan.series_folders(
        root="/comics",
        folder_pattern="{publisher}",
        series=FakeSeries(publisher=None),
        tracked_paths=["/comics/Batman 001.cbz"],
    )
    assert folders == []


# ── inspect ───────────────────────────────────────────────────────────────────


def test_inspect_parses_issue_numbers_and_ignores_non_comics(tmp_path):
    folder = tmp_path / "Batman (2016)"
    folder.mkdir()
    (folder / "Batman 001.cbz").write_bytes(b"PK")
    (folder / "Batman 002.cbr").write_bytes(b"Rar!")
    (folder / "cover.jpg").write_bytes(b"junk")

    scan = series_scan.inspect([str(folder)], [])

    assert sorted(f.issue_number for f in scan.files) == ["1", "2"]


def test_inspect_reports_which_tracked_files_still_exist(tmp_path):
    present = tmp_path / "Batman 001.cbz"
    present.write_bytes(b"PK")
    gone = tmp_path / "Batman 002.cbz"

    scan = series_scan.inspect([], [str(present), str(gone)])

    assert scan.present == {series_scan.path_key(str(present))}


def test_inspect_survives_a_missing_folder(tmp_path):
    scan = series_scan.inspect([str(tmp_path / "nope")], [])
    assert scan.files == []
