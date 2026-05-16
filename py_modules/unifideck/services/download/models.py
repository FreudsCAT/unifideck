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
    """One queued or running download entry.

    Not actually frozen — the worker mutates ``progress``,
    ``status`` and ``error`` as the install proceeds. Persistence
    serialises through ``to_dict`` / ``from_dict`` so a stable
    on-disk format is preserved across schema changes (extra fields
    in the dict are ignored, missing fields fall back to dataclass
    defaults).

    Attributes:
        store: store identifier (``"epic"`` / ``"gog"`` / etc.).
        game_id: store-specific game id.
        install_path: target install directory on disk.
        title: human-readable game name (used by the UI).
        progress: percentage 0.0-100.0 (mutated by the worker).
        status: one of ``"queued"`` / ``"running"`` /
            ``"complete"`` / ``"failed"``.
        error: failure code (empty string when status isn't
            ``"failed"``).
    """

    store: str
    game_id: str
    install_path: str
    title: str = ""
    progress: float = 0.0
    status: str = "queued"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        Used by ``DownloadService.get_queue`` for the UI payload
        and by ``persistence.save_queue`` for on-disk persistence.

        Returns:
            Dict with every public field. No nested objects.
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
        """Reconstruct a ``DownloadItem`` from a dict.

        Extra keys in ``d`` (from forward-compatibility schemas)
        are silently ignored. Missing keys fall back to the
        dataclass defaults — a queue persisted on an older plugin
        version is still loadable on a newer version that added
        fields.

        Args:
            d: dict (typically from ``to_dict`` or a JSON load).

        Returns:
            A fresh ``DownloadItem`` populated from the dict.
        """
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def classify_download_error(exc: Exception) -> str:
    """Map an exception's message to a typed error code.

    Used by the worker when an install raises an unhandled
    exception (typically a subprocess error). Pattern-matches the
    exception's string for known substrings and falls back to
    ``"unknown_error"`` on no match.

    The classification is intentionally simple and English-only —
    finer-grained classification (e.g. distinguishing transient
    network errors from a permanent DNS failure) lives in the
    store-side installer which produces structured ``InstallResult``
    errors that bypass this fallback.

    Args:
        exc: the exception caught by the worker.

    Returns:
        One of ``"permission_denied"`` / ``"disk_full"`` /
        ``"timeout"`` / ``"network_error"`` / ``"not_found"`` /
        ``"unknown_error"``.
    """
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
