"""Search result schema, query builder, fan-out, and scoring logic."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pullbox.models import Indexer, Issue, Series

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    indexer_id: int
    indexer_name: str
    source_type: str
    title: str
    guid: str
    download_url: str
    size_bytes: int | None = None
    published_at: datetime | None = None
    seeders: int | None = None
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "indexer_id": self.indexer_id,
            "indexer_name": self.indexer_name,
            "source_type": self.source_type,
            "title": self.title,
            "guid": self.guid,
            "download_url": self.download_url,
            "size_bytes": self.size_bytes,
            "published_at": self.published_at,
            "seeders": self.seeders,
            "score": self.score,
        }


def build_search_queries(series_title: str, issue_number: str) -> list[str]:
    title = series_title.lower()
    issue_lower = issue_number.lower()
    base = f"{title} {issue_lower}"
    results: list[str] = [base]

    if issue_number.isdigit():
        pad2 = f"{title} {issue_number.zfill(2)}"
        if pad2 not in results:
            results.append(pad2)
        pad3 = f"{title} {issue_number.zfill(3)}"
        if pad3 not in results:
            results.append(pad3)


    return results


async def fan_out_search(
    issue: Issue,
    series: Series,
    indexers: list[Indexer],
) -> list[SearchResult]:
    from pullbox.clients.jackett import JackettClient
    from pullbox.clients.newznab import NewznabClient
    from pullbox.clients.prowlarr import ProwlarrClient

    enabled = sorted(
        (idx for idx in indexers if idx.enabled),
        key=lambda i: i.priority,
    )

    logger.info(
        "Searching for %r #%s (issue id=%d) across %d enabled indexer(s)",
        series.title,
        issue.issue_number,
        issue.id,
        len(enabled),
    )

    queries = build_search_queries(series.title, issue.issue_number)

    logger.info(
        "fan_out_search: queries=%r enabled_indexers=%d",
        queries,
        len(enabled),
    )

    tasks = []
    for idx in enabled:
        client: NewznabClient | ProwlarrClient | JackettClient | None
        if idx.type in ("newznab", "nzbhydra2"):
            client = NewznabClient(idx)
        elif idx.type == "prowlarr":
            client = ProwlarrClient(idx)
        elif idx.type == "jackett":
            client = JackettClient(idx)
        else:
            client = None

        if client is not None:
            logger.info(
                "fan_out_search: queuing %d queries on indexer %r (type=%s)",
                len(queries),
                idx.name,
                idx.type,
            )
            for query in queries:
                tasks.append(client.search(query))
        else:
            logger.info("fan_out_search: skipping indexer %r (type=%s not supported)", idx.name, idx.type)

    if not tasks:
        logger.warning("fan_out_search: no searchable indexers found — check indexer types and enabled flags")
        return []

    all_results = await asyncio.gather(*tasks, return_exceptions=False)
    logger.info("fan_out_search: got %d total results across %d queries", sum(len(b) for b in all_results), len(tasks))

    seen_guids: set[str] = set()
    combined: list[SearchResult] = []
    for batch in all_results:
        for result in batch:
            if result.guid not in seen_guids:
                seen_guids.add(result.guid)
                combined.append(result)

    return combined


def score_results(
    results: list[SearchResult],
    series_title: str,
    issue_number: str,
) -> list[SearchResult]:
    title_lower = series_title.lower()
    now = datetime.now(tz=timezone.utc)

    for r in results:
        s = 0.0
        t = r.title.lower()

        if title_lower in t:
            s += 2.0
        if issue_number in t:
            s += 1.0
        if "cbz" in t or "cbr" in t:
            s += 1.0
        elif "pdf" in t:
            s += 0.5

        if r.published_at is not None:
            pub = r.published_at
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age_days = (now - pub).days
            if age_days <= 30:
                s += 0.5
            elif age_days <= 90:
                s += 0.25

        if r.source_type == "usenet":
            s += 0.5

        r.score = s

    return sorted(results, key=lambda r: r.score, reverse=True)
