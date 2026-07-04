"""core/types/results.py — Result dataclasses and StoreError exceptions.
The split keeps `events.py` free of runtime imports (no dataclass
module needed) and lets future result additions touch only this
file without rebuilding the enums.
Design: every result subclass adds its own fields but inherits
`success: bool`, `error: Optional[str]`, and `store: Optional[str]`
from `Result`. This lets generic code (logging, telemetry) treat
all results uniformly via isinstance checks on `Result`.
Reference: Technical Document v1.0 — Section 3.4.5 (Result types).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    """Base dataclass for every store operation return value.
    Fields:
      success: True if the operation completed without error.
        Callers should NEVER parse `error` to decide success —
        always check this flag first.
      error: Optional error string. Human-readable message, free-
        form. Populated on failure; None on success. Must NOT be
        parsed by callers for control-flow decisions — use
        ``error_code`` for that. The string is intended for logs
        and user-visible messages only.
      error_code: Optional machine-readable error identifier.
        When populated, it's the authoritative classification of
        the failure (a ``LauncherErrorCode`` enum value or a stable
        string like "exit_<rc>" for subprocess exit propagation).
        The dispatcher's exit code mapping dispatches on this
        field exclusively — no more fragile ``"not_implemented"
        in err_str`` string-matching. None on success or for
        results that don't need machine classification (historical
        store results that pre-date this field).
      store: Optional store ID that produced the result, for
        logging and frontend routing. None for global operations.
      metadata: Free-form store-specific payload for fields that
        don't belong on the canonical `Result` surface. Mirrors
        `Game.metadata`: keeps the base class slim while letting
        individual stores carry their own signaling flags (e.g.
        ``pending``, ``needs_2fa``, ``auth_type``). Frontend should
        treat these as optional hints, never as contract.
    """

    success: bool = True
    error: str | None = None
    error_code: str | None = None
    store: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthResult(Result):
    """Returned by `store.auth_action('start'|'complete'|'status')`."""

    action: str | None = None
    next_step: str | None = None
    url: str | None = None
    tokens_cached: bool = False


@dataclass
class InstallResult(Result):
    """Returned by `store.install_game()` and `uninstall_game()`."""

    game_id: str | None = None
    install_path: str | None = None
    size_bytes: int = 0


@dataclass
class SyncResult(Result):
    """Returned by `SyncService.sync()`."""

    games: list[Any] = field(default_factory=list)
    count: int = 0
    duration_ms: int = 0
    # True when a sync request was queued behind an in-flight sync
    # (per ``SyncService._enqueue`` merging). The frontend uses this
    # flag to auto-listen for the next SYNC_STARTED so the post-auth
    # refresh feels seamless without a polling loop.
    restart_pending: bool = False
    # Provenance of the request — propagated from ``SyncRequest.source``
    # ("manual" | "auth:<store>" | "background" | "scheduled"). Lets
    # callers and logs distinguish user-initiated syncs from
    # auto-triggered ones.
    source: str = "manual"


@dataclass
class DownloadResult(Result):
    """Returned by DownloadService operations."""

    download_id: str | None = None
    bytes_downloaded: int = 0
    bytes_total: int = 0
    progress_pct: float = 0.0


@dataclass
class PlaytimeResult(Result):
    """Returned by PlaytimeService queries."""

    game_id: str | None = None
    total_seconds: int = 0
    last_played: str | None = None  # ISO 8601
    session_count: int = 0


@dataclass
class MetadataResult(Result):
    """Returned by MetadataService.get()."""

    game_id: str | None = None
    title: str | None = None
    description: str | None = None
    release_date: str | None = None
    genres: list[str] = field(default_factory=list)
    metacritic_score: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtworkResult(Result):
    """Returned by ArtworkService.fetch()."""

    game_id: str | None = None
    hero_path: str | None = None
    logo_path: str | None = None
    grid_path: str | None = None
    icon_path: str | None = None
    source: str | None = None


@dataclass
class CloudSaveResult(Result):
    """Returned by CloudSaveService.upload()/download()."""

    game_id: str | None = None
    direction: str | None = None  # "upload" or "download"
    files_processed: int = 0
    bytes_transferred: int = 0


@dataclass
class AccountResult(Result):
    """Returned by AccountService.get_steam_user()."""

    steam_id64: str | None = None
    persona_name: str | None = None
    account_name: str | None = None
    most_recent: bool = False


# ── Exception hierarchy ─────────────────────────────────────────
class StoreError(Exception):
    """Base class for all store-originated errors.
    Store connectors should raise subclasses of StoreError rather
    than bare Exception so callers can catch `StoreError` to match
    any store failure without catching unrelated programmer errors.
    """

    def __init__(
        self,
        message: str,
        *,
        store: str | None = None,
        code: str | None = None,
    ) -> None:
        """Build a typed store error with optional store + machine code.

        The message goes to ``Exception.__init__`` so it
        renders as ``str(exc)``; ``store`` and ``code`` are
        kept as instance attributes for typed handlers
        (e.g. classifying ``code=="not_authenticated"`` to
        trigger a re-auth flow).

        Args:
            message: human-readable error description.
            store: optional store identifier producing the
                error (e.g. ``"epic"``).
            code: optional machine-readable classification
                (typically an ``ErrorCode`` value).
        """
        super().__init__(message)
        self.store = store
        self.code = code


class StoreAuthError(StoreError):
    """Raised on any auth-related failure."""


class StoreSyncError(StoreError):
    """Raised when library fetching fails unrecoverably."""


class StoreDownloadError(StoreError):
    """Raised when an install/uninstall/update fails."""
