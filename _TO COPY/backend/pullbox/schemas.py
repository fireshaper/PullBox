from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # NULL for import-origin series not yet matched to a ComicVine volume.
    comicvine_id: str | None
    title: str
    publisher: str | None
    start_year: int | None
    status: str
    subscribed: bool
    auto_download: bool
    cover_url: str | None
    created_at: datetime


class SeriesDetailResponse(SeriesResponse):
    description: str | None


class IssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    series_id: int
    # NULL for import-origin issues not yet synced with ComicVine.
    comicvine_id: str | None
    issue_number: str
    title: str | None
    cover_date: date | None
    store_date: date | None
    cover_url: str | None
    status: str
    file_path: str | None
    created_at: datetime
    updated_at: datetime


# ── Story arc schemas ─────────────────────────────────────────────────────────


class StoryArcSummary(BaseModel):
    """Lightweight arc reference used for badges on the issue list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    comicvine_id: str
    name: str


class IssueListItem(IssueResponse):
    """Issue list row augmented with the arcs the issue belongs to (for badges)."""

    arcs: list[StoryArcSummary] = []


class ArcMemberIssue(BaseModel):
    """A single issue within a story arc's cross-series member list.

    ComicVine only supplies id/name/site_detail_url for arc members, so issues
    outside the local library have no issue number or series. Members matched to
    the local library are hydrated with `local_*` fields for internal linking.
    """

    comicvine_id: str
    name: str | None
    site_detail_url: str | None
    in_library: bool
    local_issue_id: int | None = None
    local_series_id: int | None = None
    local_series_title: str | None = None
    local_issue_number: str | None = None
    local_status: str | None = None


class StoryArcDetail(BaseModel):
    """A story arc plus its full member issue list, for the expandable panel."""

    id: int
    comicvine_id: str
    name: str
    publisher: str | None
    cover_url: str | None
    description: str | None
    count_of_issue_appearances: int | None
    issues: list[ArcMemberIssue]


class PaginatedSeriesResponse(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[SeriesResponse]


class SeriesSearchResult(BaseModel):
    comicvine_id: str
    title: str
    publisher: str | None
    start_year: int | None
    cover_url: str | None
    description: str | None
    issue_count: int
    in_library: bool


class AddSeriesRequest(BaseModel):
    comicvine_id: str
    subscribed: bool = False
    auto_download: bool = False


class UpdateSeriesRequest(BaseModel):
    subscribed: bool | None = None
    auto_download: bool | None = None


class SyncIssuesResponse(BaseModel):
    added: int
    updated: int
    total: int


class MarkAllWantedResponse(BaseModel):
    marked: int


# ── Indexer schemas (Phase 5) ─────────────────────────────────────────────────


class IndexerCreate(BaseModel):
    name: str
    type: str
    url: str
    api_key: str | None = None
    enabled: bool = True
    priority: int = 100
    usenet_retention_days: int | None = None


class IndexerUpdate(BaseModel):
    """All fields optional — only provided fields are written to the database."""

    name: str | None = None
    type: str | None = None
    url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    usenet_retention_days: int | None = None


class IndexerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    url: str
    api_key: str | None
    enabled: bool
    priority: int
    usenet_retention_days: int | None
    last_tested_at: datetime | None
    last_test_success: bool | None
    created_at: datetime
    updated_at: datetime


class IndexerTestResponse(BaseModel):
    success: bool
    message: str


# ── Releases schemas (Phase 10) ──────────────────────────────────────────────


class ReleaseIssueSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    status: str


class ReleaseSeriesSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    publisher: str | None


class WeeklyReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    release_date: date
    pulled: bool
    issue: ReleaseIssueSummary
    series: ReleaseSeriesSummary


# ── Queue schemas (Phase 7) ───────────────────────────────────────────────────


class IssueSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    status: str


class SeriesSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    publisher: str | None


# ── Download Client schemas ───────────────────────────────────────────────────


class DownloadClientCreate(BaseModel):
    name: str
    type: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    category: str = "pullbox-comics"
    enabled: bool = True


class DownloadClientUpdate(BaseModel):
    """All fields optional — only provided fields are written to the database."""

    name: str | None = None
    type: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    category: str | None = None
    enabled: bool | None = None


class DownloadClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    host: str
    port: int
    username: str | None
    password: str | None
    api_key: str | None
    category: str
    enabled: bool
    last_tested_at: datetime | None
    last_test_success: bool | None
    created_at: datetime
    updated_at: datetime


class DownloadClientTestResponse(BaseModel):
    success: bool
    message: str


# ── Post-processing (post-download actions) schemas ───────────────────────────


class PostProcessingSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    operation: str
    destination_root: str | None
    folder_pattern: str
    file_pattern: str
    created_at: datetime
    updated_at: datetime


class PostProcessingSettingsUpdate(BaseModel):
    """All fields optional — only provided fields are written to the database."""

    enabled: bool | None = None
    operation: str | None = None
    destination_root: str | None = None
    folder_pattern: str | None = None
    file_pattern: str | None = None


class PostProcessingPreviewRequest(BaseModel):
    """Render a sample target path. Patterns fall back to the saved settings when
    omitted; sample issue/series values are used when no fields are supplied."""

    folder_pattern: str | None = None
    file_pattern: str | None = None
    destination_root: str | None = None
    # Optional sample overrides (default to a Batman example).
    series: str | None = None
    publisher: str | None = None
    year: int | None = None
    issue: str | None = None
    title: str | None = None
    ext: str | None = None


class PostProcessingPreviewResponse(BaseModel):
    path: str


# ── Library import schemas (Phase 16) ─────────────────────────────────────────


class ScannedFile(BaseModel):
    file_path: str
    issue_number: str | None


class ScannedSeries(BaseModel):
    title: str
    year: int | None
    file_count: int
    files: list[ScannedFile]


class LibraryScanRequest(BaseModel):
    path: str


class LibraryScanResponse(BaseModel):
    root: str
    unparsed_count: int
    series: list[ScannedSeries]


class ImportSeriesSelection(BaseModel):
    """One scanned series to import: just its parsed name/year and files. No
    ComicVine data — matching happens later in the background scheduler."""

    title: str
    year: int | None = None
    files: list[ScannedFile]


class LibraryImportRequest(BaseModel):
    series: list[ImportSeriesSelection]


class LibraryImportResponse(BaseModel):
    series_queued: int
    files_queued: int
    errors: list[str]


class ImportStatusResponse(BaseModel):
    """Snapshot of the imported-issue ComicVine backfill."""

    pending_files: int
    series_pending: int
    synced_files: int
    unmatched_files: int
    no_match_files: int


class DownloadJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int
    source_type: str
    indexer_id: int | None
    result_guid: str | None
    result_title: str | None
    download_client_type: str | None
    client_job_id: str | None
    status: str
    attempts: int
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime
    issue: IssueSummary | None = None
    series: SeriesSummary | None = None


class RetryFailedResponse(BaseModel):
    retried: int


# ── Dashboard schemas ─────────────────────────────────────────────────────────


class DashboardIssueRef(BaseModel):
    """Flat issue+series reference for dashboard job cards."""

    id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    status: str
    series_id: int
    series_title: str


class DashboardJob(BaseModel):
    id: int
    status: str
    attempts: int
    source_type: str
    result_title: str | None
    download_client_type: str | None
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    updated_at: datetime
    issue: DashboardIssueRef | None


class QueueHealth(BaseModel):
    queued: int
    searching: int
    pending: int
    downloading: int
    failed: int


class DashboardActivityResponse(BaseModel):
    queue_health: QueueHealth
    active_downloads: list[DashboardJob]
    recent_completed: list[DashboardJob]
    recent_failed: list[DashboardJob]


class LibraryStats(BaseModel):
    total_series: int
    total_issues: int
    downloaded_issues: int
    storage_bytes: int


class SyncInfo(BaseModel):
    last_run_at: datetime | None
    success: bool | None
    message: str | None


class DashboardSyncStatus(BaseModel):
    calendar: SyncInfo
    backfill: SyncInfo
    next_backfill_at: datetime | None
    import_pending: int


class DashboardLibraryItem(BaseModel):
    id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    series_id: int
    series_title: str
    updated_at: datetime


class StuckSeries(BaseModel):
    series_id: int
    series_title: str
    publisher: str | None
    wanted_count: int
    max_attempts: int


class DashboardOverviewResponse(BaseModel):
    library_stats: LibraryStats
    sync_status: DashboardSyncStatus
    recent_library: list[DashboardLibraryItem]
    stuck_series: list[StuckSeries]


class DashboardRelease(BaseModel):
    issue_id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    status: str
    release_date: date
    series_id: int
    series_title: str
    publisher: str | None
    subscribed: bool


class DashboardPullResponse(BaseModel):
    week: str
    this_week: list[DashboardRelease]
    upcoming: list[DashboardRelease]
