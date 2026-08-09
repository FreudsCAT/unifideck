"""Protected shortcut IDs — shortcuts the reconcile pass must never drop.

Auth-forwarder shortcuts (e.g. ``ubisoft:upc-auth``) are owned by
the auth flow, not the game-library flow. Without an explicit
protected-set, the reconcile pass would treat them as stale managed
entries (they match the ``<store>:<id>`` LaunchOptions shape) and
delete them after every sync — breaking the next login attempt.

Membership rule
===============
A LaunchOptions full-id is protected if either:

* it appears verbatim in :data:`PROTECTED_IDS` (exact match), or
* it starts with one of the prefixes in :data:`PROTECTED_PREFIXES`
  (catches store-internal auth shortcuts that share a launcher).

Both checks ignore the user-param suffix (``[...]``) — protection
is per-launch-target, not per-user-permutation.
"""
from __future__ import annotations

# Exact full-ids that must be preserved. Singular per store, but
# new stores might add their own; extend this set rather than
# scattering string literals across reconcile code.
PROTECTED_IDS: frozenset[str] = frozenset({
    "ubisoft:upc-auth",
    "battlenet:bnet-auth",
    "epic:epic-auth",
    "gog:gog-auth",
    "amazon:amazon-auth",
    # NOTE: Microsoft has no protected auth id. Its 0.7 auth flow uses
    # ephemeral, frontend-managed shortcuts (``microsoft:ms-auth`` /
    # ``microsoft:ms-auth-temp-*``) that never reach shortcuts.vdf, so
    # there is nothing to protect here. The old persistent 0.6.x
    # ``microsoft:ms-auth`` row MUST stay sweepable so reconcile can
    # remove it on the next sync — do not add it back.
})

# Prefix-protected — when an auth shortcut uses a per-session id
# (e.g. ``store:auth-2026-05-18T...``) the suffix changes each
# time but the prefix is stable.
PROTECTED_PREFIXES: tuple[str, ...] = (
    "auth-",
)


def is_protected(full_id: str | None) -> bool:
    """Return True if ``full_id`` matches the protected-set or a prefix.

    Caller normally strips the ``[...]`` user-param suffix before
    calling (see :func:`launch_options.get_full_id`).
    """
    if not full_id:
        return False
    if full_id in PROTECTED_IDS:
        return True
    # Match on the id portion after the ``store:`` prefix so
    # ``epic:auth-xyz`` reads as protected.
    parts = full_id.split(":", 1)
    return len(parts) == 2 and any(
        parts[1].startswith(prefix) for prefix in PROTECTED_PREFIXES
    )
