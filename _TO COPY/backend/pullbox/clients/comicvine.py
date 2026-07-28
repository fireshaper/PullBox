"""ComicVine API client for series, issue, and release metadata."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://comicvine.gamespot.com/api"


class ComicVineError(Exception):
    pass


class ComicVineRateLimitError(ComicVineError):
    """Raised when ComicVine throttles us (HTTP 429 / status_code 107) or when the
    local rate limiter's hourly budget/cooldown is exhausted. Subclasses
    ``ComicVineError`` so existing ``except ComicVineError`` handlers still catch it;
    callers that want to *pause* (e.g. the import backlog job) catch it specifically.
    """


class _RateLimiter:
    """Process-global limiter shared by every ComicVine caller (one API key).

    Enforces a minimum interval between requests (velocity) and a rolling hourly
    cap (ComicVine allows ~200/hour per resource). When the budget is exhausted or
    a throttle response set a cooldown, ``acquire()`` raises immediately rather than
    blocking for up to an hour — callers decide whether to stop or surface it.
    """

    def __init__(self, min_interval: float, per_hour: int, cooldown: float = 60.0) -> None:
        self._min_interval = min_interval
        self._per_hour = per_hour
        self._cooldown = cooldown
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._times: deque[float] = deque()
        self._cooldown_until = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                raise ComicVineRateLimitError("ComicVine rate-limit cooldown active")
            cutoff = now - 3600
            while self._times and self._times[0] < cutoff:
                self._times.popleft()
            if len(self._times) >= self._per_hour:
                raise ComicVineRateLimitError("ComicVine hourly request budget exhausted")
            wait = self._last + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._last = now
            self._times.append(now)

    def note_throttle(self) -> None:
        """Start a cooldown after ComicVine reported a throttle (429 / 107)."""
        self._cooldown_until = time.monotonic() + self._cooldown


# Module-global limiter. ``None`` = throttling disabled (the default in tests and
# any context that never called ``configure_rate_limiter``). The app configures it
# from Settings during startup (see main.py lifespan).
_limiter: _RateLimiter | None = None


def configure_rate_limiter(min_interval: float, per_hour: int, cooldown: float = 60.0) -> None:
    global _limiter
    _limiter = _RateLimiter(min_interval, per_hour, cooldown)


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None


class ComicVineClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        client_kwargs: dict[str, Any] = {
            "headers": {"User-Agent": "pullbox/0.1"},
            "timeout": 30.0,
            "follow_redirects": True,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> dict:
        params["api_key"] = self._api_key
        params["format"] = "json"
        if _limiter is not None:
            await _limiter.acquire()
        response = await self._client.get(f"{self._base_url}{path}", params=params)
        if response.status_code == 429:
            if _limiter is not None:
                _limiter.note_throttle()
            raise ComicVineRateLimitError("ComicVine HTTP 429: rate limited")
        if response.status_code != 200:
            raise ComicVineError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        status = data.get("status_code")
        # 107 = "Rate Limit Exceeded" in ComicVine's JSON envelope.
        if status == 107:
            if _limiter is not None:
                _limiter.note_throttle()
            raise ComicVineRateLimitError("ComicVine rate limit exceeded (status_code 107)")
        if status != 1:
            raise ComicVineError(
                f"ComicVine error {status}: {data.get('error', 'Unknown')}"
            )
        return data

    async def search_series(self, query: str, limit: int = 20) -> list[dict]:
        data = await self._get("/search", resources="volume", query=query, limit=limit)
        results = []
        for item in data.get("results", []):
            publisher = item.get("publisher")
            results.append(
                {
                    "comicvine_id": str(item["id"]),
                    "title": item.get("name", ""),
                    "publisher": publisher.get("name") if publisher else None,
                    "start_year": item.get("start_year"),
                    "cover_url": (item.get("image") or {}).get("small_url"),
                    "description": item.get("description"),
                    "issue_count": item.get("count_of_issues", 0),
                }
            )
        return results

    def _map_issues(self, items: list) -> list[dict]:
        return [
            {
                "comicvine_id": str(item["id"]),
                "issue_number": item.get("issue_number", ""),
                "title": item.get("name"),
                "cover_date": item.get("cover_date"),
                "store_date": item.get("store_date"),
                "cover_url": (item.get("image") or {}).get("small_url"),
                "description": item.get("description"),
            }
            for item in items
        ]

    async def get_issues(
        self,
        comicvine_volume_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        fields = "id,issue_number,name,cover_date,store_date,image,description"
        data = await self._get(
            "/issues",
            filter=f"volume:{comicvine_volume_id}",
            field_list=fields,
            limit=limit,
            offset=offset,
        )
        total = data.get("number_of_total_results", 0)
        all_results = self._map_issues(data.get("results", []))

        while len(all_results) < total:
            data = await self._get(
                "/issues",
                filter=f"volume:{comicvine_volume_id}",
                field_list=fields,
                limit=limit,
                offset=offset + len(all_results),
            )
            page_results = data.get("results", [])
            if not page_results:
                break
            all_results.extend(self._map_issues(page_results))

        return all_results

    async def get_volume(self, comicvine_id: str) -> dict:
        # Strip the resource prefix if the caller passes it, then always add it back.
        # ComicVine volume lookups require the 4050- resource type prefix.
        clean_id = comicvine_id.removeprefix("4050-")
        fields = "id,name,publisher,start_year,count_of_issues,image,description,deck"
        try:
            data = await self._get(f"/volume/4050-{clean_id}", field_list=fields)
        except ComicVineRateLimitError:
            raise
        except ComicVineError as exc:
            raise ComicVineError(f"Volume {comicvine_id} not found: {exc}") from exc

        item = data.get("results") or {}
        if not item:
            raise ComicVineError(f"Volume {comicvine_id} not found")

        publisher = item.get("publisher")
        return {
            "comicvine_id": str(item["id"]),
            "title": item.get("name", ""),
            "publisher": publisher.get("name") if publisher else None,
            "start_year": item.get("start_year"),
            "cover_url": (item.get("image") or {}).get("small_url"),
            "description": item.get("description"),
            "issue_count": item.get("count_of_issues", 0),
        }

    async def get_issue(self, comicvine_id: str) -> dict:
        """Fetch a single issue's detail, including its story arc memberships.

        `story_arc_credits` is only returned by the single-issue detail endpoint,
        never by the /issues list endpoint — so this is the only way to learn
        which arcs an issue belongs to.

        ``volume`` and the cover/date fields are requested so an issue reached by
        id alone (a story-arc member PullBox doesn't own) carries enough to create
        the local Series and Issue rows without a second lookup.
        """
        clean_id = comicvine_id.removeprefix("4000-")
        fields = (
            "id,issue_number,name,story_arc_credits,volume,cover_date,store_date,image"
        )
        try:
            data = await self._get(f"/issue/4000-{clean_id}", field_list=fields)
        except ComicVineRateLimitError:
            raise
        except ComicVineError as exc:
            raise ComicVineError(f"Issue {comicvine_id} not found: {exc}") from exc

        item = data.get("results") or {}
        if not item:
            raise ComicVineError(f"Issue {comicvine_id} not found")

        arcs = []
        for credit in item.get("story_arc_credits") or []:
            arcs.append(
                {
                    "comicvine_id": str(credit["id"]),
                    "name": credit.get("name", ""),
                }
            )
        volume = item.get("volume") or {}
        return {
            "comicvine_id": str(item["id"]),
            "issue_number": item.get("issue_number", ""),
            "title": item.get("name"),
            "cover_date": item.get("cover_date"),
            "store_date": item.get("store_date"),
            "cover_url": (item.get("image") or {}).get("small_url"),
            # ComicVine's issue payload names only the volume — publisher and
            # start year need a /volume call, so they are left unset here.
            "series": (
                {
                    "comicvine_id": str(volume["id"]),
                    "title": volume.get("name") or "",
                    "publisher": None,
                    "start_year": None,
                }
                if volume.get("id")
                else None
            ),
            "story_arcs": arcs,
        }

    async def get_story_arc(self, comicvine_id: str) -> dict:
        """Fetch a story arc's metadata plus its full cross-series issue list.

        Each issue in the arc's ``issues`` list carries only ComicVine's default
        association fields (id, name, site_detail_url) — no issue number or volume.
        Callers match these ids against the local library for richer display.
        """
        clean_id = comicvine_id.removeprefix("4045-")
        fields = (
            "id,name,publisher,image,description,"
            "count_of_issue_appearances,issues"
        )
        try:
            data = await self._get(f"/story_arc/4045-{clean_id}", field_list=fields)
        except ComicVineRateLimitError:
            raise
        except ComicVineError as exc:
            raise ComicVineError(f"Story arc {comicvine_id} not found: {exc}") from exc

        item = data.get("results") or {}
        if not item:
            raise ComicVineError(f"Story arc {comicvine_id} not found")

        publisher = item.get("publisher")
        issues = []
        for entry in item.get("issues") or []:
            issues.append(
                {
                    "comicvine_id": str(entry["id"]),
                    "name": entry.get("name"),
                    "site_detail_url": entry.get("site_detail_url"),
                }
            )
        return {
            "comicvine_id": str(item["id"]),
            "name": item.get("name", ""),
            "publisher": publisher.get("name") if publisher else None,
            "cover_url": (item.get("image") or {}).get("small_url"),
            "description": item.get("description"),
            "count_of_issue_appearances": item.get("count_of_issue_appearances"),
            "issues": issues,
        }

    async def get_weekly_releases(
        self,
        store_date_start: str,
        store_date_end: str,
    ) -> list[dict]:
        data = await self._get(
            "/issues",
            filter=f"store_date:{store_date_start}|{store_date_end}",
            field_list="id,issue_number,name,store_date,image,volume",
        )
        results = []
        for item in data.get("results", []):
            volume = item.get("volume") or {}
            results.append(
                {
                    "comicvine_id": str(item["id"]),
                    "issue_number": item.get("issue_number", ""),
                    "title": item.get("name"),
                    "store_date": item.get("store_date"),
                    "cover_url": (item.get("image") or {}).get("small_url"),
                    "series": {
                        "comicvine_id": str(volume.get("id", "")),
                        "title": volume.get("name", ""),
                    },
                }
            )
        return results
