"""services/cloud_save/gog_cloud_api.py — GOG cloud-storage HTTP client.

Stateless helpers split out of ``gog_strategy.py`` (which had crossed the
550-LOC complexity gate). Everything here is a pure function over GOG's
public endpoints — build manifests, ``remote-config.gog.com`` cloud-save
templates, and the ``cloudstorage.gog.com`` object listing — with no
dependency on the strategy's instance state. The strategy orchestrates
these (credentials, caching, prefix resolution); this module just talks HTTP.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from unifideck.core.net.ssl_helpers import ssl_ctx_permissive
from unifideck.services.cloud_save import safety

# The store-neutral Galaxy HTTP primitives live in the GOG store package
# (Layer 4) so the store can reuse them for achievements without importing
# this service (Layer 5). ``fetch_gog_client_creds`` / ``exchange_game_token``
# moved there too — gog_strategy imports those from galaxy_api directly.
from unifideck.stores.gog.galaxy_api import GOG_BUILDS_URL, http_json

logger = logging.getLogger(__name__)

# GOG cloud-save location templates use path variables like
# ``<?DOCUMENTS?>\\The Witcher 3``. Each variable maps to a folder under
# the Wine prefix's ``drive_c/users/steamuser``. Mirrors GOG Galaxy /
# Heroic so resolved paths match where the game actually reads saves.
# NOTE: GOG is inconsistent about the LocalLow token — some games emit
# ``APPLICATION_DATA_LOCAL_LOW`` (with the underscore, e.g. Control:Override),
# others ``APPLICATION_DATA_LOCALLOW``. Accept both or the Auto-Cloud
# candidate is dropped and we fall back to the wrong SDK path.
_GOG_PATH_VARS: dict[str, tuple[str, ...]] = {
    "DOCUMENTS": ("Documents",),
    "SAVED_GAMES": ("Saved Games",),
    "APPLICATION_DATA_LOCAL": ("AppData", "Local"),
    "APPLICATION_DATA_LOCALLOW": ("AppData", "LocalLow"),
    "APPLICATION_DATA_LOCAL_LOW": ("AppData", "LocalLow"),
    "APPLICATION_DATA_ROAMING": ("AppData", "Roaming"),
}

# Tokens that point INSIDE the game's install directory rather than a user
# folder — very common for older GOG titles (Fallout, Thief, the SSI Gold
# Box games …) which save next to their files. Resolved against the install
# path (games.map ``work_dir``); Proton games write there directly via the
# umu mount, so the Linux install dir IS where the saves land.
_GOG_INSTALL_VARS = frozenset({"INSTALL", "INSTALL_DIR", "GAME_DIR"})

# GOG cloud objects are namespaced by the ``name`` of the cloudStorage
# location they belong to (e.g. ``saves/…`` or ``__default/…``). gogdl's
# ``save-sync --name`` selects which namespace to pull/push; ``__default``
# is gogdl's own default and the namespace for SDK IStorage saves.
GOG_DEFAULT_NAMESPACE = "__default"

_REMOTE_CONFIG_URL = (
    "https://remote-config.gog.com/components/galaxy_client/"
    "clients/{client_id}?component_version=2.0.45"
)


def resolve_gog_location(
    template: str, drive_c: Path, install_path: str = "",
) -> Path | None:
    """Resolve a GOG cloud-save location template to a filesystem dir.

    Maps the leading ``<?VAR?>`` to its base — a folder under
    ``drive_c/users/steamuser`` for user-folder tokens, or the game's
    ``install_path`` for install-dir tokens (``<?INSTALL?>`` …) — and appends
    the remainder (Windows backslashes normalised). Returns None for an
    unrecognised variable, or for an install-dir token when ``install_path``
    is unknown (e.g. the game isn't installed yet).
    """
    match = re.match(r"<\?([A-Z_]+)\?>(.*)", template.strip())
    if not match:
        return None
    var, rest = match.group(1), match.group(2)
    rest = rest.replace("\\", "/").strip("/")
    if var in _GOG_INSTALL_VARS:
        if not install_path:
            return None
        base = Path(install_path)
    else:
        parts = _GOG_PATH_VARS.get(var)
        if parts is None:
            return None
        base = drive_c / "users" / "steamuser"
        for part in parts:
            base = base / part
    resolved = base / rest if rest else base
    # Wine is case-insensitive but Linux isn't — match the dir the game
    # actually created so gogdl doesn't sync into a divergent-cased folder.
    from unifideck.services.cloud_save.path_resolver import WinePrefixResolver
    return Path(WinePrefixResolver.realize_case_insensitive(str(resolved)))


def fetch_gog_client_id(game_id: str) -> str | None:
    builds = http_json(GOG_BUILDS_URL.format(game_id=game_id))
    items = builds.get("items") if isinstance(builds, dict) else None
    if not items:
        return None
    link = items[0].get("link")
    if not link:
        return None
    manifest = http_json(link, decompress=True)
    cid = manifest.get("clientId") if isinstance(manifest, dict) else None
    return str(cid) if cid else None


def fetch_gog_save_locations(client_id: str) -> list[tuple[str, str]]:
    """Auto-Cloud ``(namespace_name, location_template)`` pairs from remote-config.

    The ``name`` is the cloud-storage namespace the location's objects live
    under (gogdl's ``--name``); we MUST preserve it — gogdl only pulls/pushes
    objects whose name starts with it (default ``__default``), so a game whose
    saves live under e.g. ``saves/`` is silently skipped if we don't pass it.
    """
    cfg = http_json(_REMOTE_CONFIG_URL.format(client_id=client_id))
    try:
        locations = cfg["content"]["Windows"]["cloudStorage"]["locations"]
    except (KeyError, TypeError):
        return []
    return [
        (str(loc.get("name") or GOG_DEFAULT_NAMESPACE), str(loc["location"]))
        for loc in locations
        if isinstance(loc, dict) and loc.get("location")
    ]


def resolve_gog_save_locations(
    client_id: str, drive_c: Path, install_path: str = "",
) -> list[tuple[Path, str]]:
    """ALL ``(local dir, cloud namespace)`` save targets for a GOG game.

    Per GOG's developer docs a game uses Auto Cloud and/or SDK IStorage; some
    games (e.g. BioShock Remastered) split saves across MULTIPLE Auto-Cloud
    locations with different namespaces (``saves`` + ``saves2``). We return
    every resolved Auto-Cloud location (in GOG's order) followed by the SDK
    IStorage dir (namespace ``__default``) so the caller can sync them all —
    syncing only one would strand the saves the game keeps in the others.
    ``install_path`` (games.map ``work_dir``) resolves install-dir tokens
    (``<?INSTALL?>`` …), common for older GOG titles that save next to files.
    """
    targets: list[tuple[Path, str]] = []
    for name, template in fetch_gog_save_locations(client_id):
        resolved = resolve_gog_location(template, drive_c, install_path)
        if resolved is not None:
            targets.append((resolved, name))
    # GOG Galaxy SDK IStorage location → the "__default" cloud namespace.
    targets.append((
        drive_c / "users" / "steamuser" / "AppData" / "Local"
        / "GOG.com" / "Galaxy" / "Applications" / client_id / "Storage",
        GOG_DEFAULT_NAMESPACE,
    ))
    return targets


def select_primary_save_target(
    targets: list[tuple[Path, str]],
) -> tuple[Path, str] | None:
    """The single best ``(dir, namespace)`` for status/backup/snapshot.

    Prefers whichever target already holds real saves on disk; otherwise the
    first (the historical behaviour, so games like The Witcher 3 are
    unchanged). The full ``targets`` list is what actually gets synced.
    """
    for cand, name in targets:
        try:
            if cand.is_dir() and safety.has_save_data(cand):
                logger.info(
                    "[GOGSync] Using on-disk save dir: %s (cloud namespace %r)",
                    cand, name,
                )
                return cand, name
        except Exception as e:
            logger.debug("[GOGSync] save-dir candidate %s check failed: %s", cand, e)
            continue
    return targets[0] if targets else None


def pick_gog_save_dir(
    client_id: str, drive_c: Path, install_path: str = "",
) -> tuple[Path, str] | None:
    """Primary ``(local save dir, cloud namespace)`` for a GOG game.

    Thin wrapper over :func:`resolve_gog_save_locations` +
    :func:`select_primary_save_target`. The namespace (gogdl's ``--name``) is
    the location's ``name`` for an Auto-Cloud dir, ``__default`` for SDK
    IStorage — callers MUST pass it to ``gogdl save-sync --name`` so the right
    objects sync (and uploads land in the namespace the game actually reads).
    """
    return select_primary_save_target(
        resolve_gog_save_locations(client_id, drive_c, install_path),
    )


def list_cloud_objects(
    user_id: str, client_id: str, access_token: str,
) -> list[dict[str, Any]] | None:
    """GET the GOG cloud-storage object list for a game. 404 → ``[]``."""
    import urllib.error
    import urllib.request
    url = f"https://cloudstorage.gog.com/v1/{user_id}/{client_id}"
    ctx = ssl_ctx_permissive("GOG cloud-save listing — outdated Deck cert store")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "GalaxyClient/2.0.45",
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, list) else []
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def head_object_local_mtime(
    user_id: str, client_id: str, access_token: str, name: str,
) -> float | None:
    """The object's preserved LOCAL mtime (``X-Object-Meta-LocalLastModified``).

    GOG's object LIST only reports the server-side ``last_modified`` (when the
    PUT was received), so that timestamp jumps to "now" on every upload even
    when the bytes are unchanged — making "Cloud" disagree with "Local" right
    after a push. gogdl uploads each file with the ORIGINAL local mtime in the
    ``X-Object-Meta-LocalLastModified`` header (and restores it via ``os.utime``
    on download); a HEAD reads it back. Returns the epoch float, or ``None`` on
    a missing header / HTTP / parse failure so the caller falls back to the
    server ``last_modified``. Never raises.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    url = (
        f"https://cloudstorage.gog.com/v1/{user_id}/{client_id}/"
        + urllib.parse.quote(name, safe="/")
    )
    ctx = ssl_ctx_permissive("GOG cloud-save HEAD — outdated Deck cert store")
    req = urllib.request.Request(url, method="HEAD", headers={
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "GalaxyClient/2.0.45",
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = resp.headers.get("X-Object-Meta-LocalLastModified")
    except (urllib.error.URLError, OSError) as e:
        logger.debug("[GOGSync] HEAD %s failed: %s", name, e)
        return None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone().timestamp()
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def summarize_cloud_objects(
    objects: list[dict[str, Any]],
    mtime_resolver: Callable[[str], float | None] | None = None,
) -> dict[str, Any]:
    """Summarize the ACTIVE cloud save set (the newest location prefix).

    GOG cloud storage namespaces objects by location NAME — a game can
    carry several (e.g. ``__default/…`` plus a stale ``saves/…`` left by
    an earlier sync). gogdl materializes only ONE location into the local
    dir, so counting EVERY object overstates the cloud save count relative
    to what a download actually produces (the "Cloud 20 / Local 11"
    confusion). Group by top-level prefix and report only the
    most-recently-written group — the set gogdl pulls. Our own
    ``.unifideck_sync.json`` manifest is excluded so the count lines up
    with ``safety.snapshot`` on the local side.

    ``mtime_resolver`` (optional) maps an object name → its preserved local
    mtime (see :func:`head_object_local_mtime`). When given, the reported
    ``timestamp`` is the newest LOCAL mtime across the active group rather than
    the server PUT time — so "Cloud" matches "Local" after an upload — with a
    per-object fall back to the server ``last_modified`` when the resolver can't
    supply one. Without it the behaviour is the original server-time summary.
    """
    groups = _group_cloud_objects(objects)
    if not groups:
        return {
            "has_saves": False, "timestamp": 0.0,
            "file_count": 0, "total_bytes": 0,
        }
    # The active location = the group whose newest object is the most recent
    # (server time) — what gogdl treats as the current cloud save.
    active = max(
        groups,
        key=lambda top: max((ts or 0.0 for _, ts in groups[top]), default=0.0),
    )
    items = groups[active]
    timestamps = _resolve_active_timestamps(items, mtime_resolver)
    return {
        "has_saves": len(items) > 0,
        "timestamp": max(timestamps) if timestamps else 0.0,
        "file_count": len(items),
        "total_bytes": 0,
    }


def _group_cloud_objects(
    objects: list[dict[str, Any]],
) -> dict[str, list[tuple[str, float | None]]]:
    """Group cloud objects by top-level location prefix.

    Maps each prefix to a list of ``(name, server_last_modified_epoch|None)``,
    excluding our own ``.unifideck_sync.json`` manifest so the count lines up
    with the local snapshot.
    """
    groups: dict[str, list[tuple[str, float | None]]] = {}
    for entry in objects:
        name = str(entry.get("name", ""))
        if name.endswith(".unifideck_sync.json"):
            continue
        top = name.split("/", 1)[0] if "/" in name else ""
        server_ts: float | None = None
        lm = entry.get("last_modified")
        if lm:
            try:
                server_ts = datetime.fromisoformat(lm).astimezone().timestamp()
            except ValueError:
                server_ts = None
        groups.setdefault(top, []).append((name, server_ts))
    return groups


def _resolve_active_timestamps(
    items: list[tuple[str, float | None]],
    mtime_resolver: Callable[[str], float | None] | None,
) -> list[float]:
    """Newest-write timestamps for the active group.

    Prefers each object's preserved LOCAL mtime (so the cloud time reflects
    when the save was actually written, matching the local snapshot) and falls
    back per-object to the server ``last_modified``.
    """
    timestamps: list[float] = []
    for name, server_ts in items:
        ts = mtime_resolver(name) if mtime_resolver is not None else None
        if ts is None:
            ts = server_ts
        if ts is not None:
            timestamps.append(ts)
    return timestamps
