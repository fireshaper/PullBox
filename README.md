# PullBox

A self-hosted comic book acquisition manager. Subscribe to series, see what's releasing each week, and let PullBox find and download issues automatically via Usenet or torrents.

---

## What's inside

| Page | What it does |
|---|---|
| **Dashboard** | Landing page — live download activity, queue health, library stats, sync status, and this week's pull |
| **Pull List** | Upcoming releases for the current week plus the next *N* weeks; download any issue on the spot |
| **Calendar** | Sonarr-style date grid of upcoming issues, showing which ones PullBox will grab |
| **Series** | Search, subscribe, sync issue lists, mark issues wanted |
| **Story Arcs** | Browse arcs, subscribe to one, and let PullBox fill in the members you're missing |
| **Queue** | Active, pending and failed jobs with full retry history |
| **Settings** | Indexers, download clients, library import, post-processing, file health, duplicate series, general |

---

## Prerequisites

- **Docker** and **Docker Compose** (V2 — `docker compose`)
- **A metadata account** — [Metron](https://metron.cloud/accounts/register/) (free, recommended) and/or a [ComicVine API key](https://comicvine.gamespot.com/api/documentation/). See [Metadata Sources](#metadata-sources).
- **At least one indexer** — a Newznab indexer (NZBGeek, DrunkenSlug), NZBHydra2, Prowlarr, or Jackett
- **A download client** — NZBGet or SABnzbd for Usenet, qBittorrent for torrents

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/fireshaper/pullbox.git
cd pullbox
cp .env.example .env
```

Edit `.env`:

```env
TZ=America/New_York
COMICS_PATH=/your/comics/library    # host path where files are saved
PULLBOX_SECRET_KEY=                 # run: openssl rand -hex 32
PULLBOX_METRON_USERNAME=            # your metron.cloud login
PULLBOX_METRON_PASSWORD=
PULLBOX_COMICVINE_API_KEY=          # optional fallback
```

### 2. (Optional) Edit the config file

```bash
cp config/config.example.yaml config/config.yaml
```

The defaults work out of the box. Anything in `config.yaml` is overridden by the matching `PULLBOX_*` environment variable, so keep secrets in `.env` and everything else here.

### 3. Start the container

```bash
docker compose up -d
```

Open **http://localhost:8585**. The database is created automatically at `config/pullbox.db`.

---

## Metadata Sources

PullBox reads series, issue, arc and release metadata through a provider layer with **one primary source and one fallback**.

### Metron (default primary)

[Metron](https://metron.cloud/) is a community-run comic database. It needs a **registered account** — PullBox authenticates with your login username and password over HTTP Basic auth, *not* an API key. Register at [metron.cloud/accounts/register/](https://metron.cloud/accounts/register/), then set:

```yaml
metron_username: "your-username"
metron_password: "your-password"
metadata_provider: "metron"
```

Metron enforces two independent caps on an account: roughly **20 requests/minute** and **5000/day**. PullBox throttles itself below both (`metron_rate_limit_per_min`, `metron_rate_limit_per_day`) using a single process-wide limiter, and backs off when Metron returns a 429.

### ComicVine

Still fully supported. Set `comicvine_api_key` and, if you want it in charge, `metadata_provider: "comicvine"`. ComicVine's own limit is ~200 requests/hour, which PullBox stays under via `comicvine_rate_limit_per_hour`.

### How the two interact

Configure both and the secondary becomes an automatic fallback:

- **Search and weekly releases** fall back to the other source when the primary errors — and, for search, when it returns nothing.
- **Id-based lookups** (a specific volume, issue, or arc) can't blindly fall back, because a Metron id means nothing to ComicVine. They *route* to whichever source the record actually has an id for, preferring the primary, and try the other only if that one errors and the second id exists.

Every record PullBox stores carries **both** `metron_id` and `comicvine_id` where known, so a series added from one source stays matchable against the other.

> **Switching sources on an existing library:** records added under ComicVine keep only their `comicvine_id` — turning Metron on does not backfill Metron ids for them. They keep working through the routing above. Because the two id spaces are separate, adding the same series again from the other source creates a second row; **Settings → Duplicate Series** finds and merges those.

---

## First Run

### Add indexers

**Settings → Indexers**, add at least one:

| Type | What it is |
|---|---|
| `newznab` | Direct Newznab Usenet indexer |
| `nzbhydra2` | NZBHydra2 Usenet meta-aggregator |
| `prowlarr` | Prowlarr aggregator (torrent + Usenet) |
| `jackett` | Jackett torrent aggregator |

Each has a **Test** button. `priority` decides the order results are preferred in; `usenet_retention_days` lets PullBox skip releases older than your provider's retention.

### Configure a download client

**Settings → Download Clients** — NZBGet, SABnzbd, or qBittorrent, with host, port and credentials.

> **Create the category in your download client first.** PullBox tags every job it submits with a category — `pullbox-comics` by default — and the client must already have that category defined. It is not created for you.
>
> - **SABnzbd:** Config → Categories → add a category named `pullbox-comics`, then Save. SABnzbd silently falls back to `Default` for a category it doesn't recognise, which sends your comics to the wrong folder and can leave post-processing unable to find the finished file.
> - **NZBGet:** Settings → Categories → add `pullbox-comics` and reload.
>
> If you'd rather reuse a category you already have, change the **Category** field on the client in PullBox to match its exact name — the two just have to agree.

### Subscribe to a series

1. **Series** → search for a title
2. Click a result to add it
3. Click **Sync Series** to pull the full issue list
4. Toggle **Subscribe** for auto-download, or mark individual issues **Wanted**

### Import an existing library

**Settings → Library Import** scans a server-side folder and creates series and issues immediately from the parsed folder and filenames — no metadata calls, so a large library populates in seconds. A background job then backfills real metadata in throttled batches; the page shows pending / synced / unmatched counts as it works.

### Watch downloads

**Queue** shows every job. Failed jobs retry on exponential backoff, swept daily at `retry_time` and once at startup (so a window missed while PullBox was down isn't skipped). A job the download client has lost track of is failed and retried after `download_missing_grace_minutes` instead of hanging in "downloading" forever.

---

## Other Settings pages

- **Post-Processing** — move, copy or hardlink completed downloads into an organized tree using `{publisher}/{series} ({year})` and `{series} #{issue} - {title}` patterns. Requires PullBox to see the same filesystem paths the download client reports.
- **File Health** — scans tracked issues *and* everything under the library root for files readers choke on: a RAR named `.cbz`, archives with no images, 0-byte or truncated files, and database rows pointing at files moved or deleted outside PullBox. The default pass reads headers only; a deep scan CRC-verifies every entry to catch bit-rot, at the cost of reading every byte.
- **Duplicate Series** — finds title-collision duplicates and merges them, repointing issues onto the survivor.
- **General** — override the library path at runtime without touching `config.yaml`.

---

## Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `metron_username` / `metron_password` | — | Metron account login (HTTP Basic, not an API key) |
| `metadata_provider` | `metron` | Which source is primary: `metron` or `comicvine` |
| `comicvine_api_key` | — | ComicVine API key; fallback when Metron is primary |
| `secret_key` | `changeme` | **Change this.** Internal token signing |
| `external_api_token` | — | Shared token for `/api/external/*`; blank disables those routes |
| `library_path` | `/comics` | Root folder for downloaded files (Settings → General overrides) |
| `config_path` | `/config` | Where the database and config file live |
| `database_url` | SQLite at `/config/pullbox.db` | Override to use PostgreSQL |
| `retry_time` | `06:00` | Daily time (24h) for the queue retry sweep |
| `max_retries` | `20` | Max attempts per issue before permanent failure |
| `pull_list_lookahead_weeks` | `2` | Weeks ahead shown in the pull list |
| `download_poll_interval_minutes` | `1` | How often to ask clients about active downloads |
| `download_missing_grace_minutes` | `30` | How long a job the client has no record of is given before it's failed |
| `import_sync_interval_minutes` | `5` | Metadata backfill cadence for imported issues |
| `import_sync_batch_size` | `10` | Issues backfilled per batch |
| `arc_sync_interval_minutes` | `60` | Subscribed-arc gap-fill cadence |
| `arc_sync_budget` | `15` | Max provider lookups per arc per run |
| `metron_rate_limit_per_min` | `18` | Local cap, under Metron's 20/min |
| `metron_rate_limit_per_day` | `4800` | Local cap, under Metron's 5000/day |
| `comicvine_rate_limit_per_hour` | `190` | Local cap, under ComicVine's ~200/hr |
| `comicvine_min_interval` | `1.0` | Minimum seconds between ComicVine calls |
| `debug` | `false` | Verbose logging and FastAPI debug pages |
| `db_echo` | `false` | Echo every SQL statement — very noisy, independent of `debug` |

All settings accept a `PULLBOX_`-prefixed environment variable override (e.g. `PULLBOX_RETRY_TIME=08:00`).

---

## Companion apps

`/api/external/*` is a read-only feed for local companion apps such as Thwip. It serves cached database rows only and never calls a metadata provider, so a companion re-syncing on every library scan can't drain your Metron budget — PullBox stays the single throttled caller.

Set `external_api_token` to a random string and give the companion the same value; it sends it as an `X-PullBox-Token` header. **Blank leaves the routes disabled**, which is deliberate — this feed exposes the whole library including on-disk paths.

---

## Updating

```bash
docker compose pull
docker compose up -d
```

Migrations run automatically on startup.

---

## Development

### Backend

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
uv run fastapi dev pullbox/main.py --port 8585
```

API at **http://localhost:8585**, interactive docs at **/docs**.

```bash
uv run pytest                        # tests
uv run ruff check pullbox/ tests/    # lint
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Dev server at **http://localhost:5173**, proxying `/api` to the backend.

```bash
npm run test        # unit tests
npm run type-check  # TypeScript validation
```

### Both together

```bash
# Terminal 1
cd backend && uv run fastapi dev pullbox/main.py --port 8585

# Terminal 2
cd frontend && npm run dev
```

### Deploying to a separate machine

If you develop on one machine and run PullBox on another, `build-deploy.ps1` builds the frontend, stages it into `backend/pullbox/static/`, and produces `pullbox-deploy.zip`:

```powershell
powershell -ExecutionPolicy Bypass -File build-deploy.ps1
```

Copy the zip to the server, unzip, then `uv sync --frozen` and start it. Note that the app serves the frontend as a **pre-built** static bundle — a frontend source edit does not appear in the running app until that bundle is rebuilt.

---

## Troubleshooting

**A frontend change isn't showing.** The running app serves a pre-built bundle from `backend/pullbox/static/`. Rebuild it (`npm run build`, or `build-deploy.ps1`). For live reload during development use `npm run dev` instead.

**Startup hangs before "Database ready".** Run PullBox on a standard CPython (uv-managed is easiest). Some bundled distributions — Anaconda in particular — stall on `aiosqlite`'s async startup.

**Downloads complete but never land in the library.** Check that the category on the client in PullBox (`pullbox-comics` by default) exists in SABnzbd or NZBGet under exactly that name. An unknown category falls back to the client's `Default`, so the file finishes somewhere PullBox isn't looking and post-processing can't relocate it.

**Scheduled jobs seem to do nothing.** Check `config/logs/pullbox.log`. APScheduler job failures and misfires are routed there at WARNING.

**Nothing gets found for an issue.** Confirm the indexer passes its **Test**, then check whether your indexer carries category `7030` (Books/Comics); PullBox falls back to `7000` (Books) when `7030` comes back empty.

---

## Data & Backups

Everything lives in `config/`:

- `config/pullbox.db` — SQLite database (series, issues, queue, indexers, arcs)
- `config/config.yaml` — your configuration
- `config/logs/` — application logs

Back up `config/` to preserve all data. The comics library itself (mounted at `/comics`) is not modified by PullBox unless post-processing is enabled.
