from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Many-to-many link between locally-tracked issues and the story arcs they belong to.
issue_story_arcs = Table(
    "issue_story_arcs",
    Base.metadata,
    Column("issue_id", Integer, ForeignKey("issues.id"), primary_key=True),
    Column("story_arc_id", Integer, ForeignKey("story_arcs.id"), primary_key=True),
)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Primary metadata identity (Metron series id). NULL for import-origin series not
    # yet matched to a Metron series.
    metron_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    # ComicVine cross-reference (Metron exposes it as cv_id; also the id when ComicVine
    # is the source). NULL when unknown.
    comicvine_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ongoing")
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    issues: Mapped[list["Issue"]] = relationship("Issue", back_populates="series")


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(Integer, ForeignKey("series.id"), nullable=False)
    # Primary metadata identity (Metron issue id). NULL for import-origin issues not
    # yet synced with a metadata source.
    metron_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    # ComicVine cross-reference / id. NULL when unknown.
    comicvine_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    issue_number: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    store_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="unknown")
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # When we last fetched this issue's story_arc_credits from ComicVine.
    # NULL means "not yet enriched" — sync will fetch arc membership for it.
    arcs_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    series: Mapped["Series"] = relationship("Series", back_populates="issues")
    download_jobs: Mapped[list["DownloadJob"]] = relationship(
        "DownloadJob", back_populates="issue"
    )
    weekly_releases: Mapped[list["WeeklyRelease"]] = relationship(
        "WeeklyRelease", back_populates="issue"
    )
    arcs: Mapped[list["StoryArc"]] = relationship(
        "StoryArc", secondary=issue_story_arcs, back_populates="issues"
    )


class Indexer(Base):
    __tablename__ = "indexers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    usenet_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    download_jobs: Mapped[list["DownloadJob"]] = relationship(
        "DownloadJob", back_populates="indexer"
    )


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("issues.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    indexer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("indexers.id"), nullable=True
    )
    result_guid: Mapped[str | None] = mapped_column(String, nullable=True)
    result_title: Mapped[str | None] = mapped_column(String, nullable=True)
    download_client_type: Mapped[str | None] = mapped_column(String, nullable=True)
    client_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    issue: Mapped["Issue"] = relationship("Issue", back_populates="download_jobs")
    indexer: Mapped["Indexer | None"] = relationship("Indexer", back_populates="download_jobs")


class WeeklyRelease(Base):
    __tablename__ = "weekly_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("issues.id"), nullable=False)
    release_date: Mapped[date] = mapped_column(Date, nullable=False)
    pulled: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String, default="comicvine")

    issue: Mapped["Issue"] = relationship("Issue", back_populates="weekly_releases")


class DownloadClient(Base):
    __tablename__ = "download_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # nzbget | sabnzbd | qbittorrent
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(String, nullable=True)
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, default="pullbox-comics")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PostProcessingSettings(Base):
    """Singleton (id=1) row driving the post-download move/rename step.

    When enabled, a completed download is relocated under
    ``destination_root or settings.library_path`` using ``folder_pattern`` and
    ``file_pattern`` (see ``services/postprocess.py`` for token rendering).
    """

    __tablename__ = "post_processing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # move | copy | hardlink
    operation: Mapped[str] = mapped_column(String, default="move")
    # Optional override; blank/None falls back to Settings.library_path.
    destination_root: Mapped[str | None] = mapped_column(String, nullable=True)
    folder_pattern: Mapped[str] = mapped_column(
        String, default="{publisher}/{series} ({year})"
    )
    file_pattern: Mapped[str] = mapped_column(String, default="{series} #{issue} - {title}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ImportFile(Base):
    """Tracks the ComicVine-sync state of one imported issue.

    The library-import endpoint creates the real ``Series``/``Issue`` rows
    immediately (with ``comicvine_id = NULL``) and one of these tracking rows per
    imported issue (status ``pending``). The ``sync_imported_issues`` scheduler
    job later, in batches: matches the issue's ``Series`` to a ComicVine volume,
    enriches the owned ``Issue`` in place and marks this row ``synced`` — or
    ``unmatched`` (issue number not in the volume) / ``no_match`` (no ComicVine
    volume found for the series). Terminal statuses are never retried.
    """

    __tablename__ = "import_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("issues.id"), nullable=False, index=True
    )
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.id"), nullable=False, index=True
    )
    # pending | synced | unmatched | no_match
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    issue: Mapped["Issue"] = relationship("Issue")
    series: Mapped["Series"] = relationship("Series")


class SyncStatus(Base):
    """Records the outcome of a named background sync operation.

    One row per ``key`` (e.g. ``comicvine_calendar`` for the weekly-release
    refresh, ``import_backfill`` for the imported-issue ComicVine sync). The
    dashboard reads these to show the user when each sync last ran and whether
    it succeeded (bad API key, rate limit, network error surface in ``message``).
    """

    __tablename__ = "sync_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StoryArc(Base):
    __tablename__ = "story_arcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Primary metadata identity (Metron arc id). Nullable so Metron-origin arcs
    # without a ComicVine cross-reference remain valid.
    metron_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    comicvine_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    count_of_issue_appearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When we last fetched the arc's full cross-series issue list from ComicVine.
    # NULL means only the name (from story_arc_credits) is known so far.
    detail_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    issues: Mapped[list["Issue"]] = relationship(
        "Issue", secondary=issue_story_arcs, back_populates="arcs"
    )
