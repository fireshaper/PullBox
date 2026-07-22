"""qBittorrent download client — WebUI API.

Currently implements only connection testing. qBittorrent is torrent-based and
is not yet wired into the (NZB-oriented) download-dispatch flow, so this class
intentionally does not extend BaseDownloadClient.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class QBittorrentClient:
    """qBittorrent WebUI API client.

    Authenticates against ``/api/v2/auth/login`` (which returns the literal
    body ``Ok.`` on success and sets an ``SID`` session cookie), then confirms
    the session is usable by fetching the application version.
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
        self._base_url = f"http://{host}:{port}"
        self._username = username
        self._password = password
        self._transport = transport

    async def test_connection(self) -> bool:
        """Return True if the WebUI is reachable and the credentials authenticate."""
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**kwargs) as http:
                # qBittorrent's WebUI checks Referer/Origin for CSRF; send Referer.
                login = await http.post(
                    f"{self._base_url}/api/v2/auth/login",
                    data={"username": self._username, "password": self._password},
                    headers={"Referer": self._base_url},
                )
                if login.status_code != 200 or login.text.strip() != "Ok.":
                    return False

                # Confirm the authenticated session actually works.
                version = await http.get(
                    f"{self._base_url}/api/v2/app/version",
                    cookies=login.cookies,
                )
                return version.status_code == 200
        except Exception:
            return False
