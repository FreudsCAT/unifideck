"""config/key_presence.py — Verify every code-referenced config key resolves.

Complements ``ConfigValidator`` (which checks shape + types against the
JSON Schema). This module answers a different question: after the
3-layer merge (``_FALLBACK`` → ``defaults/config.json`` → user overrides),
does **every key actually read by the backend code** resolve to a
non-``None`` value?

Why this matters
----------------
The ConfigValidator is permissive about optional keys — only 9 out of
105 schema nodes are ``required``. Without this extra check, a key that
is:

    * read by code at runtime (e.g. ``config.get("cloud.enabled")``)
    * declared in the schema but not marked required
    * accidentally omitted from ``defaults/config.json``

…would silently return ``None`` and crash the feature that relied on it.
This check makes that class of bug fail at boot with a clear error
rather than surface as an obscure ``AttributeError`` on ``None`` later.

Design
------
The list of required-at-runtime keys is **declared in code** (the
``RUNTIME_REQUIRED_KEYS`` tuple below) rather than scraped with a
regex from call sites. Scraping is fragile — renaming a key in code
wouldn't update the scraped list, and the presence check would still
pass against the old name. An explicit list forces the developer to
update it alongside the call site, which is exactly what we want: a
machine-readable contract that code and config stay in sync.

Updating RUNTIME_REQUIRED_KEYS
------------------------------
When adding a new ``config.get("some.key", default)`` or
``_cfg(config, "some.key", default)`` call anywhere in ``py_modules/``:

    1. Add ``"some.key"`` to ``RUNTIME_REQUIRED_KEYS`` below.
    2. Add the key to ``defaults/config.json`` with a sensible value.
    3. Add the key to ``py_modules/unifideck/config/schema.json`` under
       the right section so typos in user overrides are caught.
    4. Remove the hardcoded ``default`` arg from the call site — it
       is now redundant and merely obscures a missing declaration.

The pre-commit hook (``scripts/check_config_keys.py``) enforces that
every key string literal passed to ``config.get`` or ``_cfg`` appears
in this tuple, so step 1 cannot be forgotten in practice.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config_manager import ConfigManager

logger = logging.getLogger(__name__)


# Every key the backend code reads via ``config.get`` or ``_cfg``.
# Sorted alphabetically for grep-friendliness; duplicates removed.
# Organised by top-level section so the reader can spot gaps per domain.
RUNTIME_REQUIRED_KEYS: tuple[str, ...] = (
    # accounts
    "accounts.poll_interval_seconds",
    # artwork
    "artwork.download_timeout_seconds",
    "artwork.failure_cooldown_seconds",
    "artwork.steamgriddb_api_base",
    "artwork.steamgriddb_api_key",
    # auth
    "auth.browser_oauth_timeout_seconds",
    "auth.browser_poll_interval_seconds",
    # binary_resolver
    "binary_resolver.version_check_timeout_seconds",
    # cache_ttl
    "cache_ttl.compat",
    "cache_ttl.metacritic_metadata",
    "cache_ttl.steam_metadata",
    # cdp
    "cdp.eval_timeout_seconds",
    "cdp.host",
    "cdp.port",
    "cdp.response_timeout_seconds",
    # cloud
    "cloud.enabled",
    "cloud.root",
    "cloud.sync_wait_timeout_seconds",
    "cloud.tolerance_seconds",
    # compat (ProtonDB + Deck Verified API timeouts)
    "compat.deck_verified_timeout_seconds",
    "compat.protondb_timeout_seconds",
    # dedup — Microsoft Store is intentionally absent so xCloud /
    # Game Pass entries are never filtered against Steam-native or
    # cross-store duplicates. ``cross_store_enabled`` gates the opt-in
    # "one shortcut per game" collapse (default false).
    "dedup.tracked_stores",
    "dedup.cross_store_enabled",
    # discovery
    "discovery.manifest_filename",
    # download
    "download.custom_path",
    # i18n — whole sub-dict is consumed as a unit by utils/locale
    "i18n",
    # metadata
    "metadata.metacritic.composer_url",
    "metadata.metacritic.fetch_timeout_seconds",
    "metadata.steam_store.search_timeout_seconds",
    "metadata.steam_store.search_url",
    "metadata.unifidb.cdn_base",
    "metadata.unifidb.fetch_timeout_seconds",
    "metadata.unifidb.match_threshold",
    # paths
    "paths.data_dir",
    "paths.games_map",
    "paths.sd_card_root",
    "paths.steam_candidates",
    # probes
    "probes.probe_to_features",
    "probes.probe_to_handlers",
    # stores — per-store sub-dicts consumed whole by the store ctor
    "stores.amazon",
    "stores.amazon.user_file",
    "stores.epic",
    "stores.epic.user_file",
    "stores.gog.client_secret",
    "stores.microsoft.subscription_check_url",
    # sync
    "sync.artwork_concurrency",
    # ui
    "ui.locale",
)


class KeyPresenceError(RuntimeError):
    """Raised when one or more runtime-required keys resolve to None."""


def assert_all_keys_resolve(config: ConfigManager) -> None:
    """Verify every key in ``RUNTIME_REQUIRED_KEYS`` returns non-None.

    Raises:
        KeyPresenceError: If any required key is missing. The error
            message lists every missing path at once so operators can
            fix their ``defaults/config.json`` in a single edit rather
            than iterating through N reboots.

    This runs once at plugin boot, right after
    ``ConfigValidator.validate_config`` succeeds but before any service
    instantiates. A failure here is fatal — it means the plugin code
    depends on a value that isn't declared anywhere, which is a
    developer error (either RUNTIME_REQUIRED_KEYS or defaults/config.json
    is out of sync with the call sites).

    """
    missing: list[str] = []
    for key in RUNTIME_REQUIRED_KEYS:
        # Pass a sentinel rather than None so keys legitimately set to
        # None in config are distinguished from absent keys. Anything
        # other than the sentinel is considered "present".
        sentinel = object()
        value = config.get(key, sentinel)
        if value is sentinel:
            missing.append(key)
    if missing:
        msg = (
            f"[config] {len(missing)} required key(s) missing from "
            f"the merged config. Expected in defaults/config.json:\n  "
            + "\n  ".join(missing)
        )
        logger.error(msg)
        raise KeyPresenceError(msg)
    logger.info(
        "[config] key presence check passed: %d keys resolve",
        len(RUNTIME_REQUIRED_KEYS),
    )


def collect_missing_keys(config: ConfigManager) -> list[str]:
    """Return the list of missing keys without raising.

    Useful for diagnostics paths where the caller wants to report what
    went wrong rather than abort. The main boot path uses the stricter
    ``assert_all_keys_resolve`` variant.
    """
    missing: list[str] = []
    sentinel = object()
    for key in RUNTIME_REQUIRED_KEYS:
        if config.get(key, sentinel) is sentinel:
            missing.append(key)
    return missing
