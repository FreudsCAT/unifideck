"""utils/config_helpers.py — None-safe ConfigManager accessors.

Centralizes the ``_cfg`` helper that was copy-pasted across 13
modules (cdp_client, artwork_service, cloud_save_service,
metacritic, unifidb, paths, locale, browser, library, manifest,
gog_config, and two others). The historical pattern:

    def _cfg(config: "ConfigManager | None", key, default):
        if config is None:
            return default
        try:
            return config.get(key, default)
        except Exception:
            return default

is reproduced here verbatim for semantic parity, with one
addition: a rate-limited WARNING log the first time a given
caller passes ``config=None``, making latent "forgot to inject
config" bugs observable in Decky's logs instead of being
silently papered over.

Design notes:

- Exposed as ``get_cfg`` (public, imported) rather than ``_cfg``
  (module-private, the old name). Callers should import
  ``from unifideck.utils.config_helpers import get_cfg``.
- The broad ``except Exception`` is intentional: ``ConfigManager``
  is duck-typed — tests pass a stub, prod passes the real
  class. Any AttributeError or KeyError on the stub must
  degrade to the default, not propagate.
- The warning tracks (caller_module, caller_lineno) so noisy
  call sites only log once per (site, process) rather than
  flooding on every access during a sync loop.
"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

# (module, lineno) pairs that have already emitted a WARNING about
# config=None. Prevents log spam from hot paths like sync loops.
_none_sites_seen: set[tuple[str, int]] = set()


def get_cfg(
    config: ConfigManager | None,
    key: str,
    default: Any,
) -> Any:
    """Read ``key`` from ``config`` with graceful fallback.

    Behavior:

    - If ``config`` is None, return ``default``. Log a one-time
      WARNING identifying the caller so missing-injection bugs
      surface in logs instead of silently returning ``default``
      on every access.
    - If ``config.get(key, default)`` raises (non-``ConfigManager``
      stubs in tests, schema drift in prod), swallow the
      exception and return ``default``.
    - Otherwise return what ``config.get`` returns.

    The ``default`` must match the value declared for ``key`` in
    ``defaults/config.json`` — ``get_cfg`` is a safety net, not
    a place to introduce new defaults. Divergence between the
    schema default and the value passed here is a bug.
    """
    if config is None:
        # Once-per-site WARNING so a forgotten injection is
        # visible but we don't flood the log when the same
        # buggy object is used in a tight loop.
        frame = sys._getframe(1)
        site = (frame.f_globals.get("__name__", "?"), frame.f_lineno)
        if site not in _none_sites_seen:
            _none_sites_seen.add(site)
            logger.warning(
                "[config_helpers] config=None at %s:%d key=%r "
                "— falling back to default=%r. Likely a forgotten "
                "ConfigManager injection.",
                site[0], site[1], key, default,
            )
        return default
    try:
        return config.get(key, default)
    except Exception:
        # Duck-typed config objects in tests may raise anything.
        return default


# Cold-start config path — used before ConfigManager is ready.
# Must stay constant and not depend on any DI.
_COLD_START_CONFIG_PATH = "~/.local/share/unifideck/config.json"


def _read_cold_start_json() -> dict[str, Any] | None:
    """Read the cold-start config.json file, returning ``None`` on
    any error (missing file, malformed JSON, OS error).

    Single-purpose helper extracted from
    ``read_config_int_cold_start`` so the imports live in one
    place — ``json`` and ``pathlib`` are loaded lazily here to
    keep the launcher cold-start import graph minimal (no
    ``config/`` subpackage dependency).

    Returns the parsed top-level dict, or ``None`` to signal
    "use default" to the caller. We deliberately don't
    distinguish "file missing" from "malformed JSON" — both
    funnel to the same fallback path in the caller.
    """
    import json
    from pathlib import Path

    config_path = Path(_COLD_START_CONFIG_PATH).expanduser()
    if not config_path.is_file():
        return None
    try:
        with config_path.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_config_int_cold_start(key: str, default: int) -> int:
    """Read a positive int from config.json without ConfigManager.

    Bypasses the normal ``ConfigManager`` API because this helper
    runs on the launcher cold-start path, before the bootstrap
    has wired ``ConfigManager``. Reading the JSON directly keeps
    the cold-start import graph minimal (no ``config/`` subpackage
    dependency, no bootstrap coupling).

    Dotted ``key`` (e.g. ``"launcher.auth_max_seconds"``) walks
    into nested objects. Returns ``default`` on any error — file
    missing, malformed JSON, wrong type, key absent, non-positive
    int — so callers never have to guard the call.

    Do NOT use after bootstrap completes — prefer
    ``ConfigManager.get_int(key, default)`` once the registry is
    up, as it picks up in-memory overrides, defaults layering,
    and migration rewrites.

    Refactor history (2026-05-15): the I/O concern was extracted
    to ``_read_cold_start_json`` so this function reads as
    pure dotted-key traversal + validation, with the
    "did we fail to load?" decision collapsed into a single
    ``is None`` check.
    """
    data = _read_cold_start_json()
    if data is None:
        return default
    node: object = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    if not isinstance(node, int) or node <= 0:
        return default
    return node
