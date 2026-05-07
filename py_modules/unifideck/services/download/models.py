"""services/download/models.py — DownloadItem dataclass + error classifier.

Pure data model + classifier. Kept separate so the service
layer can evolve without touching the wire format (the
``to_dict`` / ``from_dict`` contract is what persistence and
the frontend rely on).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DownloadItem:
    """A single queued download request."""

    store: str
    game_id: str
    install_path: str
    title: str = ""
    # Store the progress state emitted by store implementations
    progress: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"  # queued | running | complete | failed
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        Shape matches what ``persistence.save_queue`` writes and
        what the frontend consumes — changing a field name here
        is a wire-format break.
        """
        return {
            "store": self.store,
            "game_id": self.game_id,
            "install_path": self.install_path,
            "title": self.title,
            "progress": self.progress,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DownloadItem:
        """Parse from dict, silently dropping unknown keys.

        Forward-compatible: older persisted queues that lack
        newer fields fall back to dataclass defaults.
        """
        return cls(
            store=d.get("store", ""),
            game_id=d.get("game_id", ""),
            install_path=d.get("install_path", ""),
            title=d.get("title", ""),
            progress=d.get("progress", {}),
            status=d.get("status", "queued"),
            error=d.get("error", ""),
        )


def classify_download_error(exc: Exception | str) -> str:
    """Map an exception message to a stable error code string.

    Pure function, unit-testable. Substring matches on lowered
    ``str(exc)`` in order: ``permission_denied`` / ``disk_full``
    / ``timeout`` / ``network_error`` / ``not_found``; anything
    else → ``unknown_error``. Frontend uses these codes to
    render user-friendly error messages.
    """
    msg = str(exc).lower()

    if "permission denied" in msg or "eacces" in msg:
        return "permission_denied"
    if "no space left" in msg or "enospc" in msg or "disk full" in msg:
        return "disk_full"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg or "resolve" in msg or "socket" in msg:
        return "network_error"
    if "not found" in msg or "404" in msg:
        return "not_found"

    return "unknown_error"
