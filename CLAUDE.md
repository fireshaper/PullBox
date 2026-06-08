# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMPORTANT:
# Always read memory-bank/@architecture.md before writing any code. Include entire database schema.
# Always read memory-bank/@app-design-document.md before writing any code.
# After adding a major feature or completing a milestone, update memory-bank/@architecture.md.

## Project Overview

**PullBox** is a self-hosted comic book acquisition manager. It integrates with torrent and Usenet sources to automatically find, download, and organize comic books, improving upon tools like Mylar3 with a polished weekly pull list experience, flexible subscription management, and a reliable retry-until-done download queue.

**Current Status:** Design phase. Architecture and tech stack finalized; implementation not yet started.

### Key Design Documents
- `app-design-document.md` — Complete feature specification, data models, UI routes, and architecture diagrams
- `tech-stack.md` — Rationale for each technology choice with alternatives considered

## Architecture Overview

### Tech Stack

| Layer | Technology | Details |
|---|---|---|
| **Backend** | Python 3.12 + FastAPI | Async, native `asyncio` for parallel indexer searches |
| **Database** | SQLite (dev) → PostgreSQL (prod) | SQLAlchemy 2.x async ORM + Alembic migrations |
| **Task Scheduler** | APScheduler 4.x | In-process scheduling: nightly release refresh, daily queue retry, 5-min client polling |
| **Frontend** | React 18 + Vite + TailwindCSS | SPA with shadcn/ui + Radix UI components |
| **HTTP Client** | httpx (async) | Parallel fan-out across ComicVine, Prowlarr, Jackett, Newznab indexers |
| **Real-time** | Server-Sent Events (SSE) | Unidirectional queue status updates |
| **State Management** | TanStack Query v5 (server) + Zustand (UI) | Query caching and optimistic updates |
| **Routing** | TanStack Router | Type-safe, search-param aware routing |
| **Deployment** | Docker + Docker Compose | Single container: FastAPI + static frontend + APScheduler |

### Backend Architecture

The FastAPI backend is organized into logical services that communicate with external APIs and the database:

```
┌─────────────────────────────────────────────────────┐
│                     FastAPI REST API                │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│ Core Services (async)                              │
│                                                     │
│  • PullListService    → ComicVine API, DB          │
│  • DownloadQueueService → Search, retry scheduler  │
│  • LibraryService     → File mgmt, DB              │
│  • SearchService      → Multi-source fan-out       │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│ External Integrations (via httpx.AsyncClient)      │
│                                                     │
│  Indexers:            Download Clients:            │
│  • ComicVine API      • qBittorrent WebAPI         │
│  • Prowlarr API       • NZBGet API                 │
│  • Jackett Torznab    • SABnzbd API                │
│  • Newznab (direct)                                │
│  • NZBHydra2                                       │
└─────────────────────────────────────────────────────┘
```

**Key Design Patterns:**
- **Async-first**: All I/O operations use `async`/`await` with `asyncio.gather()` for parallel indexer searches
- **Single httpx.AsyncClient instance** with connection pooling for all external API calls
- **Persistent scheduler**: APScheduler stores job state in the database; missed runs execute on startup
- **Exponential backoff retry logic**: Failed downloads retry at 1, 2, 4, 7 day intervals (capped)

### Data Model

Key entities and their relationships:

- **Series** — Comic series metadata (title, publisher, ComicVine ID, subscription status)
- **Issue** — Individual comic issues with status (wanted|downloading|downloaded|skipped), file path
- **DownloadJob** — Persistent queue entry linking issues to search results, with retry state and attempt history
- **Indexer** — Configuration for Prowlarr, Jackett, Newznab, NZBHydra2 endpoints (name, URL, API key, priority, enabled flag)
- **WeeklyRelease** — Upcoming releases from ComicVine, pulled into the UI's pull list view

See `app-design-document.md` (Data Models section) for full schema.

### Frontend Architecture

Single-page React app with client-side routing:

```
Root
├── /pull-list              ← Default landing page; shows releases for current + future weeks
├── /series
│   ├── /series/search      ← Search ComicVine, add series
│   ├── /series/:id         ← Series detail + issue grid/list
│   └── /series/:id/issue/:issueId
├── /queue                  ← Download queue manager with retry history
├── /library                ← Downloaded comics grid/list
└── /settings
    ├── /settings/indexers         ← CRUD indexer configs
    ├── /settings/download-clients ← qBit / NZBGet / SABnzbd setup
    └── /settings/general          ← Library path, retry time, API keys
```

**State Management:**
- **TanStack Query** handles all server state (series, issues, queue, settings) with automatic caching, background polling (30s for queue), and optimistic updates
- **Zustand store** for UI-only state: sidebar open/closed, theme preference
- **SSE stream** (`/api/events`) broadcasts queue state changes to all connected clients

## Development Setup (When Implementation Begins)

### Backend

