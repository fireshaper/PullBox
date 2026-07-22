"""Provider-agnostic metadata layer.

Consumers depend on a ``MetadataProvider`` rather than importing ``ComicVineClient``
or ``MetronClient`` directly. ``CompositeProvider`` runs one source as primary and the
other as a fallback:

* **Query/date-based** operations (``search_series``, ``get_weekly_releases``) fall
  back to the secondary source on error — and, for search, on an empty result.
* **Id-based** operations (``get_volume``, ``get_issues``, ``get_issue``,
  ``get_story_arc``) cannot blindly fall back — a Metron id is meaningless to
  ComicVine and vice-versa. They *route* to whichever source the record actually has
  an id for, preferring the primary. If that source errors and the other id is also
  present, the other source is tried.

Every record returned carries both ``metron_id`` and ``comicvine_id`` keys (either may
be ``None``) plus a ``source`` tag, so callers can persist and match on either id.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from pullbox.clients.comicvine import (
    ComicVineClient,
    ComicVineError,
    ComicVineRateLimitError,
)
from pullbox.clients.metron import MetronClient, MetronError, MetronRateLimitError

logger = logging.getLogger(__name__)

# Any error from either underlying client. Both rate-limit errors subclass these.
PROVIDER_ERRORS = (MetronError, ComicVineError)
_PROVIDER_ERRORS = PROVIDER_ERRORS
# The rate-limit subset — callers that want to *pause* (e.g. the import backfill job)
# catch these specifically to stop until the next scheduled run.
RATE_LIMIT_ERRORS = (MetronRateLimitError, ComicVineRateLimitError)


class MetadataProvider(ABC):
    """Interface consumers depend on. Id-based methods take both ids and route."""

    @abstractmethod
    async def search_series(self, query: str, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    async def get_weekly_releases(self, start: str, end: str) -> list[dict]: ...

    @abstractmethod
    async def get_volume(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict: ...

    @abstractmethod
    async def get_issues(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> list[dict]: ...

    @abstractmethod
    async def get_issue(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict: ...

    @abstractmethod
    async def get_story_arc(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...


def ids_for(obj: Any) -> dict[str, str | None]:
    """Return ``{"metron_id": ..., "comicvine_id": ...}`` kwargs for id-based provider
    calls, extracted from an ORM row or a normalized record dict.
    """
    if isinstance(obj, dict):
        return {"metron_id": obj.get("metron_id"), "comicvine_id": obj.get("comicvine_id")}
    return {
        "metron_id": getattr(obj, "metron_id", None),
        "comicvine_id": getattr(obj, "comicvine_id", None),
    }


def _cv_series(record: dict) -> dict:
    """Normalize a ComicVine series/volume record to the dual-id shape."""
    return {**record, "metron_id": None, "source": "comicvine"}


def _cv_issue(record: dict) -> dict:
    return {**record, "metron_id": None}


class ComicVineProvider:
    """Adapts ``ComicVineClient`` (single ``comicvine_id`` args, ComicVine-only dicts)
    to the dual-id record shape. Not a full ``MetadataProvider`` — it is only ever
    driven by ``CompositeProvider`` with a known ``comicvine_id``.
    """

    source = "comicvine"

    def __init__(self, client: ComicVineClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    async def search_series(self, query: str, limit: int = 20) -> list[dict]:
        return [_cv_series(r) for r in await self._client.search_series(query, limit)]

    async def get_weekly_releases(self, start: str, end: str) -> list[dict]:
        results = await self._client.get_weekly_releases(start, end)
        out = []
        for r in results:
            series = {**r.get("series", {}), "metron_id": None}
            out.append({**r, "metron_id": None, "series": series})
        return out

    async def get_volume(self, comicvine_id: str) -> dict:
        return _cv_series(await self._client.get_volume(comicvine_id))

    async def get_issues(self, comicvine_id: str) -> list[dict]:
        return [_cv_issue(r) for r in await self._client.get_issues(comicvine_id)]

    async def get_issue(self, comicvine_id: str) -> dict:
        record = await self._client.get_issue(comicvine_id)
        arcs = [{**a, "metron_id": None} for a in record.get("story_arcs", [])]
        return {**record, "metron_id": None, "story_arcs": arcs}

    async def get_story_arc(self, comicvine_id: str) -> dict:
        record = await self._client.get_story_arc(comicvine_id)
        issues = [{**i, "metron_id": None} for i in record.get("issues", [])]
        return {**record, "metron_id": None, "issues": issues}


class CompositeProvider(MetadataProvider):
    """Primary + optional fallback over Metron and ComicVine sources."""

    def __init__(
        self,
        *,
        metron: MetronClient | None = None,
        comicvine: ComicVineProvider | None = None,
        primary: str = "metron",
    ) -> None:
        if metron is None and comicvine is None:
            raise ValueError("CompositeProvider needs at least one source")
        self._metron = metron
        self._comicvine = comicvine
        self._primary = primary

    async def close(self) -> None:
        if self._metron is not None:
            await self._metron.close()
        if self._comicvine is not None:
            await self._comicvine.close()

    # -- ordering helpers ----------------------------------------------------

    def _ordered_sources(self) -> list[Any]:
        """Primary source first, then the other — skipping any that aren't configured."""
        order = (
            [self._metron, self._comicvine]
            if self._primary == "metron"
            else [self._comicvine, self._metron]
        )
        return [s for s in order if s is not None]

    # -- query/date-based (fall back on error; search also on empty) ---------

    async def search_series(self, query: str, limit: int = 20) -> list[dict]:
        last_exc: Exception | None = None
        for source in self._ordered_sources():
            try:
                results = await source.search_series(query, limit)
            except _PROVIDER_ERRORS as exc:
                last_exc = exc
                logger.warning("search_series via %s failed: %s", source.source, exc)
                continue
            if results:
                return results
        if last_exc is not None:
            raise last_exc
        return []

    async def get_weekly_releases(self, start: str, end: str) -> list[dict]:
        last_exc: Exception | None = None
        for source in self._ordered_sources():
            try:
                return await source.get_weekly_releases(start, end)
            except _PROVIDER_ERRORS as exc:
                last_exc = exc
                logger.warning("get_weekly_releases via %s failed: %s", source.source, exc)
        assert last_exc is not None  # _ordered_sources is never empty
        raise last_exc

    # -- id-based (route by which id the record has, prefer primary) ---------

    def _id_candidates(
        self, metron_id: str | None, comicvine_id: str | None
    ) -> list[tuple[Any, str]]:
        """(source, id) pairs to try in order — primary source's id first."""
        pairs: list[tuple[Any, str]] = []
        if self._metron is not None and metron_id:
            pairs.append((self._metron, metron_id))
        if self._comicvine is not None and comicvine_id:
            pairs.append((self._comicvine, comicvine_id))
        if self._primary != "metron":
            pairs.reverse()
        return pairs

    async def _route(
        self, method: str, metron_id: str | None, comicvine_id: str | None
    ) -> Any:
        candidates = self._id_candidates(metron_id, comicvine_id)
        if not candidates:
            raise ValueError(f"{method}: no usable id for the configured source(s)")
        last_exc: Exception | None = None
        for source, ident in candidates:
            try:
                return await getattr(source, method)(ident)
            except _PROVIDER_ERRORS as exc:
                last_exc = exc
                logger.warning("%s via %s failed: %s", method, source.source, exc)
        assert last_exc is not None
        raise last_exc

    async def get_volume(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict:
        return await self._route("get_volume", metron_id, comicvine_id)

    async def get_issues(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> list[dict]:
        return await self._route("get_issues", metron_id, comicvine_id)

    async def get_issue(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict:
        return await self._route("get_issue", metron_id, comicvine_id)

    async def get_story_arc(
        self, *, metron_id: str | None = None, comicvine_id: str | None = None
    ) -> dict:
        return await self._route("get_story_arc", metron_id, comicvine_id)
