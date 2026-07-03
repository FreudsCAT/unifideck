"""Detect the active Steam user's account ID.

The active user is *not* ``"0"`` — that directory is Steam's guest /
meta dir and ignored by the running Steam client. Writing
``shortcuts.vdf`` or artwork into it makes those writes invisible
to Steam, which is the root cause of "I synced 553 games but Steam
shows nothing" symptoms in older Unifideck builds.

Two-tier detection (ported from
``staging:py_modules/unifideck/steam/steam_utils.py``):

1. **Primary**: parse ``<steam>/config/loginusers.vdf``, find the
   user with ``MostRecent = "1"``, convert their SteamID64 to the
   32-bit account ID Steam uses as the ``userdata/`` folder name.
2. **Fallback**: pick the most-recently-touched ``userdata/<digit>``
   directory — explicitly skipping ``"0"``.

Both layers reject ``"0"``; if the user genuinely has no logged-in
account (fresh deck) this returns ``None`` and callers should
defer Layer-5 wiring until Steam logs in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Steam's special / non-real userdata directories.
_RESERVED_USERDATA_DIRS = frozenset({"0", "anonymous", "ac"})


def get_active_steam_user(steam_root: Path) -> str | None:
    """Return the active user's account ID (``userdata/`` folder name).

    ``None`` when no real user can be detected — caller should treat
    this as "Steam isn't logged in" and defer any write that targets
    the user's ``shortcuts.vdf`` or ``grid/`` directories.
    """
    user = _from_loginusers(steam_root)
    if user is not None:
        logger.info("[SteamUser] active user from loginusers.vdf: %s", user)
        return user
    user = _from_mtime(steam_root)
    if user is not None:
        logger.info("[SteamUser] active user from mtime fallback: %s", user)
        return user
    logger.warning(
        "[SteamUser] could not detect active Steam user under %s; "
        "shortcuts.vdf and artwork writes will be deferred",
        steam_root,
    )
    return None


def _from_loginusers(steam_root: Path) -> str | None:
    """Parse ``config/loginusers.vdf`` and return the MostRecent account id."""
    loginusers = steam_root / "config" / "loginusers.vdf"
    if not loginusers.exists():
        return None
    try:
        import vdf

        with loginusers.open("r", encoding="utf-8", errors="ignore") as f:
            data = vdf.load(f)  # type: ignore[no-untyped-call]  # vendored vdf is untyped
    except Exception as e:
        logger.debug("[SteamUser] loginusers.vdf parse failed: %s", e)
        return None
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, dict):
        return None
    for steam64_id_str, user_info in users.items():
        if not isinstance(user_info, dict):
            continue
        if user_info.get("MostRecent") != "1":
            continue
        account_id = _account_id_from_steam64(steam64_id_str)
        if account_id is None:
            continue
        if account_id in _RESERVED_USERDATA_DIRS:
            continue
        user_dir = steam_root / "userdata" / account_id
        if user_dir.is_dir():
            return account_id
    return None


def _from_mtime(steam_root: Path) -> str | None:
    """Pick the most-recently-touched real user directory."""
    userdata = steam_root / "userdata"
    if not userdata.is_dir():
        return None
    best: tuple[float, str] | None = None
    for entry in userdata.iterdir():
        name = entry.name
        if name in _RESERVED_USERDATA_DIRS:
            continue
        if not name.isdigit():
            continue
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, name)
    return best[1] if best is not None else None


def _account_id_from_steam64(steam64_id_str: str) -> str | None:
    """Convert a SteamID64 string to the 32-bit ``userdata/`` folder name."""
    try:
        steam64_id = int(steam64_id_str)
    except (TypeError, ValueError):
        return None
    return str(steam64_id & 0xFFFFFFFF)


__all__ = ["get_active_steam_user"]
