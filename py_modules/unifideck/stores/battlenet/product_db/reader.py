"""Tolerant reader for Battle.net's ``product.db``.

py_modules/unifideck/stores/battlenet/product_db/reader.py

Never raises. Every failure mode we know about — file absent, zero length,
torn write mid-download, an Agent that renumbered a field — degrades to an
empty or partial result plus a WARN, because the caller is a library sync
that must not blow up when a download happens to be in flight.

Scope note: ``product.db`` is the *secondary* installed-state source. The
primary is ``aggregate.json`` (plain JSON, and much richer — display name,
real exe, launch URI, official artwork). This module exists for what
``aggregate.json`` cannot tell us: whether an install actually finished,
which version, and the total size. See ``ownership/installed.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import schema
from .wire import WireError, get_bytes, get_scalar, get_str, submessages

logger = logging.getLogger(__name__)

# Path of product.db relative to a prefix's drive_c.
PRODUCT_DB_RELATIVE = "ProgramData/Battle.net/Agent/product.db"

# The Agent writes a sibling '.product.db' during updates. It is a torn
# intermediate and must never be read — reading it yields half a record.
_TORN_SIBLING = ".product.db"


def _as_bool(value: int | None) -> bool:
    return bool(value)


def _parse_settings(blob: bytes) -> tuple[str | None, str | None, str | None]:
    return (
        get_str(blob, schema.F_INSTALL_PATH),
        get_str(blob, schema.F_PLAY_REGION),
        get_str(blob, schema.F_LANGUAGE),
    )


def _parse_state(blob: bytes) -> tuple[bool, bool, bool, str | None, int | None]:
    """Pull the completion flags, version and total size out of cached_state."""
    installed = playable = update_complete = False
    version: str | None = None
    total: int | None = None

    base = get_bytes(blob, schema.F_BASE_STATE)
    if base:
        installed = _as_bool(get_scalar(base, schema.F_INSTALLED))
        playable = _as_bool(get_scalar(base, schema.F_PLAYABLE))
        update_complete = _as_bool(get_scalar(base, schema.F_UPDATE_COMPLETE))
        version = get_str(base, schema.F_VERSION)

    size_state = get_bytes(blob, schema.F_SIZE_STATE)
    if size_state:
        raw_total = get_scalar(size_state, schema.F_TOTAL_BYTES)
        # 0 means "not known yet" (the whole download), not "zero bytes".
        total = raw_total or None

    return installed, playable, update_complete, version, total


def _parse_install(blob: bytes) -> schema.ProductInstall | None:
    code = get_str(blob, schema.F_PRODUCT_CODE)
    if not code:
        return None
    install_path = play_region = language = None
    settings = get_bytes(blob, schema.F_SETTINGS)
    if settings:
        install_path, play_region, language = _parse_settings(settings)

    installed = playable = update_complete = False
    version: str | None = None
    total: int | None = None
    cached = get_bytes(blob, schema.F_CACHED_STATE)
    if cached:
        installed, playable, update_complete, version, total = _parse_state(cached)

    return schema.ProductInstall(
        code=code,
        uid=get_str(blob, schema.F_UID),
        install_path=install_path,
        play_region=play_region,
        language=language,
        version=version,
        installed=installed,
        playable=playable,
        update_complete=update_complete,
        total_bytes=total,
    )


def parse_product_db(raw: bytes) -> dict[str, schema.ProductInstall]:
    """Parse raw ``product.db`` bytes into ``{product_code: ProductInstall}``.

    Games only — the Agent and client records are filtered out. A record
    that fails to parse is skipped rather than aborting the whole file.
    """
    out: dict[str, schema.ProductInstall] = {}
    if not raw:
        return out
    try:
        blobs = list(submessages(raw, schema.F_PRODUCT_INSTALL))
    except WireError as exc:
        logger.warning("[Battlenet] product.db is not decodable: %s", exc)
        return out

    for blob in blobs:
        try:
            entry = _parse_install(blob)
        except WireError as exc:
            logger.warning("[Battlenet] skipping undecodable product record: %s", exc)
            continue
        if entry and entry.is_game:
            out[entry.code] = entry
    return out


def read_product_db(drive_c: Path) -> dict[str, schema.ProductInstall]:
    """Read and parse ``product.db`` under ``drive_c``. Never raises."""
    path = Path(drive_c) / PRODUCT_DB_RELATIVE
    if path.name == _TORN_SIBLING:
        return {}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("[Battlenet] cannot read %s: %s", path, exc)
        return {}
    return parse_product_db(raw)
