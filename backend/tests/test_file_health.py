"""File-health inspection + API tests.

The archive fixtures are built byte-by-byte rather than mocked: the whole point
of this feature is that the bytes on disk disagree with the file name, so the
detector has to be exercised against real files.
"""

from __future__ import annotations

import asyncio
import zipfile

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app
from pullbox.services import file_health as fh

# ── Fixtures: files with known contents ───────────────────────────────────────

RAR4_MAGIC = b"Rar!\x1a\x07\x00"
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


def make_cbz(path, names=("001.jpg", "002.jpg")):
    """A structurally valid ZIP containing image entries."""
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"\xff\xd8\xff\xe0 fake jpeg bytes")
    return path


def make_rar(path):
    """A RAR header + filler. Enough for magic detection; not a real archive."""
    path.write_bytes(RAR4_MAGIC + b"\x00" * 512)
    return path


# ── detect_format ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"PK\x03\x04rest", "zip"),
        (b"PK\x05\x06rest", "zip"),
        (RAR4_MAGIC + b"rest", "rar"),
        (b"Rar!\x1a\x07\x01\x00rest", "rar"),
        (SEVENZ_MAGIC + b"rest", "7z"),
        (b"%PDF-1.7 rest", "pdf"),
        (b"<!DOCTYPE html><html>", None),
        (b"", None),
    ],
)
def test_detect_format(header, expected):
    assert fh.detect_format(header) == expected


def test_detect_format_tar_magic_at_offset_257():
    header = b"\x00" * 257 + b"ustar\x0000"
    assert fh.detect_format(header) == "tar"


# ── inspect_file ──────────────────────────────────────────────────────────────


def test_healthy_cbz_reports_nothing(tmp_path):
    assert fh.inspect_file(make_cbz(tmp_path / "good.cbz")) is None


def test_healthy_cbz_passes_deep_scan(tmp_path):
    assert fh.inspect_file(make_cbz(tmp_path / "good.cbz"), deep=True) is None


def test_rar_named_cbz_is_wrong_format(tmp_path):
    """The 'File is not a zip file' case — the reason this feature exists."""
    problem = fh.inspect_file(make_rar(tmp_path / "mislabeled.cbz"))
    assert problem is not None
    assert problem.kind == fh.WRONG_FORMAT
    assert problem.severity == fh.ERROR
    assert "RAR" in problem.detail
    assert "not a zip file" in problem.detail  # names the error the user will see
    assert ".cbr" in problem.detail  # and the fix


def test_zip_named_cbr_is_wrong_format(tmp_path):
    problem = fh.inspect_file(make_cbz(tmp_path / "mislabeled.cbr"))
    assert problem is not None
    assert problem.kind == fh.WRONG_FORMAT
    assert ".cbz" in problem.detail


def test_missing_file(tmp_path):
    problem = fh.inspect_file(tmp_path / "gone.cbz")
    assert problem is not None
    assert problem.kind == fh.MISSING


def test_directory_is_reported_missing(tmp_path):
    (tmp_path / "afolder.cbz").mkdir()
    problem = fh.inspect_file(tmp_path / "afolder.cbz")
    assert problem is not None
    assert problem.kind == fh.MISSING


def test_empty_file(tmp_path):
    path = tmp_path / "empty.cbz"
    path.write_bytes(b"")
    problem = fh.inspect_file(path)
    assert problem is not None
    assert problem.kind == fh.EMPTY
    assert problem.size_bytes == 0


def test_unknown_format(tmp_path):
    path = tmp_path / "notacomic.cbz"
    path.write_bytes(b"<!DOCTYPE html><html>404 Not Found</html>")
    problem = fh.inspect_file(path)
    assert problem is not None
    assert problem.kind == fh.UNKNOWN_FORMAT


