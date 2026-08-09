"""Installed-state and artwork from the client's ``aggregate.json``.

py_modules/unifideck/stores/battlenet/ownership/installed.py

``ProgramData/Battle.net/Agent/aggregate.json`` is plain JSON and far
richer than the protobuf ``product.db`` sitting beside it::

    {"installed": [{
      "product_id": "hsb",
      "name": "Hearthstone",
      "icon_path": "C:/Program Files (x86)/Hearthstone/Hearthstone Beta Launcher.exe",
      "launch_uri": "battlenet://game/hsb",
      "box_art_uri": "https://.../hsb/box-enUS.webp",
      "logo_art_uri": "https://.../hsb/logo-enUS.webp",
      "last_played_timestamp": 0}]}

That single file gives the installed set, display name, the real game
executable, a launch URI, official CDN artwork and last-played — no
protobuf needed. It is therefore the primary installed-state source, with
``product_db`` demoted to the things it cannot answer.

**The trap:** ``aggregate.json`` is written *early*. During a real 12.43 GB
install the Hearthstone entry appeared at roughly 40% downloaded. Presence
means "the client has started installing this", NOT "installed". Anything
that needs certainty must confirm via ``ProductInstall.is_ready``, which is
why ``merge_install_state`` exists rather than trusting this file alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from unifideck.stores.battlenet.product_db import ProductInstall
from unifideck.stores.shared.wine_path import wine_path_to_linux

logger = logging.getLogger(__name__)

# Path of aggregate.json relative to a prefix's drive_c.
AGGREGATE_RELATIVE = "ProgramData/Battle.net/Agent/aggregate.json"


@dataclass(frozen=True, slots=True)
class InstalledGame:
    """One entry from ``aggregate.json``, enriched from ``product.db``."""

    code: str
    # The install variant id, e.g. 'hs_beta' for product code 'hsb'. This is
    # the join key against the catalog, which addresses titles by uid;
    # matching on `code` fails because the catalog never mentions it.
    uid: str | None = None
    name: str | None = None
    exe_windows_path: str | None = None
    launch_uri: str | None = None
    box_art_url: str | None = None
    logo_art_url: str | None = None
    last_played_ms: int | None = None
    # Filled from product.db, which is the only authority on these.
    install_path: str | None = None
    version: str | None = None
    total_bytes: int | None = None
    is_ready: bool = False
    # Linux-side paths, resolved from the Wine-side ones above. Populated by
    # `resolve_host_paths`, which needs the prefix and so cannot be done at
    # parse time.
    host_install_path: str | None = None
    host_exe_path: str | None = None


def _entry_to_game(entry: object) -> InstalledGame | None:
    if not isinstance(entry, dict):
        return None
    code = entry.get("product_id")
    if not isinstance(code, str) or not code:
        return None
    last_played = entry.get("last_played_timestamp")
    return InstalledGame(
        code=code,
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        exe_windows_path=entry.get("icon_path") if isinstance(entry.get("icon_path"), str) else None,
        launch_uri=entry.get("launch_uri") if isinstance(entry.get("launch_uri"), str) else None,
        box_art_url=entry.get("box_art_uri") if isinstance(entry.get("box_art_uri"), str) else None,
        logo_art_url=entry.get("logo_art_uri") if isinstance(entry.get("logo_art_uri"), str) else None,
        last_played_ms=last_played if isinstance(last_played, int) and last_played > 0 else None,
    )


def parse_aggregate(raw: bytes | str) -> dict[str, InstalledGame]:
    """Parse ``aggregate.json`` bytes into ``{product_code: InstalledGame}``."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        logger.warning("[Battlenet] aggregate.json is not valid JSON: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, InstalledGame] = {}
    for entry in payload.get("installed") or []:
        game = _entry_to_game(entry)
        if game:
            out[game.code] = game
    return out


def read_aggregate(drive_c: Path) -> dict[str, InstalledGame]:
    """Read ``aggregate.json`` under ``drive_c``. Never raises."""
    path = Path(drive_c) / AGGREGATE_RELATIVE
    try:
        return parse_aggregate(path.read_bytes())
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("[Battlenet] cannot read %s: %s", path, exc)
        return {}


def merge_install_state(
    aggregate: dict[str, InstalledGame],
    products: dict[str, ProductInstall],
) -> dict[str, InstalledGame]:
    """Overlay ``product.db`` truth onto the ``aggregate.json`` entries.

    Both are keyed on the product code, which is the stable key (uid is a
    variant string: a real Hearthstone install reported uid ``hs_beta``
    against code ``hsb``).

    An entry present in ``aggregate.json`` but not yet complete in
    ``product.db`` keeps ``is_ready=False`` — that is the mid-download case
    and the reason this merge is not optional.
    """
    merged: dict[str, InstalledGame] = {}
    for code, game in aggregate.items():
        product = products.get(code)
        if product is None:
            merged[code] = game
            continue
        merged[code] = replace(
            game,
            uid=product.uid,
            install_path=product.install_path,
            version=product.version,
            total_bytes=product.total_bytes,
            is_ready=product.is_ready,
        )
    # A product.db row with no aggregate entry is still a real install.
    for code, product in products.items():
        if code not in merged:
            merged[code] = InstalledGame(
                code=code,
                uid=product.uid,
                install_path=product.install_path,
                version=product.version,
                total_bytes=product.total_bytes,
                is_ready=product.is_ready,
            )
    return merged


def resolve_host_paths(
    games: dict[str, InstalledGame],
    prefix: Path,
) -> dict[str, InstalledGame]:
    """Translate the Wine-side paths into Linux ones for a given prefix.

    ``product.db`` records ``C:/Program Files (x86)/Hearthstone`` and
    ``aggregate.json`` records the exe the same way; both are useless to
    Steam or to a size walk until converted. A path that will not convert
    stays ``None`` rather than being guessed — for a non-C: drive that means
    the game is on removable media whose ``dosdevices`` link is missing, and
    inventing a path would point install detection somewhere the game is not.
    """
    resolved: dict[str, InstalledGame] = {}
    for code, game in games.items():
        install = (
            wine_path_to_linux(game.install_path, str(prefix))
            if game.install_path
            else None
        )
        exe = (
            wine_path_to_linux(game.exe_windows_path, str(prefix))
            if game.exe_windows_path
            else None
        )
        resolved[code] = replace(game, host_install_path=install, host_exe_path=exe)
    return resolved


def read_installed(
    drive_c: Path,
    products: dict[str, ProductInstall],
    prefix: Path | None = None,
) -> dict[str, InstalledGame]:
    """Full installed-state read for one prefix, host paths included."""
    merged = merge_install_state(read_aggregate(drive_c), products)
    return resolve_host_paths(merged, prefix) if prefix is not None else merged
