from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, model_validator


class SeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Primary metadata id (Metron). NULL for import-origin series not yet matched.
    metron_id: str | None
    # ComicVine cross-reference / id. NULL when unknown.
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
    # Primary metadata id (Metron). NULL for import-origin issues not yet synced.
    metron_id: str | None
    # ComicVine cross-reference / id. NULL when unknown.
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
    metron_id: str | None
    comicvine_id: str | None
    name: str


class IssueListItem(IssueResponse):
    """Issue list row augmented with the arcs the issue belongs to (for badges)."""

    arcs: list[StoryArcSummary] = []


class ArcMemberIssue(BaseModel):
    """A single issue within a story arc's cross-series member list.

    The metadata source only supplies id/name/site_detail_url for arc members, so
    issues outside the local library have no issue number or series. Members matched
    to the local library are hydrated with `local_*` fields for internal linking.
    """

    metron_id: str | None = None
    comicvine_id: str | None = None
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
    metron_id: str | None
    comicvine_id: str | None
    name: str
    publisher: str | None
    cover_url: str | None
    description: str | None
    count_of_issue_appearances: int | None
    issues: list[ArcMemberIssue]


# ── Story arcs page (/api/arcs) ──────────────────────────────────────────────


class StoryArcListItem(BaseModel):
    """One row on the Story Arcs page.

    ``owned`` counts the arc's members PullBox tracks locally; ``total`` is the
    arc's true size from the metadata source and is NULL until the arc's detail
    has been fetched at least once — so a missing total means "unknown", not zero.
    """

    id: int
    metron_id: str | None
    comicvine_id: str | None
    name: str
    publisher: str | None
    cover_url: str | None
    subscribed: bool
    auto_download: bool
    total: int | None
    owned: int
    downloaded: int
    wanted: int
    series_count: int
    detail_synced_at: datetime | None


class ArcIssueRow(BaseModel):
    """A locally-tracked member of an arc, as shown on the arc detail page."""

    id: int
    series_id: int
    series_title: str
    issue_number: str
    title: str | None
    cover_url: str | None
    cover_date: date | None
    store_date: date | None
    status: str
    has_file: bool


class StoryArcPageDetail(StoryArcListItem):
    """An arc plus every member PullBox tracks, newest series first.

    Served entirely from cached rows — opening the page costs no provider call.
    Discovering members PullBox does *not* have is the explicit ``/sync`` action.
    """

    description: str | None
    issues: list[ArcIssueRow] = []


class StoryArcUpdate(BaseModel):
    """Partial update of an arc's subscription flags (PATCH semantics)."""

    subscribed: bool | None = None
    auto_download: bool | None = None


class ArcSyncResponse(BaseModel):
    """Outcome of resolving an arc's member list against the local library."""

    members: int
    in_library: int
    added: int
    enqueued: int
    failed: int
    remaining: int
    rate_limited: bool
    message: str


class ArcDownloadResponse(BaseModel):
    enqueued: int
    message: str


# ── External (companion-app) schemas ─────────────────────────────────────────
#
# Consumed by Thwip and any other local companion. These are served entirely from
# cached DB rows — the external router never calls the metadata provider, so a
# companion polling on every library scan can't drain the shared Metron budget.


