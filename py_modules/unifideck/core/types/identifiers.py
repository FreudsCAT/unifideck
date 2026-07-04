"""core/types/identifiers.py — validation for store-supplied identifiers.

This module provides the single authoritative regex and helper for
sanitising untrusted ``store`` / ``game_id`` strings at the RPC
boundary. Every store-facing RPC method should pass user-supplied
identifiers through :func:`validate_game_id` (and the matching
:func:`validate_store_id`) before letting them flow into:

* subprocess argv (legendary, gogdl, nile, …)
* filesystem paths via ``os.path.join`` / ``Path(...)``
* URL templates (``f"{base}/products/{game_id}"``)

Threat model
------------
The Decky plugin runs as the user; the frontend (a TS/React layer
inside Steam's overlay) is trusted. So there's no "remote attacker"
in the classic sense. The audit value of this module is
defence-in-depth against:

* a compromised browser session that hits the Decky API with a
  ``..`` payload to clobber files outside the install root;
* a malicious URI handler (``unifideck://install/<store>/<game_id>``)
  invoked from a link the user clicks in another app;
* a store API that returns a malformed identifier the plugin then
  hands to its own subprocess + path code.

Format
------
Real store identifiers are a small alphanumeric subset:

* Epic     : ``Fortnite_e5e10c1d``  (alpha + underscore)
* GOG      : ``1149782466``         (numeric)
* Amazon   : a UUID without dashes
* Microsoft: ``9NCBCSZSJRSB``       (alphanumeric)
* Ubisoft  : ``348``                 (numeric)

The conservative regex below covers every observed in-the-wild
identifier while rejecting ``/``, the path-separator backslash,
``..``, ``:``, spaces, and every shell/path metacharacter.
"""
from __future__ import annotations

import re

# ── Identifier regex ───────────────────────────────────────────────
# Allowed: ASCII letters, digits, underscore, hyphen, dot.
# Length ceiling stops absurd inputs from blowing up logs / paths.
# A single leading or trailing dot is rejected (would mean
# ``./.`` or ``..`` once expanded into a path).
_GAME_ID_RE = re.compile(r"^(?!\.)[A-Za-z0-9_\-.]{1,128}(?<!\.)$")

# Store IDs are even tighter — they're hard-coded names declared by
# the plugin (epic / gog / amazon / microsoft / ubisoft). We use the
# same regex for symmetry but the realistic ceiling is ~16 chars.
_STORE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class InvalidIdentifierError(ValueError):
    """Raised when a caller-supplied identifier fails validation.

    Caught at the RPC boundary and converted into a structured
    ``Result(error="invalid_identifier")`` rather than letting a
    traceback bubble up to the frontend.
    """


def validate_game_id(game_id: str) -> str:
    """Return ``game_id`` unchanged after validating its shape.

    Raises :class:`InvalidIdentifierError` on anything that contains
    a path separator, ``..`` traversal, NUL byte, control character,
    or that exceeds 128 characters.

    The function returns the input on success rather than ``None``
    or a bool so callers can chain it inline:
    ``await store.install_game(validate_game_id(raw_id), …)``.
    """
    if not isinstance(game_id, str):
        raise InvalidIdentifierError(
            f"game_id must be str, got {type(game_id).__name__}",
        )
    if ".." in game_id:
        raise InvalidIdentifierError(
            f"game_id contains '..' (potential path traversal): "
            f"{game_id!r}",
        )
    if "\x00" in game_id:
        raise InvalidIdentifierError(
            "game_id contains NUL byte",
        )
    if not _GAME_ID_RE.match(game_id):
        raise InvalidIdentifierError(
            f"game_id does not match [A-Za-z0-9_.-]{{1,128}}: "
            f"{game_id!r}",
        )
    return game_id


def validate_store_id(store_id: str) -> str:
    """Return ``store_id`` unchanged after validating its shape.

    Stricter than :func:`validate_game_id`: store IDs are
    plugin-controlled identifiers (``epic`` / ``gog`` / …), never
    user-supplied content. Anything outside ``[a-z][a-z0-9_]*`` is
    a programming error.
    """
    if not isinstance(store_id, str):
        raise InvalidIdentifierError(
            f"store_id must be str, got {type(store_id).__name__}",
        )
    if not _STORE_ID_RE.match(store_id):
        raise InvalidIdentifierError(
            f"store_id does not match [a-z][a-z0-9_]*: {store_id!r}",
        )
    return store_id


def is_safe_game_id(game_id: str) -> bool:
    """Boolean form of :func:`validate_game_id` for branch logic.

    Use this when the call site wants to log + skip rather than
    raise — for example, inside a sync loop that should keep
    processing other games when one entry is malformed.
    """
    try:
        validate_game_id(game_id)
    except InvalidIdentifierError:
        return False
    return True