**Environment & Dependencies:**
```bash
# Create and activate virtualenv (using uv, preferred over pip)
uv sync

# Install optional dev dependencies
uv sync --group dev
```

**Key backend dependencies:**
- `fastapi` — Web framework
- `sqlalchemy[asyncio]` + `aiosqlite` — ORM and async SQLite driver
- `alembic` — Schema migrations
- `apscheduler` — Task scheduling
- `httpx` — Async HTTP client
- `pydantic[email]` — Request/response validation and settings
- `lxml` — XML parsing for Newznab responses
- `pytest` + `pytest-asyncio` — Testing

**Running the backend:**
```bash
# Development (auto-reload on file changes)
fastapi dev src/main.py

# Or with uvicorn directly
uvicorn src.main:app --reload --port 8585
```

**Database Migrations:**
```bash
# Create a new migration (after schema changes)
alembic revision --autogenerate -m "describe the change"

# Apply pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

**Testing:**
```bash
# Run all backend tests
pytest

# Run a single test file
pytest tests/test_indexers.py

# Run with coverage
pytest --cov=src

# Watch mode (auto-rerun on change)
pytest-watch
```

**Linting & Formatting:**
```bash
# Format code with ruff
ruff format src/ tests/

# Lint with ruff
ruff check src/ tests/

