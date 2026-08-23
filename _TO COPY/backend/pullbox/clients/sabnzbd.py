"""SABnzbd download client — JSON API implementation."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pullbox.clients.download_client import BaseDownloadClient

logger = logging.getLogger(__name__)

_HISTORY_STATUS_MAP: dict[str, str] = {
    "Completed": "completed",
    "Failed": "failed",
}

# How many history entries to pull when the ``nzo_ids`` server-side filter comes
# back empty. SABnzbd's history defaults to only the 10 most recent slots, so an
# unfiltered lookup silently loses any job that finished more than a few
# downloads ago.
_HISTORY_SCAN_LIMIT = 500


class SABnzbdClient(BaseDownloadClient):
    """SABnzbd download client using its JSON API.

    All calls are GET requests to http://{host}:{port}/api with apikey and output=json.
    SABnzbd fetches the NZB from the URL itself — we never download file content here.
    """

    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = f"http://{host}:{port}/api"
        self._api_key = api_key
        self._transport = transport

    async def _call(self, params: dict[str, str]) -> Any:
        """Execute one API call and return the parsed JSON."""
        all_params: dict[str, str] = {"apikey": self._api_key, "output": "json", **params}
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as http:
            resp = await http.get(self._base_url, params=all_params)
            resp.raise_for_status()
            return resp.json()

    async def send_nzb(self, url: str, name: str, category: str) -> str:
        """Submit NZB URL via addurl. Returns the SABnzbd NZO ID as a string."""
        result = await self._call({
            "mode": "addurl",
            "name": url,
            "nzbname": name,
            "cat": category,
        })
        if not result.get("status"):
            raise RuntimeError(f"SABnzbd addurl failed: {result}")
        nzo_ids = result.get("nzo_ids", [])
        if not nzo_ids:
            raise RuntimeError(f"SABnzbd addurl returned no nzo_ids: {result}")
        logger.info("SABnzbd accepted NZB %r (cat=%s) → nzo_id=%s", name, category, nzo_ids[0])
        return nzo_ids[0]

    async def _history_slot(self, job_id: str) -> dict[str, Any] | None:
        """Return this job's history slot, or None if SABnzbd has no record of it.

        ``mode=history`` returns only the 10 most recent slots unless told
        otherwise, so it cannot be scanned unfiltered — a job that finished a
        few downloads ago simply is not in the response, which is
        indistinguishable from "never existed". Ask the server to filter by
        ``nzo_ids`` instead, and fall back to an explicitly-limited scan for
        SABnzbd builds old enough to ignore that parameter.
        """
        filtered = await self._call({"mode": "history", "nzo_ids": job_id})
        for slot in filtered.get("history", {}).get("slots", []):
            if slot.get("nzo_id") == job_id:
                return slot

        scanned = await self._call({"mode": "history", "limit": str(_HISTORY_SCAN_LIMIT)})
        for slot in scanned.get("history", {}).get("slots", []):
            if slot.get("nzo_id") == job_id:
                return slot

        return None

    async def get_job_status(self, job_id: str) -> str:
        """Return normalized status by checking queue then history.

        Returns ``missing`` when SABnzbd has no record of the job at all —
        history purged, or the job was removed by hand. That is deliberately
        distinct from an API error, which propagates: the caller must not
        confuse "SABnzbd says this is gone" with "SABnzbd did not answer".
        """
        queue_resp = await self._call({"mode": "queue"})
        for slot in queue_resp.get("queue", {}).get("slots", []):
            if slot.get("nzo_id") == job_id:
                return "downloading"

        slot = await self._history_slot(job_id)
        if slot is None:
            logger.warning(
                "SABnzbd job %s is in neither the queue nor history — treating as missing",
                job_id,
            )
            return "missing"

        raw = slot.get("status", "")
        mapped = _HISTORY_STATUS_MAP.get(raw)
        if mapped is not None:
            return mapped

        # Anything else SABnzbd reports in history (Extracting, Repairing,
        # Verifying, Moving, Running, Queued, …) is post-processing still in
        # flight. It is emphatically not a terminal state, so keep waiting.
        logger.info(
            "SABnzbd job %s in history with non-terminal status %r; still working", job_id, raw
        )
        return "downloading"

    async def get_completed_path(self, job_id: str) -> str | None:
        """Return the final storage path of a completed download from history, or None.

        SABnzbd history slots expose ``storage`` — the absolute path to the
        completed download (file or folder).
        """
        try:
            slot = await self._history_slot(job_id)
            if slot is None:
                logger.warning(
                    "SABnzbd get_completed_path: job %s not found in history", job_id
                )
                return None
            storage = slot.get("storage") or None
            logger.info("SABnzbd job %s completed path (storage)=%r", job_id, storage)
            return storage
        except Exception:
            logger.warning(
                "SABnzbdClient.get_completed_path failed for job %s", job_id, exc_info=True
            )

        return None

    async def test_connection(self) -> bool:
        """Call the version endpoint to confirm the server is reachable and authenticated."""
        try:
            result = await self._call({"mode": "version"})
            return bool(result.get("version"))
        except Exception:
            return False
