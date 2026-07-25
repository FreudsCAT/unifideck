"""Detect the active Steam user's account ID (compatibility shim).

The active user is *not* ``"0"`` — that directory is Steam's guest /
meta dir and ignored by the running Steam client. Writing
``shortcuts.vdf`` or artwork into it makes those writes invisible to
Steam, which is the root cause of "I synced N games but Steam shows
nothing" symptoms.

Resolution now lives in ONE place — :mod:`unifideck.steam.current_user`
(``resolve``) — which ranks frontend/config → loginusers → registry
AutoLoginUser → localconfig-mtime, all identity-validated. This module
remains only as a thin, launcher-safe shim so existing callers keep
working; new code should call ``current_user.resolve`` directly.
"""

from __future__ import annotations

from pathlib import Path


def get_active_steam_user(steam_root: Path) -> str | None:
    """Return the active user's account ID (``userdata/`` folder name).

    Thin compatibility shim over :func:`unifideck.steam.current_user.resolve`
    — the single source of truth for active-user resolution. Kept so existing
    callers (and the launcher, which has no ConfigManager) get the hardened
    resolution without each re-implementing it. Passes ``config=None`` so the
    persisted frontend value is read config-free from disk.

    ``None`` when no real user can be confirmed — callers MUST treat this as
    "Steam isn't logged in / can't be confirmed" and defer any write that
    targets the user's ``shortcuts.vdf`` or ``grid/`` directories, never
    falling back to the guest ``"0"`` dir (Steam ignores writes there).
    """
    from unifideck.steam.current_user import resolve
    return resolve(steam_root, config=None)


__all__ = ["get_active_steam_user"]
