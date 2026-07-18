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
    async def get_completed_path(self, job_id: str) -> str | None:
        """Return the final output path of a completed download, or None.

        This is the on-disk location (usually a directory) the download client
        wrote the finished files to. Used by post-processing to locate the comic
        file. Must never raise — return None on any failure or if not found.
        """

    @abstractmethod
    async def test_connection(self) -> bool:
        """Return True if the client is reachable and authenticated."""
