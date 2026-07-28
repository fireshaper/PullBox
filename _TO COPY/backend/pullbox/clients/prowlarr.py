"""Prowlarr search client — queries Prowlarr's unified JSON search API.

Prowlarr aggregates many torrent and Usenet indexers behind a single endpoint.
We use its v1 search API (``/api/v1/search``) which returns a JSON array of
releases spanning both protocols, authenticated with an ``X-Api-Key`` header.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from pullbox.models import Indexer
from pullbox.search import SearchResult

logger = logging.getLogger(__name__)

# Comics (7030) first, then the broader Books (7000) bucket as a fallback.
_CATEGORIES = ("7030", "7000")


class ProwlarrClient:
    def __init__(self, indexer: Indexer) -> None:
        self._indexer = indexer

    async def search(self, query: str) -> list[SearchResult]:
        results = await self._search_cat(query, _CATEGORIES[0])
        if not results:
            results = await self._search_cat(query, _CATEGORIES[1])
        return results

    async def _search_cat(self, query: str, cat: str) -> list[SearchResult]:
        url = f"{self._indexer.url.rstrip('/')}/api/v1/search"
        params: dict[str, str] = {"query": query, "categories": cat, "type": "search"}
        headers: dict[str, str] = {}
        if self._indexer.api_key:
            headers["X-Api-Key"] = self._indexer.api_key

        try:
            logger.info("Prowlarr search: %s %s params=%s", self._indexer.name, url, params)
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params, headers=headers)
        except Exception as exc:
            logger.warning("Prowlarr request failed for %s: %s", self._indexer.name, exc)
            return []

        if resp.status_code != 200:
            logger.warning(
                "Prowlarr %s returned HTTP %s: %s",
                self._indexer.name,
                resp.status_code,
                resp.text[:200],
            )
            return []

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("Prowlarr JSON parse error for %s: %s", self._indexer.name, exc)
            return []

        return self._parse_releases(payload)

    def _parse_releases(self, payload: object) -> list[SearchResult]:
        if not isinstance(payload, list):
            return []

        results: list[SearchResult] = []
        for release in payload:
            if not isinstance(release, dict):
                continue

            title = str(release.get("title") or "").strip()
            # Prefer a direct download URL; fall back to a magnet link for torrents.
            download_url = release.get("downloadUrl") or release.get("magnetUrl") or ""
            guid = str(release.get("guid") or download_url or "").strip()

            if not title or not download_url or not guid:
                continue

            protocol = str(release.get("protocol") or "").lower()
            source_type = "usenet" if protocol == "usenet" else "torrent"

            size_bytes: int | None = None
            raw_size = release.get("size")
            if isinstance(raw_size, int):
                size_bytes = raw_size
            elif isinstance(raw_size, str) and raw_size.isdigit():
                size_bytes = int(raw_size)

            seeders: int | None = None
            raw_seeders = release.get("seeders")
            if isinstance(raw_seeders, int):
                seeders = raw_seeders

            published_at: datetime | None = None
            raw_pub = release.get("publishDate")
            if isinstance(raw_pub, str) and raw_pub:
                try:
                    # Prowlarr emits ISO-8601, often with a trailing 'Z'.
                    published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
                except ValueError:
                    pass

            results.append(
                SearchResult(
                    indexer_id=self._indexer.id,
                    indexer_name=self._indexer.name,
                    source_type=source_type,
                    title=title,
                    guid=guid,
                    download_url=str(download_url),
                    size_bytes=size_bytes,
                    published_at=published_at,
                    seeders=seeders,
                )
            )

        return results
