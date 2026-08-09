"""Which stores are launcher-wrapper stores, and what that implies.

py_modules/unifideck/launcher/wrapper_stores.py

A *wrapper store* runs a vendor's own Windows client inside the prefix and
lets that client do the downloading and launching. Ubisoft (UPC) was the
first; Battle.net is the second. They share one structural consequence that
ordinary stores do not have: **the game's files live inside the prefix**,
so resetting the prefix destroys the user's install rather than costing a
rebuild.

That question was previously asked as a bare ``store == "ubisoft"`` string
comparison in five places. Centralising it is not tidiness — a disagreement
between two of those sites is exactly how the 2026-08-01 incident happened.
Launching Rayman Origins resolved ``proton_experimental``,
``prefix_setup`` borrowed managed GE-Proton for a winetricks verb,
``prefix_init`` saw the family change and wiped the prefix, deleting the
game. The borrow was for a step ``apply_prefix_compat`` skips for Ubisoft
anyway; the two sites disagreed about what Ubisoft needed.

Three separate predicates rather than one, deliberately. They currently
return the same set, but they answer different questions and will diverge:
EA App, for instance, installs some titles to ``Program Files`` *outside*
the prefix, so it would be a wrapper store that does not own its installs.

Stdlib-only and dependency-free, and deliberately **outside** the ``proton``
package: ``launcher/types/context`` needs it, and importing it from under
``proton`` pulled in ``proton/__init__`` -> handlers -> ``types/context``,
a cycle. It is also consumed from ``services/``, so a neutral home is
right. This is imported by the launcher, which runs under the system
Python (3.10-3.14), not Decky's bundled 3.11.
"""

from __future__ import annotations

# Stores whose vendor client runs inside the prefix and drives everything.
WRAPPER_STORES: frozenset[str] = frozenset({"ubisoft", "battlenet"})

# Wrapper stores whose games install INSIDE the prefix, making any prefix
# reset destructive to user data.
_PREFIX_OWNS_INSTALL: frozenset[str] = frozenset({"ubisoft", "battlenet"})

# Wrapper stores whose vendor client ships its own redistributables, making
# our generic winetricks/vcredist pass redundant (~90 s per first launch).
_SKIPS_GENERIC_COMPAT: frozenset[str] = frozenset({"ubisoft", "battlenet"})


def is_wrapper_store(store: str | None) -> bool:
    """True when the store launches through a vendor client in the prefix."""
    return bool(store) and store in WRAPPER_STORES


def prefix_owns_game_install(store: str | None) -> bool:
    """True when the game's own files live INSIDE the prefix.

    Ubisoft installs to ``drive_c/Program Files (x86)/Ubisoft/Ubisoft Game
    Launcher/games/``; Battle.net to ``drive_c/Program Files (x86)/<Game>``
    (confirmed on-device: a real Hearthstone install landed at
    ``C:/Program Files (x86)/Hearthstone``). Every other store downloads
    outside the prefix, so a reset there costs a rebuild — here it costs the
    user their game.
    """
    return bool(store) and store in _PREFIX_OWNS_INSTALL


def skips_generic_compat(store: str | None) -> bool:
    """True when the vendor client installs its own redistributables."""
    return bool(store) and store in _SKIPS_GENERIC_COMPAT


def uses_manual_download_phase(store: str | None) -> bool:
    """True when the vendor client owns the download and reports no progress.

    These stores get ``download_phase="manual"`` — an indeterminate bar —
    because there is no byte-level telemetry to drive a percentage, and a
    synthesised one would be a lie. Measured for Battle.net: ``product.db``
    carries no progress at all during a download (the completion field sits
    at exactly 0.0 across 12 GB, and the total-size field stays 0 until the
    install finishes), so the only honest signal is the growing byte count.

    They also own their own prefix bootstrap, so the generic prefix warmup
    must not run over the top of it.
    """
    return is_wrapper_store(store)
