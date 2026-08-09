"""Resilient store-id extraction from Steam shortcut LaunchOptions.

Steam preserves ``LaunchOptions`` across shutdowns reliably (much more
so than custom ``tags``, which can be stripped by Steam updates or
third-party tools). So we identify Unifideck-managed shortcuts via a
regex match on LaunchOptions, not via the legacy ``UNIFIDECK_TAG``.

The regex tolerates user-appended parameters (LSFG=1, MANGOHUD=1,
``%command%`` wrappers, etc.) and handles Amazon's dotted IDs like
``amzn1.adg.product.<uuid>``.

Ported from ``staging:py_modules/unifideck/shortcuts/launch_options.py``.
"""

from __future__ import annotations

import re

# ``\b`` anchors the store prefix on a word boundary so
# ``not-epic:foo`` doesn't match. Amazon's IDs use dots, so we
# include ``.`` in the id character class but require the first
# character to be alphanumeric (skips e.g. ``epic:.hidden`` noise).
STORE_ID_PATTERN = re.compile(
    r"\b(epic|gog|amazon|ubisoft|battlenet|microsoft):"
    r"([a-zA-Z0-9][a-zA-Z0-9._-]*)",
)


def extract_store_id(launch_options: str) -> tuple[str, str] | None:
    """Return ``(store, game_id)`` extracted from ``launch_options``.

    Returns ``None`` when no Unifideck pattern is present.
    """
    if not launch_options:
        return None
    match = STORE_ID_PATTERN.search(launch_options)
    if match:
        return match.group(1), match.group(2)
    return None


def is_unifideck_shortcut(launch_options: str) -> bool:
    """Cheap predicate: does this LaunchOptions look Unifideck-managed?"""
    if not launch_options:
        return False
    return STORE_ID_PATTERN.search(launch_options) is not None


def get_store_prefix(launch_options: str) -> str | None:
    """Return the store name (``"epic"``, ``"gog"``, …) or ``None``."""
    if not launch_options:
        return None
    match = STORE_ID_PATTERN.search(launch_options)
    return match.group(1) if match else None


def get_full_id(launch_options: str) -> str | None:
    """Return the canonical ``"<store>:<game_id>"`` key, or ``None``.

    Used as a map key (e.g. into ``shortcuts_registry.json``) — the
    canonical form discards any user-appended params so equality
    holds across runs even if the user adds MANGOHUD=1 later.
    """
    if not launch_options:
        return None
    match = STORE_ID_PATTERN.search(launch_options)
    return f"{match.group(1)}:{match.group(2)}" if match else None


def preserve_user_params(
    current_launch_options: str, new_store_id: str,
) -> str:
    """Swap the canonical core in ``current_launch_options`` for ``new_store_id``.

    Preserves any user-appended parameters around the matched
    ``<store>:<id>`` portion. Used when reclaiming an orphaned
    shortcut: we want the new ``LaunchOptions`` to point at the
    current game while keeping the user's customisations.
    """
    if not current_launch_options:
        return new_store_id
    match = STORE_ID_PATTERN.search(current_launch_options)
    if not match:
        return new_store_id
    return (
        current_launch_options[: match.start()]
        + new_store_id
        + current_launch_options[match.end():]
    )


__all__ = [
    "STORE_ID_PATTERN",
    "extract_store_id",
    "get_full_id",
    "get_store_prefix",
    "is_unifideck_shortcut",
    "preserve_user_params",
]