def test_truncated_zip_is_corrupt(tmp_path):
    """A ZIP header with the central directory chopped off — a partial download."""
    source = make_cbz(tmp_path / "full.cbz")
    data = source.read_bytes()
    path = tmp_path / "truncated.cbz"
    path.write_bytes(data[: len(data) // 2])
    problem = fh.inspect_file(path)
    assert problem is not None
    assert problem.kind == fh.CORRUPT


def test_zip_without_images_is_a_warning(tmp_path):
    path = make_cbz(tmp_path / "nopages.cbz", names=("readme.txt", "info.nfo"))
    problem = fh.inspect_file(path)
    assert problem is not None
    assert problem.kind == fh.NO_IMAGES
    assert problem.severity == fh.WARNING


def test_deep_scan_catches_corrupted_entry_bytes(tmp_path):
    """Flip bytes inside a stored entry: the directory still reads, the CRC won't."""
    path = tmp_path / "bitrot.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("001.jpg", b"\xff\xd8\xff\xe0" + b"A" * 200)

    data = bytearray(path.read_bytes())
    offset = data.find(b"A" * 200)
    assert offset != -1
    data[offset : offset + 50] = b"B" * 50
    path.write_bytes(bytes(data))

    # Shallow pass only reads the directory, which is still intact.
    assert fh.inspect_file(path) is None
    problem = fh.inspect_file(path, deep=True)
    assert problem is not None
    assert problem.kind == fh.CORRUPT


def test_rar_named_cbr_passes(tmp_path):
    """We can't open RAR without a third-party lib, so a matching header is a pass."""
    assert fh.inspect_file(make_rar(tmp_path / "fine.cbr")) is None


def test_pdf_passes(tmp_path):
    path = tmp_path / "issue.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 100)
    assert fh.inspect_file(path) is None


# ── iter_comic_files / scan_paths ─────────────────────────────────────────────


def test_iter_comic_files_finds_nested_comics_only(tmp_path):
    make_cbz(tmp_path / "a.cbz")
    nested = tmp_path / "Batman (2016)"
    nested.mkdir()
    make_cbz(nested / "b.cbz")
    (tmp_path / "cover.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")

    found = fh.iter_comic_files(tmp_path)
    assert [p.name for p in found] == ["a.cbz", "b.cbz"]


def test_iter_comic_files_missing_root_returns_empty(tmp_path):
    assert fh.iter_comic_files(tmp_path / "nope") == []


def test_scan_paths_keeps_only_problems(tmp_path):
    good = make_cbz(tmp_path / "good.cbz")
    bad = make_rar(tmp_path / "bad.cbz")
    results = fh.scan_paths([str(good), str(bad)])
    assert [r.file_path for r in results] == [str(bad)]
    assert results[0].problem.kind == fh.WRONG_FORMAT


# ── API ───────────────────────────────────────────────────────────────────────


def _set_library(client, path):
    resp = client.patch("/api/settings/general", json={"library_path": str(path)})
    assert resp.status_code == 200


def _seed_issue(file_path: str) -> int:
    """Create one Series + one Issue pointing at ``file_path``. Returns the issue id.

    Sync, driving async SQLAlchemy through ``asyncio.run`` on a fresh loop — the
    same pattern the other router tests use, so seeding and the TestClient share
    one SQLite file. ``AsyncSessionLocal`` is imported inside the function
    because ``init_db`` only assigns it once the app's lifespan has run.
    """

    async def _run():
        from pullbox.database import AsyncSessionLocal
        from pullbox.models import Issue, Series

        async with AsyncSessionLocal() as db:
            series = Series(title="Batman", publisher="DC", start_year=2016)
            db.add(series)
            await db.flush()
            issue = Issue(
                series_id=series.id,
                issue_number="1",
                status="downloaded",
                file_path=file_path,
            )
            db.add(issue)
            await db.commit()
            return issue.id

    return asyncio.run(_run())


def test_list_is_empty_before_any_scan():
    with TestClient(app) as client:
        resp = client.get("/api/file-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["issues"] == []
        assert body["summary"]["total"] == 0
        assert body["last_scan_at"] is None


def test_scan_finds_mislabeled_file_in_library(tmp_path):
    make_cbz(tmp_path / "good.cbz")
    make_rar(tmp_path / "mislabeled.cbz")

    with TestClient(app) as client:
        _set_library(client, tmp_path)
        resp = client.post("/api/file-health/scan", json={})
        assert resp.status_code == 200
        body = resp.json()

        assert body["files_scanned"] == 2
        assert body["summary"]["total"] == 1
        assert body["summary"]["errors"] == 1
        assert body["summary"]["by_kind"] == {"wrong_format": 1}
        assert body["issues"][0]["file_path"].endswith("mislabeled.cbz")
        assert body["last_scan_at"] is not None

        # Results persist for the plain GET.
        again = client.get("/api/file-health").json()
        assert again["summary"]["total"] == 1


def test_scan_replaces_previous_results(tmp_path):
    bad = make_rar(tmp_path / "bad.cbz")

    with TestClient(app) as client:
        _set_library(client, tmp_path)
        assert client.post("/api/file-health/scan", json={}).json()["summary"]["total"] == 1

        # Fix it the way a user would — rename to the correct extension.
        bad.rename(tmp_path / "bad.cbr")
        body = client.post("/api/file-health/scan", json={}).json()
        assert body["summary"]["total"] == 0
        assert body["issues"] == []


def test_scan_links_findings_to_the_tracked_issue(tmp_path):
    """A bad file inside the library that PullBox also tracks keeps its issue link,
    so the UI can name the series instead of only showing a path."""
    bad = make_rar(tmp_path / "mislabeled.cbz")

    with TestClient(app) as client:
        _set_library(client, tmp_path)
        issue_id = _seed_issue(str(bad))

        body = client.post("/api/file-health/scan", json={}).json()
        assert body["summary"]["total"] == 1
        found = body["issues"][0]
        assert found["issue_id"] == issue_id
        assert found["series_title"] == "Batman"
        assert found["issue_number"] == "1"


def test_untracked_file_is_still_reported(tmp_path):
    """A file that arrived outside PullBox has no issue link but still shows up."""
    make_rar(tmp_path / "stray.cbz")
    with TestClient(app) as client:
        _set_library(client, tmp_path)
        found = client.post("/api/file-health/scan", json={}).json()["issues"][0]
        assert found["issue_id"] is None
        assert found["series_title"] is None


def test_scan_reports_missing_tracked_file(tmp_path):
    """A DB row pointing at a path that no longer exists — the file was moved or
    deleted outside PullBox. Found via the tracked-path set, not the library walk."""
    with TestClient(app) as client:
        _set_library(client, tmp_path)
        issue_id = _seed_issue(str(tmp_path / "vanished.cbz"))

        body = client.post("/api/file-health/scan", json={}).json()
        assert body["summary"]["by_kind"] == {"missing": 1}
        found = body["issues"][0]
        assert found["issue_id"] == issue_id
        assert found["series_title"] == "Batman"


def test_tracked_and_walked_spellings_are_scanned_once(tmp_path):
    """The same file reached by two path spellings must not produce two findings."""
    bad = make_rar(tmp_path / "mislabeled.cbz")
    with TestClient(app) as client:
        _set_library(client, tmp_path)
        _seed_issue(str(bad).replace("\\", "/"))  # forward slashes, same file

        body = client.post("/api/file-health/scan", json={}).json()
        assert body["files_scanned"] == 1
        assert body["summary"]["total"] == 1


def test_recheck_clears_a_fixed_file(tmp_path):
    bad = make_rar(tmp_path / "bad.cbz")

    with TestClient(app) as client:
        _set_library(client, tmp_path)
        body = client.post("/api/file-health/scan", json={}).json()
        finding_id = body["issues"][0]["id"]

        # Still broken → row stays, refreshed.
        resp = client.post(f"/api/file-health/{finding_id}/recheck")
        assert resp.status_code == 200
        assert resp.json()["resolved"] is False
        assert resp.json()["issue"]["kind"] == "wrong_format"

        # Replace with a real CBZ at the same path → row clears.
        bad.unlink()
        make_cbz(tmp_path / "bad.cbz")
        resp = client.post(f"/api/file-health/{finding_id}/recheck")
        assert resp.json()["resolved"] is True
        assert client.get("/api/file-health").json()["summary"]["total"] == 0


def test_recheck_unknown_id_404s():
    with TestClient(app) as client:
        assert client.post("/api/file-health/9999/recheck").status_code == 404


def test_dismiss_removes_the_row(tmp_path):
    make_rar(tmp_path / "bad.cbz")
    with TestClient(app) as client:
        _set_library(client, tmp_path)
        finding_id = client.post("/api/file-health/scan", json={}).json()["issues"][0]["id"]

        assert client.delete(f"/api/file-health/{finding_id}").status_code == 204
        assert client.get("/api/file-health").json()["summary"]["total"] == 0
        assert client.delete(f"/api/file-health/{finding_id}").status_code == 404


def test_scan_with_explicit_path_overrides_library(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    make_rar(elsewhere / "bad.cbz")

    with TestClient(app) as client:
        _set_library(client, tmp_path / "empty-library")
        body = client.post("/api/file-health/scan", json={"path": str(elsewhere)}).json()
        assert body["summary"]["total"] == 1
        assert body["scanned_root"] == str(elsewhere)


def test_scan_of_unreadable_root_is_reported(tmp_path):
    with TestClient(app) as client:
        _set_library(client, tmp_path / "does-not-exist")
        body = client.post("/api/file-health/scan", json={}).json()
        assert body["files_scanned"] == 0
        assert "not readable" in body["last_scan_message"]
