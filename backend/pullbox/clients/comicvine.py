"""ComicVine API client for series, issue, and release metadata."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://comicvine.gamespot.com/api"


class ComicVineError(Exception):
    pass


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
        response = await self._client.get(f"{self._base_url}{path}", params=params)
        if response.status_code != 200:
            raise ComicVineError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if data.get("status_code") != 1:
            raise ComicVineError(
                f"ComicVine error {data.get('status_code')}: {data.get('error', 'Unknown')}"
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
