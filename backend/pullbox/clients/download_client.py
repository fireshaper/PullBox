"""Abstract base class for download client backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseDownloadClient(ABC):
    """Shared interface for all download client implementations.

    Implementations must never fetch the NZB file content themselves;
    they pass the URL directly to the download client for retrieval.
    """

    @abstractmethod
    async def send_nzb(self, url: str, name: str, category: str) -> str:
        """Submit an NZB URL to the download client. Returns the client-side job ID."""

    @abstractmethod
    async def get_job_status(self, job_id: str) -> str:
        """Return the current status: 'downloading', 'completed', 'failed', or 'unknown'."""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Return True if the client is reachable and authenticated."""
