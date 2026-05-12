"""Download data models — the per-item record + error classification.

OP-15c | py_modules/unifideck/services/download/models.py

``DownloadItem`` is the frozen dataclass describing one queued
download : store, game_id, target path, language, current state
(queued / running / paused / done / failed), progress (bytes done
+ total, ETA), failure code if any.

``classify_download_error`` is the helper that turns an
``InstallResult.error`` string from a store-side installer into a
typed enum value the UI can render.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class DownloadItem:
    """Download item."""

    store: str
    game_id: str
    install_path: str
    title: str = ""
    progress: float = 0.0
    status: str = "queued"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """To dict."""
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
        """From dict."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def classify_download_error(exc: Exception) -> str:
    """Classify download error."""
    msg = str(exc).lower()
    if "permission" in msg or "denied" in msg:
        return "permission_denied"
    if "no space" in msg or "disk full" in msg:
        return "disk_full"
    if "timeout" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg:
        return "network_error"
    if "not found" in msg or "404" in msg:
        return "not_found"
    return "unknown_error"
