"""Tests for Phase 16: library import (scan + import)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app
from pullbox.services.library_import import (
    normalize_issue_number,
    parse_comic_filename,
    scan_library,
)

# ── Filename parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_title,expected_issue,expected_year",
    [
        ("Batman 012 (2016).cbz", "Batman", "012", 2016),
        ("Batman #1.cbr", "Batman", "1", None),
        ("Saga 007.cbz", "Saga", "007", None),
        ("Batman Annual 2.cbz", "Batman Annual", "2", None),
        ("Preview.cbz", "Preview", None, None),
        ("The Wicked + The Divine 1.5 (Digital).cbz", "The Wicked + The Divine", "1.5", None),
    ],
)
def test_parse_comic_filename(name, expected_title, expected_issue, expected_year):
    title, issue, year = parse_comic_filename(name)
    assert title == expected_title
    assert issue == expected_issue
    assert year == expected_year


def test_parse_prefers_year_folder(tmp_path):
    folder = tmp_path / "Sandman (1989)"
    folder.mkdir()
    f = folder / "Sandman 003.cbz"
    title, issue, year = parse_comic_filename(f)
    assert title == "Sandman"
    assert issue == "003"
    assert year == 1989


# ── Issue-number normalization ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("012", "12"),
        ("#1", "1"),
        ("0", "0"),
        ("1.0", "1"),
        ("1.5", "1.5"),
        ("  7 ", "7"),
        ("2a", "2a"),
        (None, ""),
    ],
)
def test_normalize_issue_number(raw, expected):
    assert normalize_issue_number(raw) == expected


# ── Filesystem scan ───────────────────────────────────────────────────────────


def test_scan_library_groups_series(tmp_path):
    batman = tmp_path / "Batman (2016)"
    batman.mkdir()
    (batman / "Batman 001 (2016).cbz").write_bytes(b"x")
    (batman / "Batman 002 (2016).cbz").write_bytes(b"x")
    saga = tmp_path / "Saga"
    saga.mkdir()
    (saga / "Saga 001.cbr").write_bytes(b"x")
    # Non-comic files are ignored.
    (tmp_path / "readme.txt").write_text("ignore me")

    result = scan_library(str(tmp_path))

    titles = {s.title: s for s in result.series}
    assert set(titles) == {"Batman", "Saga"}
    assert titles["Batman"].file_count == 2
    assert titles["Batman"].year == 2016
    assert titles["Saga"].file_count == 1


def test_scan_library_bad_path():
    with pytest.raises(FileNotFoundError):
        scan_library("/definitely/not/a/real/path/xyz")


# ── Scan endpoint ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_scan_endpoint(client, tmp_path):
    folder = tmp_path / "Batman (2016)"
    folder.mkdir()
    (folder / "Batman 001 (2016).cbz").write_bytes(b"x")

    resp = client.post("/api/library-import/scan", json={"path": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["title"] == "Batman"
    assert data["series"][0]["files"][0]["issue_number"] == "001"


def test_scan_endpoint_bad_path(client):
    resp = client.post("/api/library-import/scan", json={"path": "/no/such/dir/abc"})
    assert resp.status_code == 400


# ── Import endpoint (zero ComicVine — creates Series/Issue + tracking rows) ────
# Exercised at the function level with a lock-free session: the TestClient path
# shares a DB with the background scheduler and intermittently trips SQLite locks.


def _import_body(files):
    from pullbox.schemas import ImportSeriesSelection, LibraryImportRequest, ScannedFile

    return LibraryImportRequest(
        series=[
            ImportSeriesSelection(
                title="Batman",
                year=2016,
                files=[ScannedFile(file_path=p, issue_number=n) for p, n in files],
            )
        ]
    )


async def test_import_creates_library_and_tracking(db_session):
    from sqlalchemy import select

    from pullbox.models import ImportFile, Issue, Series
    from pullbox.routers.library_import import import_library

    body = _import_body(
        [("/comics/Batman 001.cbz", "001"), ("/comics/Batman 002.cbz", "2")]
    )

    resp = await import_library(body, db_session)
    assert resp.series_queued == 1
    assert resp.files_queued == 2
    assert resp.errors == []

    # An import-origin Series is created immediately (no ComicVine id, subscribed).
    series = (await db_session.execute(select(Series))).scalars().all()
    assert len(series) == 1
    assert series[0].comicvine_id is None
    assert series[0].subscribed is True

    # One owned Issue per file, downloaded, with its file path, no ComicVine id.
    issues = (await db_session.execute(select(Issue))).scalars().all()
    assert len(issues) == 2
    assert all(i.comicvine_id is None for i in issues)
    assert all(i.status == "downloaded" for i in issues)
    assert {i.file_path for i in issues} == {
        "/comics/Batman 001.cbz",
        "/comics/Batman 002.cbz",
    }

    # One pending tracking row per issue.
    tracking = (await db_session.execute(select(ImportFile))).scalars().all()
    assert len(tracking) == 2
    assert all(t.status == "pending" for t in tracking)
    assert {t.issue_id for t in tracking} == {i.id for i in issues}


async def test_imported_series_serializes_in_list(db_session):
    """Regression: an import-origin series (comicvine_id NULL) must serialize through
    the Series list endpoint — SeriesResponse.comicvine_id has to allow None."""
    from pullbox.routers.library_import import import_library
    from pullbox.routers.series import list_series

    await import_library(_import_body([("/comics/Batman 001.cbz", "001")]), db_session)

    resp = await list_series(db_session, page=1, per_page=20, subscribed=True)
    assert resp.total == 1
    assert resp.items[0].title == "Batman"
    assert resp.items[0].comicvine_id is None


async def test_import_is_idempotent_by_file_path(db_session):
    from sqlalchemy import select

    from pullbox.models import ImportFile, Issue
    from pullbox.routers.library_import import import_library

    files = [("/comics/Batman 001.cbz", "001")]
    await import_library(_import_body(files), db_session)
    # Re-import the same file — no duplicate Issue/tracking rows.
    resp = await import_library(_import_body(files), db_session)
    assert resp.files_queued == 0

    issues = (await db_session.execute(select(Issue))).scalars().all()
    tracking = (await db_session.execute(select(ImportFile))).scalars().all()
    assert len(issues) == 1
    assert len(tracking) == 1


@pytest.fixture
async def db_session():
    """Lock-free session with tables created directly (no TestClient/scheduler)."""
    import pullbox.database as db_module
    from pullbox.config import Settings
    from pullbox.models import Base

    settings = Settings()  # reads PULLBOX_DATABASE_URL from conftest
    db_module.init_db(settings)
    async with db_module._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_module.AsyncSessionLocal() as session:
        yield session
    await db_module._engine.dispose()
    db_module._engine = None
    db_module.AsyncSessionLocal = None
