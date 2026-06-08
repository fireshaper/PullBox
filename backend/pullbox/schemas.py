from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    comicvine_id: str
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
    comicvine_id: str
    issue_number: str
    title: str | None
    cover_date: date | None
    store_date: date | None
    cover_url: str | None
    status: str
    file_path: str | None
    created_at: datetime
    updated_at: datetime


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
