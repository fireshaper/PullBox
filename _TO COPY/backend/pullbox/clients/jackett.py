"""Jackett search client — parses Torznab XML from Jackett's aggregate indexer.

Jackett exposes each configured tracker as a Torznab feed, plus an ``all``
aggregate that searches every tracker at once. Torznab is Newznab-compatible
XML, but for torrents: the enclosure points at a ``.torrent``/magnet URL and
seeder counts arrive as ``<torznab:attr name="seeders" .../>`` elements.
"""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime

import httpx
from lxml import etree

from pullbox.models import Indexer
from pullbox.search import SearchResult

logger = logging.getLogger(__name__)

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
# Comics (7030) first, then the broader Books (7000) bucket as a fallback.
_CATEGORIES = ("7030", "7000")


def _aggregate_api_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/v2.0/indexers/all/results/torznab/api"


class JackettClient:
    def __init__(self, indexer: Indexer) -> None:
        self._indexer = indexer

    async def search(self, query: str) -> list[SearchResult]:
        results = await self._search_cat(query, _CATEGORIES[0])
        if not results:
            results = await self._search_cat(query, _CATEGORIES[1])
        return results

    async def _search_cat(self, query: str, cat: str) -> list[SearchResult]:
        url = _aggregate_api_url(self._indexer.url)
        params: dict[str, str] = {"t": "search", "q": query, "cat": cat}
        if self._indexer.api_key:
            params["apikey"] = self._indexer.api_key

        try:
            logger.info("Jackett search: %s %s params=%s", self._indexer.name, url, params)
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
        except Exception as exc:
            logger.warning("Jackett request failed for %s: %s", self._indexer.name, exc)
            return []

        if resp.status_code != 200:
            logger.warning(
                "Jackett %s returned HTTP %s: %s",
                self._indexer.name,
                resp.status_code,
                resp.text[:200],
            )
            return []

        try:
            root = etree.fromstring(resp.content)
        except etree.XMLSyntaxError as exc:
            logger.warning("Jackett XML parse error for %s: %s", self._indexer.name, exc)
            return []

        return self._parse_items(root)

    def _parse_items(self, root: etree._Element) -> list[SearchResult]:
        results: list[SearchResult] = []

        # Handle optional default namespace on <rss>/<channel>.
        ns = root.nsmap.get(None, "")
        ns_prefix = f"{{{ns}}}" if ns else ""

        channel = root.find(f"{ns_prefix}channel")
        items = (
            channel.findall(f"{ns_prefix}item")
            if channel is not None
            else root.findall(f"{ns_prefix}item")
        )

        for item in items:
            guid_el = item.find("guid")
            title_el = item.find("title")
            enclosure_el = item.find("enclosure")
            pubdate_el = item.find("pubDate")

            if title_el is None or enclosure_el is None:
                continue

            title = (title_el.text or "").strip()
            download_url = enclosure_el.get("url", "")
            # Torznab feeds sometimes omit <guid>; fall back to the download URL.
            guid = (guid_el.text or "").strip() if guid_el is not None else ""
            if not guid:
                guid = download_url

            if not title or not download_url or not guid:
                continue

            size_str = enclosure_el.get("length", "")
            size_bytes: int | None = int(size_str) if size_str.isdigit() else None

            published_at = None
            if pubdate_el is not None and pubdate_el.text:
                try:
                    published_at = parsedate_to_datetime(pubdate_el.text.strip())
                except Exception:
                    pass

            seeders = self._extract_seeders(item)

            results.append(
                SearchResult(
                    indexer_id=self._indexer.id,
                    indexer_name=self._indexer.name,
                    source_type="torrent",
                    title=title,
                    guid=guid,
                    download_url=download_url,
                    size_bytes=size_bytes,
                    published_at=published_at,
                    seeders=seeders,
                )
            )

        return results

    @staticmethod
    def _extract_seeders(item: etree._Element) -> int | None:
        for attr in item.findall(f"{{{_TORZNAB_NS}}}attr"):
            if attr.get("name") == "seeders":
                value = attr.get("value", "")
                if value.isdigit():
                    return int(value)
        return None