# Run both
ruff check --fix src/ tests/
```

### Frontend

**Environment & Dependencies:**
```bash
cd frontend
npm install  # or yarn install
```

**Key frontend dependencies:**
- `react@18` + `react-dom` — UI framework
- `@tanstack/react-query@v5` — Server state management
- `@tanstack/react-router` — Type-safe routing
- `zustand` — Light global UI state
- `shadcn/ui` + `@radix-ui/*` — Headless component primitives
- `tailwindcss@v4` — Utility-first CSS
- `typescript` — Type safety
- `vitest` — Unit test runner
- `eslint` + `prettier` — Linting and formatting

**Running the frontend:**
```bash
# Development with HMR
npm run dev

# Production build (outputs to dist/)
npm run build

# Preview production build locally
npm run preview

# Type check
npm run type-check
```

**Testing:**
```bash
# Run all frontend tests
npm run test

# Run in watch mode
npm run test:watch

# Run with coverage
npm run test:coverage
```

**Linting & Formatting:**
```bash
# Format with prettier
npm run format

# Lint with eslint
npm run lint

# Check types
npm run type-check
```

### Docker Build & Deployment

```bash
# Build the image
docker build -t pullbox:latest .

# Run locally with docker-compose
docker-compose up

# Build and start in background
docker-compose up -d
```

The Dockerfile will:
1. Build the frontend (React + Vite) into static files
2. Copy frontend build into the FastAPI static files directory
3. Start FastAPI, which serves both the API and the frontend SPA

## Search & Indexer Integration Strategy

The search system is a **parallel fan-out architecture**:

1. **Query Construction**: Build search strings with variants (e.g., `Batman #123`, `Batman 0123`, etc.)
2. **Parallel Dispatch**: Send queries to all enabled indexers simultaneously via `asyncio.gather()`
3. **Response Aggregation**: Collect results from all sources (Prowlarr torrent + Usenet, Jackett torrents, direct Newznab endpoints)
4. **Scoring**: Rank results by title match quality, format preference (CBZ > CBR > PDF), seeder count (torrents), poster age (Usenet)
5. **Client Selection**: Pick the best result per source type, dispatch to appropriate download client
6. **Fallback**: On client-side failure, retry with next-best result

**Indexer Priority Order** (configurable per indexer in DB):
- Prowlarr API (if configured) — unified torrent + Usenet search
- Jackett (fallback torrent aggregator)
- Direct Newznab endpoints (individual Usenet indexers)
- NZBHydra2 (Usenet meta-aggregator)

**Newznab-specific notes:**
- Category `7030` (Books/Comics) is the primary search category
- Falls back to `7000` (Books) if `7030` returns empty
- Extracts NZB URL from `<enclosure>` element in RSS response
- Each Newznab indexer stores: name, URL, API key, priority, enabled flag, optional retention-days

## Configuration

PullBox is configured via `config.yaml` at `/config/config.yaml` (Docker mount), with environment variable overrides for secrets.

**Required Settings:**
- `library_path` — Root folder for downloaded files (e.g., `/comics`)
- `comicvine_api_key` — ComicVine API key (from https://comicvine.gamespot.com/api/documentation/)

**Optional but Common:**
- `retry_time` — Daily cron time for queue processing (default: `06:00`)
- `prowlarr_url` + `prowlarr_api_key` — Prowlarr aggregator (if configured)
- `download_client_torrent` — qBittorrent connection (URL, credentials, category)
- `download_client_usenet` — NZBGet or SABnzbd connection (URL, credentials, category)

Indexers, download clients, and series subscriptions are stored in the database and managed via the Settings UI.

## Common Development Tasks

### Adding a New Indexer Type

1. Add the type constant to the `Indexer.type` field enum in the data model
2. Create a new async client class (e.g., `FoobarIndexerClient`) in `src/services/indexers/`
3. Register it in the search dispatcher
4. Add tests for parsing responses and constructing queries

### Adding a Download Client Backend

1. Create a new async client class (e.g., `MyDownloadClient`) that extends a base interface
2. Implement `add_torrent()` and `add_nzb()` methods
3. Register it in the download queue service
4. Add status polling logic (check download progress, handle completion)

### Modifying the Data Model

1. Update the SQLAlchemy model in `src/models/`
2. Create an Alembic migration: `alembic revision --autogenerate -m "your change"`
3. Review the generated migration file (autogenerate is not always perfect)
4. Apply: `alembic upgrade head`
5. Update related Pydantic schemas (API request/response models)

### Running the Full Stack Locally

```bash
# Terminal 1: Backend
fastapi dev src/main.py

# Terminal 2: Frontend
cd frontend && npm run dev

# Visit http://localhost:5173 (Vite dev server, proxies /api to :8585)
```

## Decisions to Know

### Why APScheduler instead of Celery?

Celery requires Redis/RabbitMQ as a separate service and a worker process. APScheduler runs in-process, persists job state to the database, and handles missed runs (important for a home server with spotty uptime). For a single-user self-hosted app, APScheduler is the right fit.

### Why SQLite as default?

SQLite in WAL mode handles concurrent reads (API + scheduler) without locking. For a single-user app with thousands of rows (not millions), it's faster and zero-maintenance compared to PostgreSQL. The SQLAlchemy setup makes swapping to PostgreSQL a one-line connection-string change if needed.

### Why TanStack Query for state?

Most of the UI is server state: series lists, issue statuses, queue state. TanStack Query handles caching, background polling, cache invalidation, and optimistic updates, eliminating the need for Redux or complex custom hooks. Zustand covers the tiny bit of UI-only state (sidebar, theme).

### Why not WebSockets for real-time?

The queue view only needs server → client updates (download progress, status changes). Server-Sent Events (SSE) is unidirectional, works over plain HTTP, and avoids the upgrade handshake complexity of WebSockets. FastAPI has first-class SSE support.

## Key Files to Understand First

When implementation begins, prioritize these areas:

1. **Database schema** — `src/models/` will contain all SQLAlchemy models (Series, Issue, DownloadJob, Indexer, WeeklyRelease)
2. **Search orchestration** — `src/services/search.py` — parallel fan-out logic and result scoring
3. **Queue retry engine** — `src/services/download_queue.py` — APScheduler integration, backoff logic
4. **Indexer implementations** — `src/services/indexers/` — one file per indexer type (Prowlarr, Jackett, Newznab, NZBHydra2)
5. **Download clients** — `src/services/download_clients/` — qBittorrent, NZBGet, SABnzbd implementations
6. **Frontend API hooks** — `frontend/src/hooks/api/` — TanStack Query hooks for each API endpoint
7. **Pull list component** — `frontend/src/pages/PullList.tsx` — landing page logic, week navigation, filtering

## Build & Deployment

### Local Docker Build

```bash
docker build -t pullbox:latest .
docker run -it -p 8585:8585 \
  -v $(pwd)/config:/config \
  -v /your/comics:/comics \
  pullbox:latest
```

### Docker Compose (Production)

```yaml
version: '3.8'
services:
  pullbox:
    image: pullbox:latest
    restart: unless-stopped
    ports:
      - "8585:8585"
    volumes:
      - ./config:/config
      - /your/comics/path:/comics
    environment:
      - TZ=America/New_York
      - PULLBOX_SECRET_KEY=changeme
```

The container runs a single FastAPI + APScheduler process. No separate frontend server or task worker needed.

## Testing Strategy

### Backend

- **Unit tests** for individual services: indexer clients, search scoring, retry logic
- **Integration tests** for API endpoints: mock external APIs (ComicVine, Prowlarr)
- **Database tests** with temporary SQLite in-memory DB for model and migration testing
- Use `pytest.mark.asyncio` for async test functions

### Frontend

- **Component tests** with Vitest + React Testing Library
- **Hook tests** for TanStack Query custom hooks (mock API calls)
- **E2E tests** (optional, lower priority for MVP) with Playwright or Cypress

## Useful References

- **app-design-document.md** — Feature spec, data models, UI routes
- **tech-stack.md** — Technology rationale and alternatives
- FastAPI docs: https://fastapi.tiangolo.com/
- SQLAlchemy async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- APScheduler: https://apscheduler.readthedocs.io/
- TanStack Query: https://tanstack.com/query/v5/docs/react/overview
- shadcn/ui: https://ui.shadcn.com/
- Tailwind CSS v4: https://tailwindcss.com/blog/tailwindcss-v4
