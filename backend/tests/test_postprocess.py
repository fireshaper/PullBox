"""Tests for post-download processing: pattern rendering, file ops, and the API."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app
from pullbox.services.postprocess import (
    apply_post_processing,
    build_tokens,
    find_comic_file,
    render_pattern,
    render_relative_path,
    sanitize_component,
)

# ── Sample data ───────────────────────────────────────────────────────────────

ISSUE = SimpleNamespace(id=1, issue_number="12", title="The Return")
SERIES = SimpleNamespace(title="Batman", publisher="DC Comics", start_year=2016)


def _cfg(**overrides):
    base = dict(
        enabled=True,
        operation="move",
        destination_root=None,
        folder_pattern="{publisher}/{series} ({year})",
        file_pattern="{series} #{issue} - {title}",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── render_pattern / build_tokens ─────────────────────────────────────────────


def test_build_tokens_maps_fields():
    tokens = build_tokens(ISSUE, SERIES, ext=".cbz")
    assert tokens["series"] == "Batman"
    assert tokens["publisher"] == "DC Comics"
    assert tokens["year"] == "2016"
    assert tokens["issue"] == "12"
    assert tokens["title"] == "The Return"
    assert tokens["ext"] == "cbz"


def test_render_pattern_substitutes_tokens():
    tokens = build_tokens(ISSUE, SERIES)
    assert render_pattern("{series} #{issue}", tokens) == "Batman #12"


def test_render_pattern_pads_numeric_issue():
    tokens = build_tokens(ISSUE, SERIES)
    assert render_pattern("{issue:3}", tokens) == "012"


def test_render_pattern_leaves_non_numeric_issue_unpadded():
    tokens = build_tokens(
        SimpleNamespace(issue_number="Annual 1", title=None), SERIES
    )
    assert render_pattern("{issue:3}", tokens) == "Annual 1"


def test_render_pattern_none_values_render_empty():
    tokens = build_tokens(SimpleNamespace(issue_number="5", title=None), SimpleNamespace())
    assert render_pattern("{title}", tokens) == ""
    assert render_pattern("{publisher}", tokens) == ""


# ── sanitize_component ────────────────────────────────────────────────────────


def test_sanitize_strips_illegal_chars():
    assert sanitize_component('Bat:man?/*"<>|') == "Batman"


def test_sanitize_collapses_whitespace_and_trims_dots():
    assert sanitize_component("  Batman   Returns.  ") == "Batman Returns"


def test_render_relative_path_nests_folders_and_sanitizes():
    tokens = build_tokens(ISSUE, SERIES)
    rel = render_relative_path("{publisher}/{series} ({year})", "{series} #{issue}", tokens)
    assert rel == Path("DC Comics") / "Batman (2016)" / "Batman #12"


# ── find_comic_file ───────────────────────────────────────────────────────────


def test_find_comic_file_direct_file(tmp_path):
    f = tmp_path / "Batman 12.cbz"
    f.write_bytes(b"x")
    assert find_comic_file(f) == f


def test_find_comic_file_non_comic_file_returns_none(tmp_path):
    f = tmp_path / "readme.txt"
    f.write_bytes(b"x")
    assert find_comic_file(f) is None


def test_find_comic_file_picks_largest_in_dir(tmp_path):
    small = tmp_path / "cover.cbz"
    small.write_bytes(b"x")
    big = tmp_path / "sub" / "Batman 12.cbr"
    big.parent.mkdir()
    big.write_bytes(b"x" * 100)
    assert find_comic_file(tmp_path) == big


def test_find_comic_file_none_when_empty(tmp_path):
    assert find_comic_file(tmp_path) is None


# ── apply_post_processing ─────────────────────────────────────────────────────


def test_apply_move_relocates_and_renames(tmp_path):
    src_dir = tmp_path / "downloads" / "batman.raw"
    src_dir.mkdir(parents=True)
    src = src_dir / "whatever.cbz"
    src.write_bytes(b"data")
    library = tmp_path / "comics"

    result = apply_post_processing(ISSUE, SERIES, str(src_dir), _cfg(), str(library))

    expected = library / "DC Comics" / "Batman (2016)" / "Batman #12 - The Return.cbz"
    assert Path(result) == expected
    assert expected.exists()
    assert not src.exists()  # moved


def test_apply_copy_keeps_source(tmp_path):
    src = tmp_path / "Batman 12.cbz"
    src.write_bytes(b"data")
    library = tmp_path / "comics"

    result = apply_post_processing(ISSUE, SERIES, str(src), _cfg(operation="copy"), str(library))

    assert Path(result).exists()
    assert src.exists()  # copy leaves original


def test_apply_hardlink_creates_link(tmp_path):
    src = tmp_path / "Batman 12.cbz"
    src.write_bytes(b"data")
    library = tmp_path / "comics"

    result = apply_post_processing(
        ISSUE, SERIES, str(src), _cfg(operation="hardlink"), str(library)
    )

    target = Path(result)
    assert target.exists()
    assert src.exists()
    # Same inode / content — hardlink or copy fallback both leave both readable.
    assert target.read_bytes() == b"data"


def test_apply_destination_root_override(tmp_path):
    src = tmp_path / "Batman 12.cbz"
    src.write_bytes(b"data")
    dest = tmp_path / "elsewhere"

    result = apply_post_processing(
        ISSUE, SERIES, str(src), _cfg(destination_root=str(dest)), str(tmp_path / "unused")
    )

    assert Path(result).is_relative_to(dest)


def test_apply_dedupes_on_collision(tmp_path):
    library = tmp_path / "comics"
    cfg = _cfg(operation="copy")

    src1 = tmp_path / "a.cbz"
    src1.write_bytes(b"1")
    first = apply_post_processing(ISSUE, SERIES, str(src1), cfg, str(library))

    src2 = tmp_path / "b.cbz"
    src2.write_bytes(b"2")
    second = apply_post_processing(ISSUE, SERIES, str(src2), cfg, str(library))

    assert first != second
    assert "(1)" in Path(second).name


def test_apply_returns_none_when_no_comic_file(tmp_path):
    src_dir = tmp_path / "downloads"
    src_dir.mkdir()
    (src_dir / "readme.txt").write_bytes(b"x")

    result = apply_post_processing(ISSUE, SERIES, str(src_dir), _cfg(), str(tmp_path / "comics"))
    assert result is None


# ── API endpoints ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_get_post_processing_creates_default(client):
    resp = client.get("/api/settings/post-processing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["operation"] == "move"
    assert data["folder_pattern"] == "{publisher}/{series} ({year})"


def test_patch_post_processing_partial_update(client):
    client.get("/api/settings/post-processing")  # ensure row exists
    resp = client.patch(
        "/api/settings/post-processing",
        json={"enabled": True, "operation": "hardlink"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["operation"] == "hardlink"
    # Untouched fields keep their defaults.
    assert data["file_pattern"] == "{series} #{issue} - {title}"


def test_preview_renders_sample_path(client):
    resp = client.post(
        "/api/settings/post-processing/preview",
        json={
            "folder_pattern": "{publisher}/{series} ({year})",
            "file_pattern": "{series} #{issue:3} - {title}",
            "destination_root": "/comics",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "/comics/DC Comics/Batman (2016)/Batman #012 - The Return.cbz"
