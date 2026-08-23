"""NZBGet download client — JSON-RPC implementation."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from pullbox.clients.download_client import BaseDownloadClient

logger = logging.getLogger(__name__)

# Maps NZBGet status strings to the standard four-value set
_STATUS_MAP: dict[str, str] = {
    "DOWNLOADING": "downloading",
    "SUCCESS": "completed",
    "FAILURE": "failed",
    "DELETED": "failed",
}


class NZBGetClient(BaseDownloadClient):
    """NZBGet download client using its JSON-RPC API.

    NZBGet exposes a JSON-RPC endpoint at http://{user}:{pass}@{host}:{port}/jsonrpc.
    Active downloads appear in listgroups; completed/failed items move to history.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = f"http://{username}:{password}@{host}:{port}/jsonrpc"
        self._transport = transport

    async def _call(self, method: str, params: list[Any] | None = None) -> Any:
        """Execute one JSON-RPC call and return the result value."""
        payload = {"version": "1.1", "method": method, "params": params or []}
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as http:
            resp = await http.post(self._base_url, content=json.dumps(payload).encode())
            resp.raise_for_status()
            return resp.json()["result"]

    async def send_nzb(self, url: str, name: str, category: str) -> str:
        """Submit NZB URL via appendurl. Returns the NZBGet NZBID as a string.

        NZBGet fetches the NZB from the URL itself; we never download the file content here.
        appendurl params: [NZBName, Category, Priority, AddToTop, URL]
        """
        nzbid = await self._call("appendurl", [name, category, 0, False, url])
        return str(nzbid)

    async def get_job_status(self, job_id: str) -> str:
        """Return normalized status by checking listgroups then history."""
        nzbid = int(job_id)

        try:
            # Active queue: listgroups returns DOWNLOADING, PAUSED, QUEUED, etc.
            groups = await self._call("listgroups", [0])
            for group in groups:
                if group.get("NZBID") == nzbid:
                    raw = group.get("Status", "")
                    return _STATUS_MAP.get(raw, "downloading")

            # Completed/failed items move to history with SUCCESS, FAILURE, DELETED statuses.
            history = await self._call("history", [False])
            for item in history:
                if item.get("NZBID") == nzbid:
                    raw = item.get("Status", "")
                    return _STATUS_MAP.get(raw, "unknown")

            # Absent from both lists: NZBGet has no record of this job (history
            # purged, or removed by hand). Distinct from the error path below —
            # the caller retires a 'missing' job instead of waiting on it forever.
            logger.warning(
                "NZBGet job %s is in neither listgroups nor history — treating as missing",
                job_id,
            )
            return "missing"

        except Exception:
            logger.warning("NZBGetClient.get_job_status failed for job %s", job_id, exc_info=True)

        # The call failed, so we know nothing — keep waiting and retry next poll.
        return "unknown"

    async def get_completed_path(self, job_id: str) -> str | None:
        """Return the final DestDir of a completed download from history, or None.

        NZBGet history items expose ``DestDir`` (the final output directory).
        Older entries may only have ``FinalDir`` populated; fall back to it.
        """
        nzbid = int(job_id)

        try:
            history = await self._call("history", [False])
            for item in history:
                if item.get("NZBID") == nzbid:
                    return item.get("DestDir") or item.get("FinalDir") or None
        except Exception:
            logger.warning(
                "NZBGetClient.get_completed_path failed for job %s", job_id, exc_info=True
            )

        return None

    async def test_connection(self) -> bool:
        """Call the version method to confirm the server is reachable and authenticated."""
        try:
            result = await self._call("version")
            return bool(result)
        except Exception:
            return False
