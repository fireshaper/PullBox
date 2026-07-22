"""Metron API client for series, issue, arc, and release metadata.

Metron (https://metron.cloud/) is a community-run comic database. Unlike ComicVine
it uses HTTP Basic auth (a registered account's username/password, not an API key),
Django-REST-Framework pagination (``count``/``next``/``previous``/``results``), and
different terminology: ComicVine "volume" is a Metron "series"; "story arc" is an
"arc". There are no ``4050-``/``4000-``/``4045-`` resource-type id prefixes.

This client mirrors ``ComicVineClient``'s method surface and normalized dict shapes
so both can sit behind the ``MetadataProvider`` abstraction, but every record now
carries both ``metron_id`` and ``comicvine_id`` (Metron exposes ComicVine ids as
``cv_id`` on its *detail* endpoints; list endpoints omit it).

Field-name note: mappings follow Metron's documented schema (mirrored by the
``mokkari`` wrapper). List endpoints are intentionally sparse — ``image``,
``publisher`` and ``cv_id`` appear only on detail endpoints — so ``search_series``
returns lightweight rows and ``get_volume`` fills the rest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://metron.cloud/api"


class MetronError(Exception):
    pass


class MetronRateLimitError(MetronError):
    """Raised when Metron throttles us (HTTP 429) or when the local rate limiter's
    per-minute/per-day budget or a cooldown is exhausted. Subclasses ``MetronError``
    so existing ``except MetronError`` handlers still catch it; callers that want to
    *pause* (e.g. the import backlog job) catch it specifically.
    """


class _RateLimiter:
    """Process-global limiter shared by every Metron caller (one account).

    Metron enforces two independent windows: a burst cap (~20 requests/minute) and a
    sustained cap (~5000 requests/day). This limiter tracks rolling counts for both
    and raises immediately when either is exhausted (rather than blocking for up to a
    day) so callers decide whether to stop or surface it. A cooldown started from a
    429 response (or a ``X-RateLimit-Burst-Remaining: 0`` header) also raises.
    """

    def __init__(self, per_min: int, per_day: int, cooldown: float = 60.0) -> None:
        self._per_min = per_min
        self._per_day = per_day
        self._cooldown = cooldown
        self._lock = asyncio.Lock()
        self._minute: deque[float] = deque()
        self._day: deque[float] = deque()
        self._cooldown_until = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                raise MetronRateLimitError("Metron rate-limit cooldown active")
            self._prune(now)
            if len(self._minute) >= self._per_min:
                raise MetronRateLimitError("Metron per-minute request budget exhausted")
            if len(self._day) >= self._per_day:
                raise MetronRateLimitError("Metron daily request budget exhausted")
            self._minute.append(now)
            self._day.append(now)

    def _prune(self, now: float) -> None:
        minute_cutoff = now - 60
        while self._minute and self._minute[0] < minute_cutoff:
            self._minute.popleft()
        day_cutoff = now - 86400
        while self._day and self._day[0] < day_cutoff:
            self._day.popleft()

    def note_throttle(self, retry_after: float | None = None) -> None:
        """Start a cooldown after Metron reported a throttle (HTTP 429)."""
        self._cooldown_until = time.monotonic() + (retry_after or self._cooldown)

    def note_headers(self, burst_remaining: int | None) -> None:
        """Pre-empt the next 429: if the burst window is spent, cool down briefly."""
        if burst_remaining is not None and burst_remaining <= 0:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + self._cooldown)


# Module-global limiter. ``None`` = throttling disabled (the default in tests and any
# context that never called ``configure_rate_limiter``). The app configures it from
# Settings during startup (see main.py lifespan).
_limiter: _RateLimiter | None = None


def configure_rate_limiter(per_min: int, per_day: int, cooldown: float = 60.0) -> None:
    global _limiter
    _limiter = _RateLimiter(per_min, per_day, cooldown)


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MetronClient:
    source = "metron"

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = _DEFAULT_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        client_kwargs: dict[str, Any] = {
            "headers": {"User-Agent": "pullbox/0.1"},
            "auth": httpx.BasicAuth(username, password),
            "timeout": 30.0,
            "follow_redirects": True,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_url(self, url: str, params: dict[str, Any] | None = None) -> dict:
        """GET an absolute URL (used to follow DRF ``next`` links and for base paths)."""
        if _limiter is not None:
            await _limiter.acquire()
        response = await self._client.get(url, params=params)
        if response.status_code == 429:
            retry_after = _to_int(response.headers.get("Retry-After"))
            if _limiter is not None:
                _limiter.note_throttle(retry_after)
            raise MetronRateLimitError("Metron HTTP 429: rate limited")
        if response.status_code != 200:
            raise MetronError(f"HTTP {response.status_code}: {response.text[:200]}")
        if _limiter is not None:
            _limiter.note_headers(_to_int(response.headers.get("X-RateLimit-Burst-Remaining")))
        return response.json()

    async def _get(self, path: str, **params: Any) -> dict:
        return await self._get_url(f"{self._base_url}{path}", params or None)

    async def _get_paginated(self, path: str, **params: Any) -> list[dict]:
        """Follow DRF ``next`` links, accumulating ``results`` across all pages."""
        data = await self._get(path, **params)
        results: list[dict] = list(data.get("results", []))
        next_url = data.get("next")
        while next_url:
            data = await self._get_url(next_url)
            results.extend(data.get("results", []))
            next_url = data.get("next")
        return results

    # -- field mappers -------------------------------------------------------

    @staticmethod
    def _publisher_name(item: dict) -> str | None:
        publisher = item.get("publisher")
        if isinstance(publisher, dict):
            return publisher.get("name")
        return None

    def _map_series(self, item: dict) -> dict:
        """Normalize a Metron series object (list *or* detail; missing keys → None)."""
        return {
            "metron_id": str(item["id"]),
            "comicvine_id": str(item["cv_id"]) if item.get("cv_id") else None,
            # List rows expose only a combined ``series`` display string; detail rows
            # have a clean ``name``. Prefer the clean name when present.
            "title": item.get("name") or item.get("series") or "",
            "publisher": self._publisher_name(item),
            "start_year": item.get("year_began"),
            "cover_url": item.get("image"),
            "description": item.get("desc"),
            "issue_count": item.get("issue_count", 0),
            "source": "metron",
        }

    @staticmethod
    def _issue_title(item: dict) -> str | None:
        # Detail rows split the title into ``collection_title`` + ``story_titles``;
        # list rows carry a pre-joined ``issue_name``.
        if item.get("collection_title"):
            return item["collection_title"]
        stories = item.get("story_titles")
        if isinstance(stories, list) and stories:
            return "; ".join(str(s) for s in stories)
        return item.get("issue_name") or item.get("name")

    def _map_issue(self, item: dict) -> dict:
        return {
            "metron_id": str(item["id"]),
            "comicvine_id": str(item["cv_id"]) if item.get("cv_id") else None,
            "issue_number": item.get("number", ""),
            "title": self._issue_title(item),
            "cover_date": item.get("cover_date"),
            "store_date": item.get("store_date"),
            "cover_url": item.get("image"),
            "description": item.get("desc"),
        }

    # -- public API ----------------------------------------------------------

    async def search_series(self, query: str, limit: int = 20) -> list[dict]:
        data = await self._get("/series/", name=query)
        return [self._map_series(item) for item in data.get("results", [])[:limit]]

    async def get_issues(
        self,
        metron_series_id: str,
        limit: int = 100,  # noqa: ARG002 — accepted for parity with ComicVineClient
        offset: int = 0,  # noqa: ARG002
    ) -> list[dict]:
        items = await self._get_paginated("/issue/", series_id=metron_series_id)
        return [self._map_issue(item) for item in items]

    async def get_volume(self, metron_id: str) -> dict:
        try:
            data = await self._get(f"/series/{metron_id}/")
        except MetronRateLimitError:
            raise
        except MetronError as exc:
            raise MetronError(f"Series {metron_id} not found: {exc}") from exc
        if not data:
            raise MetronError(f"Series {metron_id} not found")
        return self._map_series(data)

    async def get_issue(self, metron_id: str) -> dict:
        """Fetch a single issue's detail, including its arc memberships.

        Metron returns ``arcs`` inline on the issue *detail* endpoint (unlike
        ComicVine, which needs a separate call), so one request yields both the
        issue metadata and its arc list.
        """
        try:
            data = await self._get(f"/issue/{metron_id}/")
        except MetronRateLimitError:
            raise
        except MetronError as exc:
            raise MetronError(f"Issue {metron_id} not found: {exc}") from exc
        if not data:
            raise MetronError(f"Issue {metron_id} not found")

        arcs = []
        for arc in data.get("arcs") or []:
            arcs.append(
                {
                    "metron_id": str(arc["id"]),
                    "comicvine_id": str(arc["cv_id"]) if arc.get("cv_id") else None,
                    "name": arc.get("name", ""),
                }
            )
        return {
            "metron_id": str(data["id"]),
            "comicvine_id": str(data["cv_id"]) if data.get("cv_id") else None,
            "issue_number": data.get("number", ""),
            "title": self._issue_title(data),
            "story_arcs": arcs,
        }

    async def get_story_arc(self, metron_id: str) -> dict:
        """Fetch an arc's metadata plus its full cross-series issue list.

        Metron splits this across two endpoints: ``/arc/{id}/`` for metadata and
        ``/arc/{id}/issue_list/`` for the member issues. Members carry only the
        lightweight issue shape (id, number, name) — no ``cv_id``.
        """
        try:
            data = await self._get(f"/arc/{metron_id}/")
        except MetronRateLimitError:
            raise
        except MetronError as exc:
            raise MetronError(f"Arc {metron_id} not found: {exc}") from exc
        if not data:
            raise MetronError(f"Arc {metron_id} not found")

        members = await self._get_paginated(f"/arc/{metron_id}/issue_list/")
        issues = []
        for entry in members:
            issues.append(
                {
                    "metron_id": str(entry["id"]),
                    "comicvine_id": str(entry["cv_id"]) if entry.get("cv_id") else None,
                    "name": self._issue_title(entry),
                    "site_detail_url": entry.get("resource_url"),
                }
            )
        return {
            "metron_id": str(data["id"]),
            "comicvine_id": str(data["cv_id"]) if data.get("cv_id") else None,
            "name": data.get("name", ""),
            "publisher": self._publisher_name(data),
            "cover_url": data.get("image"),
            "description": data.get("desc"),
            "count_of_issue_appearances": len(issues),
            "issues": issues,
        }

    async def get_weekly_releases(
        self,
        store_date_start: str,
        store_date_end: str,
    ) -> list[dict]:
        items = await self._get_paginated(
            "/issue/",
            store_date_range_after=store_date_start,
            store_date_range_before=store_date_end,
        )
        results = []
        for item in items:
            series = item.get("series") or {}
            results.append(
                {
                    "metron_id": str(item["id"]),
                    "comicvine_id": str(item["cv_id"]) if item.get("cv_id") else None,
                    "issue_number": item.get("number", ""),
                    "title": self._issue_title(item),
                    "store_date": item.get("store_date"),
                    "cover_url": item.get("image"),
                    "series": {
                        "metron_id": str(series.get("id", "")),
                        "comicvine_id": str(series["cv_id"]) if series.get("cv_id") else None,
                        "title": series.get("name", ""),
                    },
                }
            )
        return results
