"""rpc/mixins/cleanup_sweeps.py — blocking filesystem sweeps for cleanup.

Pure, thread-offloaded helpers extracted from ``sync_cleanup.py`` (which had
crossed the 550-LOC volumetry cap). Each ``sweep_*`` performs one blocking
filesystem pass and returns a deleted-count; :class:`CleanupRPCMixin` calls
them via ``asyncio.to_thread``. Keeping them free of mixin state — and out of
the async methods as un-nested module functions — keeps the mixin's
per-function cognitive complexity under the gate and makes each sweep
trivially testable in isolation.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from unifideck.core.safe_delete import safe_rmtree

logger = logging.getLogger(__name__)

# Persisted credentials read by each store's ``is_available`` + stray
# auth-URL temp files left mid-flow.
_AUTH_DATA_CANDIDATES = (
    "~/.config/legendary/user.json",
    "~/.config/nile/user.json",
    "~/.config/unifideck/gog_token.json",
    "~/.config/unifideck/gogdl/gog_credentials.json",
    "~/.config/unifideck/microsoft_tokens.json",
    "~/.local/share/unifideck/microsoft_tokens.json",
    "~/.local/share/unifideck/gog_auth_url.txt",
    "~/.local/share/unifideck/ms_auth_url.txt",
    "~/.local/share/unifideck/epic_auth_url.txt",
    "~/.local/share/unifideck/amazon_auth_url.txt",
    "~/.local/share/unifideck/ubisoft_upc_session.txt",
)

# Unifideck-owned store creds under ``~/.config/unifideck`` (leaves the
# user's ``config.json`` and Heroic's ``heroic_gogdl`` untouched).
_CONFIG_AUTH_FILES = (
    "gog_token.json",
    "gog_credentials.json",
    "gogdl_auth.json",
    "gog_save_paths.json",
    "microsoft_tokens.json",
)
_CONFIG_AUTH_DIRS = ("gogdl",)


def is_unifideck_owned(
    entry: dict[str, Any],
    unifideck_tag: str,
    is_unifideck_launch_options: Callable[[str], bool],
) -> bool:
    """True iff a VDF shortcut entry is Unifideck-owned.

    Two independent signals so cleanup catches entries even when
    Steam silently strips one of them:

    * **LaunchOptions pattern** — most reliable, Steam preserves
      ``LaunchOptions`` across updates.
    * **UNIFIDECK_TAG** in ``tags`` — secondary signal for old
      entries that pre-date the LaunchOptions convention.
    """
    launch = entry.get("LaunchOptions", "")
    if isinstance(launch, str) and is_unifideck_launch_options(launch):
        return True
    tags = entry.get("tags")
    tag_values: list[Any] = []
    if isinstance(tags, dict):
        tag_values = list(tags.values())
    elif isinstance(tags, list):
        tag_values = list(tags)
    return any(
        isinstance(v, str) and v == unifideck_tag for v in tag_values
    )


def sweep_nonsteam_grid(grid_dir: str, keep_appids: set[int]) -> int:
    """Delete non-Steam grid artwork files not in *keep_appids*.

    Files are named ``<grid_dir>/<unsigned><suffix>``; real Steam
    appids are < 2³¹, so any ``>= 0x80000000`` prefix is a non-Steam
    shortcut's art. Blocking I/O — call from a thread.
    """
    prefix_re = re.compile(r"^(\d+)")
    base = Path(grid_dir)
    if not base.is_dir():
        return 0
    count = 0
    for match in base.iterdir():
        if not match.is_file():
            continue
        m = prefix_re.match(match.name)
        if not m:
            continue
        appid = int(m.group(1))
        if appid < 0x80000000 or appid in keep_appids:
            continue
        try:
            match.unlink(missing_ok=True)
            count += 1
        except OSError:
            logger.exception("[cleanup] unlink(%s) failed", match)
    return count


def sweep_auth_data() -> int:
    """Delete every store's persisted auth data + stray temp files.

    Belt-and-suspenders on top of ``registry.logout_all`` — each store's
    ``logout`` *should* clear its own credentials, but it no-ops when the
    auth submodule isn't wired and its CLI logout swallows errors. Deleting
    the files the ``is_available`` probes read guarantees signed-out state.
    """
    count = 0
    for raw in _AUTH_DATA_CANDIDATES:
        p = Path(raw).expanduser()
        try:
            if p.is_file():
                p.unlink(missing_ok=True)
                count += 1
        except OSError:
            logger.exception("[cleanup] unlink(%s) failed", p)
    return count


def sweep_data_dir(keep: frozenset[str]) -> int:
    """Delete residual state under ``~/.local/share/unifideck``.

    Iterating-and-deleting (rather than an explicit unlink list) means new
    state files added later are swept automatically — the wipe stays
    complete by construction. ``keep`` is preserved (destructive mode passes
    an empty set, reclaiming the prefixes and local saves).
    """
    data_dir = Path("~/.local/share/unifideck").expanduser()
    if not data_dir.is_dir():
        return 0
    count = 0
    for entry in data_dir.iterdir():
        if entry.name in keep:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                if safe_rmtree(entry):
                    count += 1
            else:
                entry.unlink(missing_ok=True)
                count += 1
        except OSError:
            logger.exception("[cleanup] delete(%s) failed", entry)
    return count


def sweep_external_prefixes() -> int:
    """Delete per-game prefixes recorded *outside* the data dir.

    Ubisoft games installed to SD/custom storage record an absolute
    ``prefix_path`` in ``ubisoft_id_map.json`` that lives outside
    ``~/.local/share/unifideck/prefixes``, so the blanket data-dir wipe
    never reaches them. Internal prefixes are left to the data-dir wipe.
    """
    id_map = Path("~/.local/share/unifideck/ubisoft_id_map.json").expanduser()
    data_dir = str(Path("~/.local/share/unifideck").expanduser())
    try:
        data = json.loads(id_map.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    count = 0
    for entry in data.values():
        p = entry.get("prefix_path") if isinstance(entry, dict) else None
        if not p:
            continue
        if str(Path(p).expanduser()).startswith(data_dir):
            continue
        if safe_rmtree(p):
            count += 1
    return count


def sweep_config_auth() -> int:
    """Delete Unifideck-owned store creds under ``~/.config/unifideck``.

    The live GOG refresh token sits at ``gog_credentials.json`` /
    ``gogdl_auth.json`` (top level), so a GOG login otherwise survives
    "Delete all data". Removes those plus the Unifideck gogdl config dir.
    """
    base = Path("~/.config/unifideck").expanduser()
    count = 0
    for name in _CONFIG_AUTH_FILES:
        p = base / name
        try:
            if p.is_file():
                p.unlink(missing_ok=True)
                count += 1
        except OSError:
            logger.exception("[cleanup] unlink(%s) failed", p)
    for name in _CONFIG_AUTH_DIRS:
        d = base / name
        if d.is_dir() and not d.is_symlink() and safe_rmtree(d):
            count += 1
    return count