class ExternalArc(BaseModel):
    """An arc as PullBox has it cached, embedded on an external issue row.

    ``count_of_issue_appearances`` is the arc's *true* total across all series
    (from the metadata source), not the number of members PullBox owns — a
    companion can use it to classify one-shots/minis without its own lookup.
    It is NULL until the arc's detail has been fetched at least once.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    metron_id: str | None
    comicvine_id: str | None
    name: str
    publisher: str | None
    cover_url: str | None
    description: str | None
    count_of_issue_appearances: int | None
    detail_synced_at: datetime | None


class ExternalSeries(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    metron_id: str | None
    comicvine_id: str | None
    title: str
    publisher: str | None
    start_year: int | None


class ExternalIssue(BaseModel):
    """One issue PullBox has a file on disk for, with everything a reader needs.

    ``path_rel`` is ``file_path`` made relative to PullBox's resolved library
    root, with forward slashes — the join key for a companion that scans the same
    folder under a different mount point. It is NULL when ``file_path`` lies
    outside the library root, in which case only ``file_path`` can be matched on.
    """

    id: int
    metron_id: str | None
    comicvine_id: str | None
    issue_number: str
    title: str | None
    cover_date: date | None
    store_date: date | None
    cover_url: str | None
    status: str
    file_path: str
    path_rel: str | None
    updated_at: datetime
    arcs_synced_at: datetime | None
    series: ExternalSeries
    arcs: list[ExternalArc] = []


class ExternalLibraryPage(BaseModel):
    """One page of the external library feed.

    ``library_path`` is echoed back so a companion can sanity-check that both
    apps are pointed at the same folder before trusting ``path_rel``.
    """

    total: int
    limit: int
    offset: int
    library_path: str
    items: list[ExternalIssue]


class ExternalArcMember(BaseModel):
    """A locally-owned member of an arc, from the external arc endpoint."""

    issue_id: int
    metron_id: str | None
    comicvine_id: str | None
    series_id: int
    series_title: str
    issue_number: str
    title: str | None
    cover_date: date | None
    status: str
    file_path: str | None
    path_rel: str | None


class ExternalArcDetail(ExternalArc):
    """An arc plus the members **PullBox owns**.

    Unlike the internal ``/api/issues/{id}/arcs`` panel, this does not fetch the
    arc's live cross-series member list — serving it would mean a provider call
    per arc per request. ``members`` is therefore local rows only; compare its
    length against ``count_of_issue_appearances`` to see how much of the arc is
    actually held.
    """

    members: list[ExternalArcMember] = []


class PaginatedSeriesResponse(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[SeriesResponse]


class SeriesSearchResult(BaseModel):
    metron_id: str | None = None
    comicvine_id: str | None = None
    title: str
    publisher: str | None
    start_year: int | None
    cover_url: str | None
    description: str | None
    issue_count: int
    in_library: bool


class AddSeriesRequest(BaseModel):
    # A search result carries whichever id its source provided. Exactly one is required.
    metron_id: str | None = None
    comicvine_id: str | None = None
    subscribed: bool = False
    auto_download: bool = False

    @model_validator(mode="after")
    def _require_one_id(self) -> "AddSeriesRequest":
        if not self.metron_id and not self.comicvine_id:
            raise ValueError("one of metron_id or comicvine_id is required")
        return self


class UpdateSeriesRequest(BaseModel):
    subscribed: bool | None = None
    auto_download: bool | None = None


class SyncIssuesResponse(BaseModel):
    added: int
    updated: int
    total: int


class MarkAllWantedResponse(BaseModel):
    marked: int


class SeriesRescanResponse(BaseModel):
    """Result of a disk re-scan of one series' folder.

    ``found`` and ``relinked`` are disjoint: the first is an issue that had no
    file, the second an issue whose tracked file vanished but whose replacement
    (renamed / re-encoded) turned up in the same folder.
    """

    found: int
    relinked: int
    missing: int
    unchanged: int
    files_scanned: int
    folders: list[str]
    unmatched_files: list[str]
    message: str


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


class IndexerTestRequest(BaseModel):
    """Ad-hoc indexer config to test before it has been saved."""

    type: str
    url: str
    api_key: str | None = None


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
    # The weekly refresh creates a local Series row for *every* release, so row
    # existence says nothing about whether the user follows it — this does.
    subscribed: bool


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


class DownloadClientTestRequest(BaseModel):
    """Ad-hoc download-client config to test before it has been saved."""

    type: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    api_key: str | None = None


class DownloadClientTestResponse(BaseModel):
    success: bool
    message: str


# ── Post-processing (post-download actions) schemas ───────────────────────────


class GeneralSettingsResponse(BaseModel):
    """The saved override plus everything the UI needs to explain it."""

    id: int
    # The override as stored. None means "use the config file's value".
    library_path: str | None
    # What PullBox actually uses: the override, or config_library_path.
    effective_path: str
    # The config file's library_path, shown as the fallback when no override is set.
    config_library_path: str
    # Advisory checks against effective_path (see services/general.describe_path).
    exists: bool
    writable: bool
    created_at: datetime
    updated_at: datetime


class GeneralSettingsUpdate(BaseModel):
    """Only provided fields are written. Send ``null``/``""`` to clear an override."""

    library_path: str | None = None


class PostProcessingSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    operation: str
    destination_root: str | None
    folder_pattern: str
    file_pattern: str
    delete_empty_folder: bool
    created_at: datetime
    updated_at: datetime


class PostProcessingSettingsUpdate(BaseModel):
    """All fields optional — only provided fields are written to the database."""

    enabled: bool | None = None
    operation: str | None = None
    destination_root: str | None = None
    folder_pattern: str | None = None
    file_pattern: str | None = None
    delete_empty_folder: bool | None = None


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


# ── File health (Settings → File Health) ──────────────────────────────────────


class FileIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int | None
    file_path: str
    kind: str
    severity: str
    detail: str
    size_bytes: int | None
    detected_at: datetime
    # Populated by the router from the joined Issue/Series when issue_id is set.
    series_id: int | None = None
    series_title: str | None = None
    issue_number: str | None = None


class FileHealthSummary(BaseModel):
    total: int
    errors: int
    warnings: int
    # Count per kind, e.g. {"wrong_format": 3, "missing": 1}.
    by_kind: dict[str, int]


class FileHealthResponse(BaseModel):
    summary: FileHealthSummary
    issues: list[FileIssueResponse]
    last_scan_at: datetime | None
    last_scan_message: str | None
    scanned_root: str


class FileHealthScanRequest(BaseModel):
    # Optional override of the library root; blank uses the resolved library path.
    path: str | None = None
    # CRC-verify every archive entry. Thorough but reads every byte.
    deep: bool = False


class FileHealthScanResponse(FileHealthResponse):
    files_scanned: int


class FileIssueRecheckResponse(BaseModel):
    resolved: bool
    issue: FileIssueResponse | None


# ── Duplicate series (Settings → Duplicate Series) ────────────────────────────


class DuplicateSeriesRow(BaseModel):
    id: int
    metron_id: str | None
    comicvine_id: str | None
    title: str
    publisher: str | None
    start_year: int | None
    subscribed: bool
    auto_download: bool
    cover_url: str | None
    issue_count: int
    downloaded_count: int


class DuplicateGroupResponse(BaseModel):
    key: str
    title: str
    # Rows disagree on a non-null start_year — probably distinct volumes sharing
    # a title, so the group is shown but excluded from merging.
    conflicting_years: bool
    mergeable: bool
    rows: list[DuplicateSeriesRow]


class DuplicateScanResponse(BaseModel):
    groups: list[DuplicateGroupResponse]
    total_groups: int
    mergeable_groups: int
    conflicting_groups: int


class MergeRequest(BaseModel):
    # Explicit ids rather than a group key: the client merges exactly the rows it
    # showed the user, even if the grouping has shifted since the preview.
    series_ids: list[int]


class MergeResponse(BaseModel):
    kept_series_id: int
    removed_series_ids: list[int]
    issues_moved: int
    issues_merged: int


class MergeAllResponse(BaseModel):
    merged_groups: int
    skipped_groups: int
    issues_moved: int
    issues_merged: int


# ── Calendar ──────────────────────────────────────────────────────────────────


class CalendarEntry(BaseModel):
    issue_id: int
    issue_number: str
    title: str | None
    cover_url: str | None
    status: str
    # Status of the issue's most recent DownloadJob, if it has ever had one.
    # Issue.status never says "failed"; this is where a stalled grab shows up.
    job_status: str | None
    release_date: date
    # Which field the date came from — "cover" means the shelf date is unknown
    # and the row is only accurate to the month.
    date_source: str
    series_id: int
    series_title: str
    publisher: str | None
    subscribed: bool
    auto_download: bool
    # Why this entry is on a subscribed calendar: "series", "arc", or both.
    sources: list[str]


class CalendarSummary(BaseModel):
    total: int
    # Entries not yet downloaded, downloading, or skipped — i.e. still outstanding.
    pending: int
    by_status: dict[str, int]


class CalendarResponse(BaseModel):
    start: date
    end: date
    scope: str
    entries: list[CalendarEntry]
    summary: CalendarSummary
