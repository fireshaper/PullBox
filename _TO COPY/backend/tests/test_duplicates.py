"""Tests for duplicate-series detection and merging.

The merge deletes rows and repoints issues, so the cases that matter most here
are the ones where something could be silently lost: a subscription, a
downloaded file, an arc link, a download job.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from pullbox.main import app
from pullbox.services.dedupe import normalize_title

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _add_series(
    title: str,
    *,
    metron_id: str | None = None,
    comicvine_id: str | None = None,
    publisher: str | None = None,
    start_year: int | None = None,
    subscribed: bool = False,
    auto_download: bool = False,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        s = Series(
            title=title,
            metron_id=metron_id,
            comicvine_id=comicvine_id,
            publisher=publisher,
            start_year=start_year,
            subscribed=subscribed,
            auto_download=auto_download,
        )
        db.add(s)
        await db.commit()
        return s.id


async def _add_issue(
    series_id: int,
    *,
    issue_number: str = "1",
    status: str = "unknown",
    metron_id: str | None = None,
    comicvine_id: str | None = None,
    file_path: str | None = None,
    store_date: date | None = None,
) -> int:
    import pullbox.database as db_module
    from pullbox.models import Issue

    async with db_module.AsyncSessionLocal() as db:
        i = Issue(
            series_id=series_id,
            issue_number=issue_number,
            status=status,
            metron_id=metron_id,
            comicvine_id=comicvine_id,
            file_path=file_path,
            store_date=store_date,
        )
        db.add(i)
        await db.commit()
        return i.id


async def _get_series(series_id: int):
    import pullbox.database as db_module
    from pullbox.models import Series

    async with db_module.AsyncSessionLocal() as db:
        return await db.get(Series, series_id)


async def _count(model) -> int:
    from sqlalchemy import func, select

    import pullbox.database as db_module

    async with db_module.AsyncSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── Normalization ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b",
    [
        ("Batman / Superman: World's Finest", "Batman/Superman: World's Finest"),
        ("The Fury of Firestorm", "the fury of firestorm"),
        ("Hawk-Girl Summer", "Hawk Girl Summer"),
        ("Spider-Man (2022)", "Spider Man 2022"),
    ],
)
def test_titles_that_should_collapse_to_the_same_key(a, b):
    assert normalize_title(a) == normalize_title(b)


def test_distinct_titles_stay_distinct():
    assert normalize_title("Action Comics") != normalize_title("Action Comics Annual")


# ── Detection ─────────────────────────────────────────────────────────────────


def test_no_duplicates_reports_nothing(client):
    asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    asyncio.run(_add_series("Superman", comicvine_id="cv-2"))

    data = client.get("/api/duplicates").json()
    assert data["total_groups"] == 0
    assert data["groups"] == []


def test_cross_source_pair_is_detected(client):
    asyncio.run(_add_series("Batman/Superman: World's Finest", comicvine_id="cv-1"))
    asyncio.run(_add_series("Batman / Superman: World's Finest", metron_id="m-1"))

    data = client.get("/api/duplicates").json()
    assert data["total_groups"] == 1
    assert data["mergeable_groups"] == 1
    assert len(data["groups"][0]["rows"]) == 2


def test_conflicting_start_years_are_reported_but_not_mergeable(client):
    asyncio.run(_add_series("Action Comics", comicvine_id="cv-1", start_year=1938))
    asyncio.run(_add_series("Action Comics", metron_id="m-1", start_year=2011))

    data = client.get("/api/duplicates").json()
    assert data["total_groups"] == 1
    assert data["conflicting_groups"] == 1
    assert data["mergeable_groups"] == 0
    assert data["groups"][0]["mergeable"] is False


def test_rows_are_ordered_by_issue_count(client):
    small = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    big = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_issue(small, issue_number="1"))
    for n in ("1", "2", "3"):
        asyncio.run(_add_issue(big, issue_number=n))

    rows = client.get("/api/duplicates").json()["groups"][0]["rows"]
    assert [r["id"] for r in rows] == [big, small]
    assert rows[0]["issue_count"] == 3


# ── Merging ───────────────────────────────────────────────────────────────────


def test_merge_unions_both_id_spaces(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))

    resp = client.post("/api/duplicates/merge", json={"series_ids": [a, b]})
    assert resp.status_code == 200

    kept = asyncio.run(_get_series(resp.json()["kept_series_id"]))
    assert kept.comicvine_id == "cv-1"
    assert kept.metron_id == "m-1"
    assert asyncio.run(_count(type(kept))) == 1


def test_merge_never_drops_a_subscription(client):
    """The unsubscribed row has more issues so it wins the merge — the losing
    row's subscription must still survive, or the user silently stops following
    a series they chose to follow."""
    winner = asyncio.run(_add_series("Batman", comicvine_id="cv-1", subscribed=False))
    loser = asyncio.run(_add_series("Batman", metron_id="m-1", subscribed=True, auto_download=True))
    for n in ("1", "2"):
        asyncio.run(_add_issue(winner, issue_number=n))

    resp = client.post("/api/duplicates/merge", json={"series_ids": [winner, loser]})
    assert resp.json()["kept_series_id"] == winner

    kept = asyncio.run(_get_series(winner))
    assert kept.subscribed is True
    assert kept.auto_download is True


def test_merge_moves_non_overlapping_issues(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_issue(a, issue_number="1"))
    asyncio.run(_add_issue(b, issue_number="2"))

    data = client.post("/api/duplicates/merge", json={"series_ids": [a, b]}).json()
    assert data["issues_moved"] == 1
    assert data["issues_merged"] == 0

    from pullbox.models import Issue

    assert asyncio.run(_count(Issue)) == 2


def test_colliding_issue_numbers_merge_rather_than_duplicate(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    # Same book, spelled differently by each source.
    asyncio.run(_add_issue(a, issue_number="1", comicvine_id="cvi-1"))
    asyncio.run(_add_issue(b, issue_number="01", metron_id="mi-1"))

    data = client.post("/api/duplicates/merge", json={"series_ids": [a, b]}).json()
    assert data["issues_merged"] == 1

    from pullbox.models import Issue

    assert asyncio.run(_count(Issue)) == 1


def test_merged_issue_keeps_the_downloaded_copy(client):
    """The row with the file must win on status and path regardless of which
    series row wins the merge — losing a file path orphans a real download."""
    winner = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    loser = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_issue(winner, issue_number="1", status="wanted"))
    asyncio.run(
        _add_issue(loser, issue_number="1", status="downloaded", file_path="/comics/b1.cbz")
    )
    # Give the winner more issues so it is definitely the surviving row.
    asyncio.run(_add_issue(winner, issue_number="2"))

    client.post("/api/duplicates/merge", json={"series_ids": [winner, loser]})

    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.models import Issue

    async def _load():
        async with db_module.AsyncSessionLocal() as db:
            return (
                (await db.execute(select(Issue).where(Issue.issue_number == "1"))).scalars().all()
            )

    issues = asyncio.run(_load())
    assert len(issues) == 1
    assert issues[0].status == "downloaded"
    assert issues[0].file_path == "/comics/b1.cbz"


def test_merged_issue_absorbs_ids_from_both_sources(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_issue(a, issue_number="1", comicvine_id="cvi-1"))
    asyncio.run(_add_issue(b, issue_number="1", metron_id="mi-1"))

    client.post("/api/duplicates/merge", json={"series_ids": [a, b]})

    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.models import Issue

    async def _load():
        async with db_module.AsyncSessionLocal() as db:
            return (await db.execute(select(Issue))).scalars().one()

    issue = asyncio.run(_load())
    assert issue.comicvine_id == "cvi-1"
    assert issue.metron_id == "mi-1"


def test_download_jobs_follow_the_surviving_issue(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_issue(a, issue_number="1"))
    doomed = asyncio.run(_add_issue(b, issue_number="1"))

    async def _add_job():
        import pullbox.database as db_module
        from pullbox.models import DownloadJob

        async with db_module.AsyncSessionLocal() as db:
            db.add(DownloadJob(issue_id=doomed, source_type="usenet", status="failed"))
            await db.commit()

    asyncio.run(_add_job())
    client.post("/api/duplicates/merge", json={"series_ids": [a, b]})

    from sqlalchemy import select

    import pullbox.database as db_module
    from pullbox.models import DownloadJob, Issue

    async def _load():
        async with db_module.AsyncSessionLocal() as db:
            job = (await db.execute(select(DownloadJob))).scalars().one()
            issue = (await db.execute(select(Issue))).scalars().one()
            return job.issue_id, issue.id

    job_issue_id, surviving_issue_id = asyncio.run(_load())
    assert job_issue_id == surviving_issue_id


def test_weekly_releases_do_not_collide_on_merge(client):
    """WeeklyRelease is unique on (issue_id, release_date); repointing the loser's
    row onto an issue that already has that date would violate it."""
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    ia = asyncio.run(_add_issue(a, issue_number="1"))
    ib = asyncio.run(_add_issue(b, issue_number="1"))

    async def _add_releases():
        import pullbox.database as db_module
        from pullbox.models import WeeklyRelease

        async with db_module.AsyncSessionLocal() as db:
            db.add(WeeklyRelease(issue_id=ia, release_date=date(2026, 8, 5), source="comicvine"))
            db.add(WeeklyRelease(issue_id=ib, release_date=date(2026, 8, 5), source="metron"))
            db.add(WeeklyRelease(issue_id=ib, release_date=date(2026, 8, 12), source="metron"))
            await db.commit()

    asyncio.run(_add_releases())
    resp = client.post("/api/duplicates/merge", json={"series_ids": [a, b]})
    assert resp.status_code == 200

    from pullbox.models import WeeklyRelease

    # The duplicated date collapses to one row; the unique second date survives.
    assert asyncio.run(_count(WeeklyRelease)) == 2


def test_arc_links_survive_and_do_not_duplicate(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    ia = asyncio.run(_add_issue(a, issue_number="1"))
    ib = asyncio.run(_add_issue(b, issue_number="1"))

    async def _link():
        import pullbox.database as db_module
        from pullbox.models import Issue, StoryArc

        async with db_module.AsyncSessionLocal() as db:
            shared = StoryArc(comicvine_id="arc-shared", name="Shared")
            only_loser = StoryArc(comicvine_id="arc-loser", name="Loser Only")
            shared.issues = [await db.get(Issue, ia), await db.get(Issue, ib)]
            only_loser.issues = [await db.get(Issue, ib)]
            db.add_all([shared, only_loser])
            await db.commit()

    asyncio.run(_link())
    resp = client.post("/api/duplicates/merge", json={"series_ids": [a, b]})
    assert resp.status_code == 200

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    import pullbox.database as db_module
    from pullbox.models import Issue

    async def _load():
        async with db_module.AsyncSessionLocal() as db:
            issue = (
                (await db.execute(select(Issue).options(selectinload(Issue.arcs)))).scalars().one()
            )
            return sorted(a.name for a in issue.arcs)

    assert asyncio.run(_load()) == ["Loser Only", "Shared"]


def test_merge_rejects_a_single_series(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    resp = client.post("/api/duplicates/merge", json={"series_ids": [a]})
    assert resp.status_code == 400


# ── Merge all ─────────────────────────────────────────────────────────────────


def test_merge_all_skips_conflicting_year_groups(client):
    a = asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    b = asyncio.run(_add_series("Batman", metron_id="m-1"))
    asyncio.run(_add_series("Action Comics", comicvine_id="cv-2", start_year=1938))
    asyncio.run(_add_series("Action Comics", metron_id="m-2", start_year=2011))

    data = client.post("/api/duplicates/merge-all").json()
    assert data["merged_groups"] == 1
    assert data["skipped_groups"] == 1

    remaining = client.get("/api/duplicates").json()
    assert remaining["total_groups"] == 1
    assert remaining["conflicting_groups"] == 1
    assert a is not None and b is not None


# ── Prevention: the write path that created these in the first place ──────────


def test_metron_record_matches_an_existing_comicvine_row(client):
    """The actual bug. A Metron weekly record carries no cv_id, so id matching
    alone cannot see the ComicVine row for the same book and a second one gets
    created. The normalized title has to bridge them."""
    import pullbox.database as db_module
    from pullbox.services.dedupe import find_series_for_release

    existing = asyncio.run(_add_series("Batman/Superman: World's Finest", comicvine_id="cv-1"))

    async def _resolve():
        async with db_module.AsyncSessionLocal() as db:
            found = await find_series_for_release(
                db,
                {"metron_id": "m-99", "comicvine_id": None},
                "Batman / Superman: World's Finest",
            )
            if found is None:
                return None, None
            await db.commit()
            return found.id, found.metron_id

    found_id, adopted = asyncio.run(_resolve())
    assert found_id == existing
    # The id is written onto the row, so later refreshes match on id directly.
    assert adopted == "m-99"


def test_ambiguous_title_is_not_guessed(client):
    """Two volumes share a title — adopting one would file issues under the
    wrong book, so a new row is correct here even though it looks like a dupe."""
    import pullbox.database as db_module
    from pullbox.services.dedupe import find_series_for_release

    asyncio.run(_add_series("Action Comics", comicvine_id="cv-1", start_year=1938))
    asyncio.run(_add_series("Action Comics", comicvine_id="cv-2", start_year=2011))

    async def _resolve():
        async with db_module.AsyncSessionLocal() as db:
            return await find_series_for_release(
                db, {"metron_id": "m-1", "comicvine_id": None}, "Action Comics"
            )

    assert asyncio.run(_resolve()) is None


def test_id_match_wins_over_title_match(client):
    import pullbox.database as db_module
    from pullbox.services.dedupe import find_series_for_release

    asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    by_id = asyncio.run(_add_series("Batman Beyond", metron_id="m-7"))

    async def _resolve():
        async with db_module.AsyncSessionLocal() as db:
            found = await find_series_for_release(
                db, {"metron_id": "m-7", "comicvine_id": None}, "Batman"
            )
            return found.id

    assert asyncio.run(_resolve()) == by_id


def test_existing_id_is_never_overwritten(client):
    """Adopting an id onto a row that already has a different one in that space
    would silently repoint the series at another volume."""
    import pullbox.database as db_module
    from pullbox.services.dedupe import find_series_for_release

    asyncio.run(_add_series("Batman", comicvine_id="cv-1", metron_id="m-original"))

    async def _resolve():
        async with db_module.AsyncSessionLocal() as db:
            found = await find_series_for_release(
                db, {"metron_id": "m-other", "comicvine_id": None}, "Batman"
            )
            return found.metron_id

    assert asyncio.run(_resolve()) == "m-original"


def test_norm_title_is_maintained_automatically(client):
    """The column is derived by a flush listener, not set at each call site."""
    import pullbox.database as db_module
    from pullbox.models import Series

    sid = asyncio.run(_add_series("Batman / Superman", comicvine_id="cv-1"))

    async def _read_then_rename():
        async with db_module.AsyncSessionLocal() as db:
            s = await db.get(Series, sid)
            before = s.norm_title
            s.title = "Wonder Woman!"
            await db.commit()
            return before, s.norm_title

    before, after = asyncio.run(_read_then_rename())
    assert before == "batmansuperman"
    assert after == "wonderwoman"


def test_merge_all_is_idempotent(client):
    asyncio.run(_add_series("Batman", comicvine_id="cv-1"))
    asyncio.run(_add_series("Batman", metron_id="m-1"))

    first = client.post("/api/duplicates/merge-all").json()
    second = client.post("/api/duplicates/merge-all").json()

    assert first["merged_groups"] == 1
    assert second["merged_groups"] == 0
    assert client.get("/api/duplicates").json()["total_groups"] == 0
