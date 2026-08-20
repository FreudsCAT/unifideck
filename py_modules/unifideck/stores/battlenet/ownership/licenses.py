"""Read the account's licence ledger out of the Battle.net client.

py_modules/unifideck/stores/battlenet/ownership/licenses.py

This is the **primary** ownership source, and it is a local file rather
than a web call. ``CachedData.db`` is a plain SQLite database the client
keeps in the prefix; its ``key_value_store`` table holds a JSON blob under
``features_cached_data_points`` containing a ``licenses`` array of numeric
licence ids — the same ledger the client uses to decide Install vs Buy.

Why this rather than ``account.battle.net/api/games-and-subs``: measured
on-device 2026-08-09, that endpoint returned **5** entries for an account
whose licence list resolved to **14+** owned products. It enumerates
*game accounts* (titles with a service account), which is a different and
much smaller thing than entitlements — it omitted Warcraft I and II
Remastered, Diablo, Warcraft III, Avowed and more. The web endpoint is
retained as secondary enrichment (subscription state, last-played), never
as the ownership source.

Cost of this choice: the ledger only exists once the user has signed into
the client at least once. The wrapper archetype requires that for install
and launch anyway.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Path of the client's cache DB relative to a prefix's drive_c.
CACHED_DATA_RELATIVE = "users/steamuser/AppData/Local/Battle.net/CachedData.db"

_FEATURES_KEY = "features_cached_data_points"

# Table names are inlined at each call site rather than interpolated, so the
# queries are literals: nothing here is ever built from untrusted input.
_SQL_FEATURES = "SELECT value FROM key_value_store WHERE key = ?"
_SQL_BATTLE_TAG = "SELECT battle_tag FROM login_cache LIMIT 1"


@dataclass(frozen=True, slots=True)
class AccountLicences:
    """The signed-in account's identity and entitlement ids."""

    licence_ids: frozenset[int]
    account_id: int | None = None
    battle_tag: str | None = None
    account_region: str | None = None
    account_country: str | None = None

    @property
    def is_usable(self) -> bool:
        return bool(self.licence_ids)


def _query_one(
    con: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
) -> tuple[object, ...] | None:
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error as exc:
        logger.debug("[Battlenet] CachedData.db query failed (%s): %s", sql, exc)
        return None
    return tuple(row) if row is not None else None


def _read_features(con: sqlite3.Connection) -> dict[str, object]:
    row = _query_one(con, _SQL_FEATURES, (_FEATURES_KEY,))
    if not row or not row[0]:
        return {}
    try:
        parsed = json.loads(str(row[0]))
    except (TypeError, ValueError) as exc:
        logger.warning("[Battlenet] %s is not valid JSON: %s", _FEATURES_KEY, exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_battle_tag(con: sqlite3.Connection) -> str | None:
    row = _query_one(con, _SQL_BATTLE_TAG)
    return str(row[0]) if row and row[0] else None


def parse_licences(db_path: Path) -> AccountLicences:
    """Read ``CachedData.db``. Never raises; returns empty on any failure.

    Opened read-only through a URI so a running client cannot be disturbed
    and a locked database degrades rather than blocking a library sync.
    """
    empty = AccountLicences(licence_ids=frozenset())
    path = Path(db_path)
    if not path.is_file():
        return empty
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("[Battlenet] cannot open %s: %s", path, exc)
        return empty
    try:
        features = _read_features(con)
        raw_ids = features.get("licenses")
        ids = frozenset(i for i in raw_ids if isinstance(i, int)) if isinstance(raw_ids, list) else frozenset()
        account_id = features.get("account_id")
        region = features.get("account_region")
        country = features.get("account_country")
        return AccountLicences(
            licence_ids=ids,
            account_id=account_id if isinstance(account_id, int) else None,
            battle_tag=_read_battle_tag(con),
            account_region=region if isinstance(region, str) else None,
            account_country=country if isinstance(country, str) else None,
        )
    finally:
        con.close()


def read_licences(drive_c: Path) -> AccountLicences:
    """Read the licence ledger from a prefix's ``drive_c``."""
    return parse_licences(Path(drive_c) / CACHED_DATA_RELATIVE)
