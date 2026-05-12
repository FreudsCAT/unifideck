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
    """Create (or repair) the auth shortcut for a given store.

    Auth shortcuts exist so that the user can launch the store's
    native client inside Unifideck's dedicated auth-only Wine
    prefix, complete the sign-in flow, and have Unifideck pick up
    the resulting credential file from the prefix.

    Workflow:

    1. Validate ``launcher_path`` and ``title`` are non-empty.
    2. Compute the deterministic AppID + its unsigned twin
       (Steam's UI sometimes shows the unsigned form).
    3. Build the canonical ``LaunchOptions`` string carrying the
       ``UNIFIDECK_<STORE>_ACTION=auth`` marker that the RPC
       layer parses on shortcut launches.
    4. Prune any malformed duplicates from prior plugin versions
       (``_prune_malformed_duplicates``).
    5. If no correct entry exists yet, append a fresh one.
    6. Save + emit ``SHORTCUT_CREATED`` (with ``is_auth=True``)
       so the artwork service can fetch launcher artwork.

    The unsigned id is returned in ``Result.error`` (despite the
    name) so the caller can navigate Steam to the new shortcut —
    abusing the field but keeping the ``Result`` shape consistent.

    Args:
        service: the host ``ShortcutService`` instance.
        store: store identifier.
        launcher_path: absolute path to the launcher executable.
        title: display title (e.g. ``"Ubisoft Connect"``).

    Returns:
        ``Result(success=True, error=<unsigned_id_string>)`` on
        success; ``Result(success=False, error="no_launcher_path")``
        or ``"no_title"`` on validation failure.
    """
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
    """Remove any old/broken auth shortcuts for ``store``.

    Auth shortcuts have evolved over Unifideck versions — older
    plugins produced entries that don't match the current
    schema. This pass scans every shortcut whose
    ``LaunchOptions`` contains the ``<store>:<store>-auth``
    marker and:

    * remembers the index of the one matching the current
      (app_id, title, schema) — that one is correct;
    * removes the others (sorted by index DESC so the
      ``del service._shortcuts[idx]`` calls don't invalidate the
      indices we still need to process).

    Args:
        service: the host ``ShortcutService`` instance.
        store: store identifier.
        app_id: the canonical AppID for the current entry.
        title: the canonical title for the current entry.

    Returns:
        Tuple ``(correct_idx, dirty)`` where ``correct_idx`` is
        the index of the correct entry if one was found (so the
        caller knows whether to append or not), and ``dirty`` is
        True if any pruning happened (so the caller knows
        whether to save).
    """
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
    """Construct the dict for a single auth-shortcut entry.

    Differs from a regular game shortcut by:

    * ``IsHidden=1`` — auth shortcuts shouldn't clutter the
      user's main library view (they're only used once);
    * ``AllowDesktopConfig=1`` — user may need a keyboard in the
      sign-in form, so allow desktop controller config.

    Args:
        app_id: pre-computed AppID.
        title: display title.
        launcher_path: launcher executable path.
        launch_options: pre-built launch options string with the
            ``UNIFIDECK_*_ACTION=auth`` marker.
        store: store identifier (used for the tag).

    Returns:
        Auth-shortcut entry dict.
    """
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
