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
from datetime import datetime
from pathlib import Path
from typing import Any

from unifideck.core.net.ssl_helpers import ssl_ctx_permissive
from unifideck.services.cloud_save import safety

logger = logging.getLogger(__name__)

# GOG cloud-save location templates use path variables like
# ``<?DOCUMENTS?>\\The Witcher 3``. Each variable maps to a folder under
# the Wine prefix's ``drive_c/users/steamuser``. Mirrors GOG Galaxy /
# Heroic so resolved paths match where the game actually reads saves.
_GOG_PATH_VARS: dict[str, tuple[str, ...]] = {
    "DOCUMENTS": ("Documents",),
    "SAVED_GAMES": ("Saved Games",),
    "APPLICATION_DATA_LOCAL": ("AppData", "Local"),
    "APPLICATION_DATA_LOCALLOW": ("AppData", "LocalLow"),
    "APPLICATION_DATA_ROAMING": ("AppData", "Roaming"),
}

_BUILDS_URL = (
    "https://content-system.gog.com/products/{game_id}"
    "/os/windows/builds?generation=2"
)
_REMOTE_CONFIG_URL = (
    "https://remote-config.gog.com/components/galaxy_client/"
    "clients/{client_id}?component_version=2.0.45"
)


def resolve_gog_location(template: str, drive_c: Path) -> Path | None:
    """Resolve a GOG cloud-save location template against a Wine prefix.

    Maps the leading ``<?VAR?>`` to its folder under
    ``drive_c/users/steamuser`` and appends the remainder (with Windows
    backslashes normalised). Returns None for an unrecognised variable.
    """
    match = re.match(r"<\?([A-Z_]+)\?>(.*)", template.strip())
    if not match:
        return None
    var, rest = match.group(1), match.group(2)
    parts = _GOG_PATH_VARS.get(var)
    if parts is None:
        return None
    base = drive_c / "users" / "steamuser"
    for part in parts:
        base = base / part
    rest = rest.replace("\\", "/").strip("/")
    return base / rest if rest else base


def http_json(url: str, decompress: bool = False) -> Any:
    """GET ``url`` and parse JSON, using the permissive SSL context.

    GOG's endpoints trip the Deck's outdated CA store, so we reuse the
    same permissive context the GOG store HTTP path uses. The
    content-system manifest is zlib/gzip-compressed — try the common
    decoders before parsing. Returns the decoded JSON (object or list);
    callers ``isinstance``-guard the shape they expect.
    """
    import gzip
    import urllib.request
    import zlib
    ctx = ssl_ctx_permissive("GOG cloud-save config — outdated Deck cert store")
    req = urllib.request.Request(
        url, headers={"User-Agent": "GalaxyClient/2.0.45"},
    )
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        raw = resp.read()
    if decompress:
        for decoder in (
            zlib.decompress,
            lambda b: zlib.decompress(b, 16 + zlib.MAX_WBITS),
            gzip.decompress,
            lambda b: b,
        ):
            try:
                raw = decoder(raw)
                break
            except Exception:  # noqa: S112 — trying each decoder; a failure just means try the next
                continue
    return json.loads(raw)


def fetch_gog_client_id(game_id: str) -> str | None:
    builds = http_json(_BUILDS_URL.format(game_id=game_id))
    items = builds.get("items") if isinstance(builds, dict) else None
    if not items:
        return None
    link = items[0].get("link")
    if not link:
        return None
    manifest = http_json(link, decompress=True)
    cid = manifest.get("clientId") if isinstance(manifest, dict) else None
    return str(cid) if cid else None


def fetch_gog_save_locations(client_id: str) -> list[str]:
    """All Auto-Cloud save-location templates from GOG's remote-config."""
    cfg = http_json(_REMOTE_CONFIG_URL.format(client_id=client_id))
    try:
        locations = cfg["content"]["Windows"]["cloudStorage"]["locations"]
    except (KeyError, TypeError):
        return []
    return [
        loc["location"] for loc in locations
        if isinstance(loc, dict) and loc.get("location")
    ]


