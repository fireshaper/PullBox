# PullBox

A self-hosted comic book acquisition manager. Subscribe to series, see what's releasing each week, and let PullBox find and download issues automatically via Usenet or torrents.

---

## Prerequisites

- **Docker** and **Docker Compose** (V2 — `docker compose`)
- A **ComicVine API key** — free, get one at [comicvine.gamespot.com/api/documentation](https://comicvine.gamespot.com/api/documentation/)
- A **Newznab indexer** (e.g. NZBGeek, DrunkenSlug) and/or a **Prowlarr/Jackett** instance for search
- **NZBGet** or **SABnzbd** as your download client

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/youruser/pullbox.git
cd pullbox
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
TZ=America/New_York
COMICS_PATH=/your/comics/library    # host path where files are saved
PULLBOX_SECRET_KEY=                 # run: openssl rand -hex 32
PULLBOX_COMICVINE_API_KEY=          # from comicvine.gamespot.com
```

### 2. (Optional) Edit the config file

For advanced settings, copy the example config:

```bash
cp config/config.example.yaml config/config.yaml
```

The defaults work out of the box. You only need to edit this if you want to change the database path, retry schedule, or lookahead weeks. Settings in `config.yaml` are overridden by any `PULLBOX_*` environment variable.

### 3. Start the container

```bash
docker compose up -d
```

Open **http://localhost:8585** in your browser.

The database is created automatically on first start at `config/pullbox.db`.

---

## First Run

### Add indexers

Go to **Settings → Indexers** and add at least one search source:

| Type | What it is |
|---|---|
| `newznab` | Direct Newznab/NZBHydra2 Usenet indexer |
| `prowlarr` | Prowlarr aggregator (torrent + Usenet) |
| `jackett` | Jackett torrent aggregator |

For Newznab indexers you can click **Test** to verify the connection.

### Configure your download client

Go to **Settings → Download Clients** and add NZBGet or SABnzbd with the host, port, and credentials.

### Subscribe to a series

1. Go to **Series** and search for a title (powered by ComicVine)
2. Click a result to add it to your library
3. On the series page, click **Sync Issues** to pull the full issue list
4. Toggle **Subscribe** to auto-download new issues, or mark individual issues as **Wanted** to queue them manually

### See what's releasing

The **Pull List** page shows upcoming releases for the current week and the next two weeks. Click **Download** on any issue to add it to the queue immediately.

### Monitor downloads

The **Queue** page shows all active, pending, and failed jobs. Failed jobs retry automatically each morning (configurable via `retry_time`). You can also retry any failed job manually.

---

## Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `comicvine_api_key` | — | **Required.** ComicVine API key |
| `secret_key` | `changeme` | **Change this.** Used for internal token signing |
| `library_path` | `/comics` | Root folder for downloaded files |
| `retry_time` | `06:00` | Daily time (24h) to retry failed downloads |
| `max_retries` | `20` | Max attempts per issue before permanent failure |
| `pull_list_lookahead_weeks` | `2` | Weeks ahead shown in the pull list |
| `database_url` | SQLite at `/config/pullbox.db` | Override to use PostgreSQL |
| `debug` | `false` | Verbose logging and FastAPI debug pages |

All settings can be overridden with environment variables prefixed `PULLBOX_` (e.g. `PULLBOX_RETRY_TIME=08:00`).

---

## Updating

```bash
docker compose pull
docker compose up -d
```

Database migrations run automatically on startup.

---

## Development

### Backend

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
python -m uv sync
python -m uv run fastapi dev pullbox/main.py
```

API available at **http://localhost:8585**. Interactive docs at **http://localhost:8585/docs**.

```bash
# Run tests
python -m uv run pytest

# Lint
python -m uv run ruff check pullbox/ tests/
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend dev server at **http://localhost:5173**, proxies `/api` calls to the backend.

```bash
npm run test        # unit tests
npm run type-check  # TypeScript validation
```

### Both together

```bash
# Terminal 1
cd backend && python -m uv run fastapi dev pullbox/main.py

# Terminal 2
cd frontend && npm run dev
```

---

## Data & Backups

Everything lives in the `config/` directory:

- `config/pullbox.db` — SQLite database (series, issues, queue, indexers)
- `config/config.yaml` — your configuration

Back up the `config/` folder to preserve all data. The comics library itself (mounted at `/comics`) is managed by your download client and is not touched by PullBox directly.
