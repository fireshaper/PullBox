"""Newznab search client — parses RSS XML from any Newznab-compatible indexer."""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime

import httpx
from lxml import etree

from pullbox.models import Indexer
from pullbox.search import SearchResult

logger = logging.getLogger(__name__)


class NewznabClient:
    def __init__(self, indexer: Indexer) -> None:
        self._indexer = indexer

    async def search(self, query: str) -> list[SearchResult]:
        results = await self._search_cat(query, "7030")
        if not results:
            results = await self._search_cat(query, "7000")
        return results

    async def _search_cat(self, query: str, cat: str) -> list[SearchResult]:
        url = f"{self._indexer.url.rstrip('/')}/api"
        params: dict[str, str] = {"t": "search", "q": query, "cat": cat}
        if self._indexer.api_key:
            params["apikey"] = self._indexer.api_key

        try:
            logger.info("Newznab search: %s %s params=%s", self._indexer.name, url, params)
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
        except Exception as exc:
            logger.warning("Newznab request failed for %s: %s", self._indexer.name, exc)
            return []

        if resp.status_code != 200:
            logger.warning(
                "Newznab %s returned HTTP %s: %s", self._indexer.name, resp.status_code, resp.text[:200]
            )
            return []

        try:
            root = etree.fromstring(resp.content)
        except etree.XMLSyntaxError as exc:
            logger.warning("Newznab XML parse error for %s: %s", self._indexer.name, exc)
            return []

        return self._parse_items(root)

    def _parse_items(self, root: etree._Element) -> list[SearchResult]:
        results: list[SearchResult] = []

        # Handle optional namespace on <rss> or <channel>
        ns = root.nsmap.get(None, "")
        ns_prefix = f"{{{ns}}}" if ns else ""

        channel = root.find(f"{ns_prefix}channel")
        if channel is None:
            # Some feeds put items directly under root
            items = root.findall(f"{ns_prefix}item")
        else:
            items = channel.findall(f"{ns_prefix}item")

        for item in items:
            guid_el = item.find("guid")
            title_el = item.find("title")
            enclosure_el = item.find("enclosure")
            pubdate_el = item.find("pubDate")

            if guid_el is None or title_el is None or enclosure_el is None:
                continue

            guid = (guid_el.text or "").strip()
            title = (title_el.text or "").strip()
            download_url = enclosure_el.get("url", "")
            size_str = enclosure_el.get("length", "")

            size_bytes: int | None = None
            if size_str and size_str.isdigit():
                size_bytes = int(size_str)

            published_at = None
            if pubdate_el is not None and pubdate_el.text:
                try:
                    published_at = parsedate_to_datetime(pubdate_el.text.strip())
                except Exception:
                    pass

            if not guid or not download_url:
                continue

            results.append(
                SearchResult(
                    indexer_id=self._indexer.id,
                    indexer_name=self._indexer.name,
                    source_type="usenet",
                    title=title,
                    guid=guid,
                    download_url=download_url,
                    size_bytes=size_bytes,
                    published_at=published_at,
                )
            )

        return results
