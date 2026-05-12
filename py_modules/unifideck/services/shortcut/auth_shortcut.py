"""Auth-shortcut builder — store-specific shortcuts for the auth flow.

OP-14d | py_modules/unifideck/services/shortcut/auth_shortcut.py

Some stores (Ubisoft, Epic) require the user to sign in through the
store's native client. We can't run those clients headlessly, so we
create a **dedicated Steam shortcut** that launches the client inside
its auth-only Wine prefix; the user signs in once and we propagate
credentials from there.

``build_auth_shortcut`` constructs the VDF entry for such a shortcut.
``_prune_malformed_duplicates`` cleans up stale entries from previous
versions where the schema was different.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from ...core.types import Events, Result
from .games_map import generate_app_id

if TYPE_CHECKING:
    from .service import ShortcutService
logger = logging.getLogger(__name__)
_UNIFIDECK_TAG = "Unifideck"


async def build_auth_shortcut(
    service: ShortcutService,
    store: str,
    launcher_path: str,
    title: str,
) -> Result:
    """Build auth shortcut."""
    if not launcher_path:
        return Result(success=False, error="no_launcher_path")
    if not title:
        return Result(success=False, error="no_title")
    app_id = generate_app_id(launcher_path, title)
    unsigned_id = app_id if app_id >= 0 else app_id + 2**32
    launch_options = f"{store}:{store}-auth UNIFIDECK_{store.upper()}_ACTION=auth"
    await service._load_shortcuts()
    correct_idx, dirty = _prune_malformed_duplicates(
        service,
        store,
        app_id,
        title,
    )
    if correct_idx is None:
        entry = _build_auth_entry(
            app_id,
            title,
            launcher_path,
            launch_options,
            store,
        )
        service._shortcuts.append(entry)
        dirty = True
        logger.info(
            "[ShortcutService] created auth shortcut for %s (app_id=%d, unsigned=%d)",
            store,
            app_id,
            unsigned_id,
        )
    if dirty:
        await service._save_all()
    await service._bus.emit(
        Events.SHORTCUT_CREATED,
        store=store,
        app_id=app_id,
        unsigned_id=unsigned_id,
        title=title,
        is_auth=True,
    )
    return Result(success=True, error=str(unsigned_id))


def _prune_malformed_duplicates(
    service: ShortcutService,
    store: str,
    app_id: int,
    title: str,
) -> tuple[int | None, bool]:
    """Prune malformed duplicates."""
    auth_prefix = f"{store}:{store}-auth"
    matching = [
        (i, s)
        for i, s in enumerate(service._shortcuts)
        if auth_prefix in s.get("LaunchOptions", "")
    ]
    correct_idx: int | None = None
    for i, entry in matching:
        if (
            entry.get("appid") == app_id
            and entry.get("AppName") == title
            and "UNIFIDECK_" in entry.get("LaunchOptions", "")
        ):
            correct_idx = i
            break
    dirty = False
    if matching:
        malformed_indices = sorted(
            [i for i, _ in matching if i != correct_idx],
            reverse=True,
        )
        for idx in malformed_indices:
            logger.warning(
                "[ShortcutService] removing malformed auth shortcut for %s at idx=%d",
                store,
                idx,
            )
            del service._shortcuts[idx]
            dirty = True
    return correct_idx, dirty


def _build_auth_entry(
    app_id: int,
    title: str,
    launcher_path: str,
    launch_options: str,
    store: str,
) -> dict:
    """Build auth entry."""
    return {
        "appid": app_id,
        "AppName": title,
        "Exe": f'"{launcher_path}"',
        "StartDir": '"' + str(Path(launcher_path).parent) + '"',
        "LaunchOptions": launch_options,
        "IsHidden": 1,
        "AllowDesktopConfig": 1,
        "OpenVR": 0,
        "icon": "",
        "tags": {
            "0": _UNIFIDECK_TAG,
            "1": store.capitalize(),
        },
    }