def fetch_gog_client_creds(game_id: str) -> tuple[str | None, str | None]:
    """Game's Galaxy ``(clientId, clientSecret)`` from the build manifest."""
    builds = http_json(_BUILDS_URL.format(game_id=game_id))
    items = builds.get("items") if isinstance(builds, dict) else None
    if not items:
        return None, None
    link = items[0].get("link")
    if not link:
        return None, None
    manifest = http_json(link, decompress=True)
    if not isinstance(manifest, dict):
        return None, None
    cid = manifest.get("clientId")
    csec = manifest.get("clientSecret")
    return (str(cid) if cid else None, str(csec) if csec else None)


def pick_gog_save_dir(client_id: str, drive_c: Path) -> Path | None:
    """Pick the local save dir across GOG's TWO cloud-save mechanisms.

    Per GOG's developer docs a game uses ONE of:
      * **Auto Cloud** — a filesystem directory listed in remote-config
        (e.g. ``<?DOCUMENTS?>\\The Witcher 3``); or
      * **SDK IStorage** — programmatic storage under
        ``AppData/Local/GOG.com/Galaxy/Applications/<clientId>/Storage``.
    We build candidates from BOTH and prefer whichever already holds real
    saves on disk; otherwise fall back to the first Auto-Cloud location
    (the historical behaviour, so games like The Witcher 3 are unchanged).
    """
    candidates: list[Path] = []
    for template in fetch_gog_save_locations(client_id):
        resolved = resolve_gog_location(template, drive_c)
        if resolved is not None:
            candidates.append(resolved)
    # GOG Galaxy SDK IStorage location.
    candidates.append(
        drive_c / "users" / "steamuser" / "AppData" / "Local"
        / "GOG.com" / "Galaxy" / "Applications" / client_id / "Storage",
    )
    if not candidates:
        return None
    for cand in candidates:
        try:
            if cand.is_dir() and safety.has_save_data(cand):
                logger.info("[GOGSync] Using on-disk save dir: %s", cand)
                return cand
        except Exception as e:
            logger.debug("[GOGSync] save-dir candidate %s check failed: %s", cand, e)
            continue
    return candidates[0]


def exchange_game_token(
    client_id: str, client_secret: str, refresh_token: str,
) -> str | None:
    """Exchange the refresh token for a GAME-client-scoped access token."""
    import urllib.parse
    import urllib.request
    url = "https://auth.gog.com/token?" + urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    })
    ctx = ssl_ctx_permissive("GOG token exchange — outdated Deck cert store")
    req = urllib.request.Request(url, headers={"User-Agent": "GalaxyClient/2.0.45"})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        data = json.loads(resp.read())
    tok = data.get("access_token") if isinstance(data, dict) else None
    return str(tok) if tok else None


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


def summarize_cloud_objects(objects: list[dict[str, Any]]) -> dict[str, Any]:
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
    """
    counts: dict[str, int] = {}
    stamps: dict[str, list[float]] = {}
    for entry in objects:
        name = str(entry.get("name", ""))
        if name.endswith(".unifideck_sync.json"):
            continue
        top = name.split("/", 1)[0] if "/" in name else ""
        counts[top] = counts.get(top, 0) + 1
        lm = entry.get("last_modified")
        if lm:
            try:
                stamps.setdefault(top, []).append(
                    datetime.fromisoformat(lm).astimezone().timestamp()
                )
            except ValueError:
                pass
    if not counts:
        return {
            "has_saves": False, "timestamp": 0.0,
            "file_count": 0, "total_bytes": 0,
        }
    # The active location = the group whose newest object is the most
    # recent (what gogdl treats as the current cloud save).
    active = max(counts, key=lambda top: max(stamps.get(top) or [0.0]))
    group_stamps = stamps.get(active) or []
    return {
        "has_saves": counts[active] > 0,
        "timestamp": max(group_stamps) if group_stamps else 0.0,
        "file_count": counts[active],
        "total_bytes": 0,
    }
