"""utils/config_helpers.py — None-safe ConfigManager accessors.

# OP-33c | py_modules/unifideck/utils/config_helpers.py | Depends: OP-11a

Centralises the ``_cfg`` helper previously copy-pasted across
13 modules (``cdp_client``, ``artwork_service``,
``cloud_save_service``, ``metacritic``, ``unifidb``, ``paths``,
``locale``, ``browser``, ``library``, ``manifest``,
``gog_config``, and two others). The historical pattern is
reproduced here verbatim for semantic parity, with one
addition: a rate-limited WARNING log the first time a given
caller passes ``config=None``, making latent "forgot to inject
config" bugs observable in Decky's logs instead of being
silently papered over.

Design notes:
  - Exposed as ``get_cfg`` (public, imported) rather than
    ``_cfg`` (module-private, the old name). Callers should
    import ``from unifideck.utils.config_helpers import
    get_cfg``.
  - The broad ``except Exception`` is intentional:
    ``ConfigManager`` is duck-typed — tests pass a stub, prod
    passes the real class. Any AttributeError or KeyError on
    the stub must degrade to the default, not propagate.
  - The warning tracks ``(caller_module, caller_lineno)`` so
    noisy call sites only log once per (site, process) rather
    than flooding on every access during a sync loop.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

# Sites that have already emitted a "config is None" warning.
# Keyed by ``(module, lineno)`` so each unique call site warns
# once per process lifetime. Thread-safe: the seen-sites set
# uses atomic set operations, no lock needed.
_warned_sites: set[tuple[str, int]] = set()


def get_cfg(
    config: ConfigManager | None,
    key: str,
    default: Any,
) -> Any:
    """Return ``config.get(key, default)`` or ``default`` on any failure.
    Three failure modes handled uniformly:
      - ``config is None``: emits a rate-limited WARNING
        tagged with the calling frame's ``(module, lineno)``
        so a forgotten DI shows up in logs without flooding
        on tight loops.
      - ``config.get`` raises ``Exception``: silently
        swallowed (test stubs or deprecated keys should
        degrade to default, not crash a feature).
      - ``config.get`` returns None: returned as-is (the
        caller decides whether None is acceptable — we don't
        second-guess a legitimate None default).
    Thread-safe: the seen-sites set uses atomic set
    operations, no lock needed.
    """
    if config is None:
        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        if caller:
            site = (caller.f_globals.get("__name__", "?"), caller.f_lineno)
            if site not in _warned_sites:
                _warned_sites.add(site)
                logger.warning(
                    "[config_helpers] get_cfg called with config=None "
                    "from %s:%d — using default for key '%s'",
                    site[0], site[1], key,
                )
        return default
    try:
        val = config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


# ── Cold-start config path ───────────────────────────────────
# Used before ``ConfigManager`` is ready. Must stay constant
# and not depend on any DI so the launcher cold-start path can
# read it before the bootstrap has wired ConfigManager.
_COLD_START_CONFIG_PATH = "~/.local/share/unifideck/config.json"


def read_config_int_cold_start(
    key: str, default: int,
) -> int:
    """Read a positive int from config.json without ConfigManager.
    Bypasses the normal ``ConfigManager`` API because this
    helper runs on the launcher cold-start path, before
    the bootstrap has wired ``ConfigManager``. Reading the
    JSON directly keeps the cold-start import graph
    minimal (no ``config/`` subpackage dependency, no
    bootstrap coupling).
    Dotted ``key`` (e.g. ``"launcher.auth_max_seconds"``)
    walks nested dicts. Values outside the positive-int
    range (zero, negative, non-int, missing) return
    ``default``. Used for a handful of launcher timing
    knobs — called 2-3 times per cold start, never on the
    hot path, so the missing caching is intentional:
    simpler + lower risk than memoising.
    """
    try:
        path = os.path.expanduser(_COLD_START_CONFIG_PATH)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default

    # Walk dotted key
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default

    if isinstance(current, int) and current > 0:
        return current
    return default
