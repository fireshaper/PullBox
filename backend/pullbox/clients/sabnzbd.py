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
        return nzo_ids[0]

    async def get_job_status(self, job_id: str) -> str:
        """Return normalized status by checking queue then history."""
        try:
            queue_resp = await self._call({"mode": "queue"})
            for slot in queue_resp.get("queue", {}).get("slots", []):
                if slot.get("nzo_id") == job_id:
                    return "downloading"

            history_resp = await self._call({"mode": "history"})
            for slot in history_resp.get("history", {}).get("slots", []):
                if slot.get("nzo_id") == job_id:
                    raw = slot.get("status", "")
                    return _HISTORY_STATUS_MAP.get(raw, "unknown")

        except Exception:
            logger.warning("SABnzbdClient.get_job_status failed for job %s", job_id, exc_info=True)

        return "unknown"

    async def test_connection(self) -> bool:
        """Call the version endpoint to confirm the server is reachable and authenticated."""
        try:
            result = await self._call({"mode": "version"})
            return bool(result.get("version"))
        except Exception:
            return False
