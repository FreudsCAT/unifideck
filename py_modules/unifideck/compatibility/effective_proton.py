"""effective_proton — which Proton a game will actually launch with.

Read-only companion to :mod:`compatibility.proton_helpers`, written for
the game-details page: it answers "does this game have a Proton the user
chose, and what is it called", so the panel can say so instead of leaving
the user to guess.

Unifideck has no Proton picker of its own — the choice is made in Steam's
own Properties > Compatibility dialog — and it can end up in either of
two places, which is why a plain read of one of them is not enough:

1. ``config.vdf``'s ``CompatToolMapping[appid]``, where Steam puts it;
2. ``proton_settings.json``, Unifideck's own pin, where the frontend
   moves it just before clearing Steam's copy (see ``utils/protonPin.ts``
   for why that move has to happen at all).

The order here mirrors ``launcher.proton.infrastructure.selector``'s
tiers 1 and 2 exactly, so what the panel shows is what the launcher will
do. Deliberately excluded, matching that same module and the capture in
``protonPin.ts``:

* **Steam Linux Runtime** entries — not a Proton at all;
* **Steam's global default** (``CompatToolMapping["0"]``) — a distro-wide
  setting (Bazzite ships one), not something this user picked for this
  game. Announcing it as their choice would put a line under every game
  in the library;
* **tools that do not resolve on disk** — the selector falls through to
  its later tiers when a tool cannot be found, so naming it would tell
  the user the game runs on something it does not.

Anything excluded comes back as "no custom choice", and the caller shows
nothing — which is the honest answer for the common case, where the game
runs on the GE-Proton the plugin manages by itself.
"""
from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class EffectiveProton(TypedDict):
    """What the details panel needs to render the line."""

    #: Steam's internal id, verbatim ("proton_9", "GE-Proton7-55"). Empty
    #: when the user made no custom choice for this game.
    tool_name: str
    #: The same tool as a human reads it ("Proton 9.0 (Beta)"). Empty
    #: alongside an empty ``tool_name``.
    display_name: str
    #: Where the choice was found: "steam", "pin", or "" for neither.
    #: For logs and tests; the panel does not show it.
    source: str


_NONE: EffectiveProton = {"tool_name": "", "display_name": "", "source": ""}


def describe_effective_proton(
    store_game_id: str, appid_unsigned: int,
) -> EffectiveProton:
    """Return the Proton this game will run under, or empty for default.

    ``appid_unsigned`` is the Steam appid of the Unifideck shortcut, used
    to read Steam's live per-app setting; pass 0 when it is unknown and
    only the saved pin is consulted.
    """
    tool, source = _pick_tool(store_game_id, appid_unsigned)
    if not tool:
        return _NONE
    display = _display_name(tool)
    if not display:
        # Unresolvable: the launcher will fall through to a later tier, so
        # naming this tool would be a lie. Worth a log line — this is the
        # shape of "I picked a Proton and nothing happened".
        logger.info(
            "[effective_proton] %s: %r (from %s) does not resolve on "
            "disk; the launcher will fall through to its default",
            store_game_id, tool, source,
        )
        return _NONE
    return {"tool_name": tool, "display_name": display, "source": source}


def _pick_tool(store_game_id: str, appid_unsigned: int) -> tuple[str, str]:
    """The chosen tool and where it came from, before validation."""
    from unifideck.compatibility.proton_helpers import (
        get_compat_tool_for_app,
        get_global_default_compat_tool,
        get_saved_proton_tool,
        is_linux_runtime,
    )

    steam_tool = (
        get_compat_tool_for_app(appid_unsigned) if appid_unsigned else ""
    )
    if steam_tool and not is_linux_runtime(steam_tool):
        if steam_tool != get_global_default_compat_tool():
            return steam_tool, "steam"
        logger.debug(
            "[effective_proton] %s: %r is Steam's global default, not a "
            "per-game choice", store_game_id, steam_tool,
        )
    saved = get_saved_proton_tool(store_game_id) if store_game_id else ""
    if saved and not is_linux_runtime(saved):
        return saved, "pin"
    return "", ""


def _display_name(tool_name: str) -> str:
    """Resolve ``tool_name`` to the name a human would recognise.

    Steam records official Protons under an internal id that nobody would
    recognise on screen ("proton_9"), while the build on disk lives in a
    directory named the way Steam's own dialog shows it ("Proton 9.0
    (Beta)"). Third-party builds are already stored under their real name
    ("GE-Proton7-55") and come back unchanged.

    Empty when the tool cannot be found on disk.
    """
    from unifideck.launcher.proton.infrastructure.selector import (
        resolve_proton_path,
    )

    try:
        path = resolve_proton_path(tool_name)
    except Exception:
        logger.exception("[effective_proton] resolving %r failed", tool_name)
        return ""
    if path is None:
        return ""
    # resolve_proton_path returns the ``proton`` script; its directory is
    # the build, named as Steam displays it.
    return path.parent.name or tool_name
