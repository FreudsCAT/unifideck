"""The details panel must name the Proton the game will really use.

Unifideck has no Proton picker: the user sets one in Steam's
Properties > Compatibility, and on the first launch the plugin moves that
setting into its own pin and clears Steam's copy. After that neither
place a user would look shows it any more — hence the line in the panel,
and hence these tests, which pin the two rules that keep it honest:

* it names a tool only when the *user* chose it for *this* game, and
* only when that tool actually resolves, because the launcher falls
  through to its own default when it does not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.compatibility import effective_proton
from unifideck.compatibility.effective_proton import describe_effective_proton

_GAME = "epic:fa4240e5"


@pytest.fixture(autouse=True)
def _no_choice_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine where nothing is set, so each test opts in to one thing."""
    _patch_helpers(monkeypatch, steam="", global_default="", saved="")
    _patch_resolution(monkeypatch, resolves=True)


def _patch_helpers(
    monkeypatch: pytest.MonkeyPatch,
    *, steam: str, global_default: str, saved: str,
) -> None:
    import unifideck.compatibility.proton_helpers as helpers
    monkeypatch.setattr(
        helpers, "get_compat_tool_for_app", lambda _appid: steam,
    )
    monkeypatch.setattr(
        helpers, "get_global_default_compat_tool", lambda: global_default,
    )
    monkeypatch.setattr(
        helpers, "get_saved_proton_tool", lambda _key: saved,
    )


def _patch_resolution(
    monkeypatch: pytest.MonkeyPatch, *, resolves: bool,
) -> None:
    """Stand in for the on-disk lookup of a Proton build.

    The real one walks Steam's libraries, so the directory name it would
    return is what a test asserts on: Steam records "proton_9" but the
    build on disk is "Proton 9.0 (Beta)".
    """
    import unifideck.launcher.proton.infrastructure.selector as selector
    dirs = {
        "proton_9": "Proton 9.0 (Beta)",
        "GE-Proton7-55": "GE-Proton7-55",
    }
    monkeypatch.setattr(
        selector, "resolve_proton_path",
        lambda tool: (
            Path("/steam/common") / dirs.get(tool, tool) / "proton"
            if resolves else None
        ),
    )


# ── what the user picked ───────────────────────────────────────────────
def test_reports_the_tool_steam_has_set_right_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_helpers(
        monkeypatch, steam="GE-Proton7-55", global_default="", saved="",
    )
    result = describe_effective_proton(_GAME, 1234)
    assert result["tool_name"] == "GE-Proton7-55"
    assert result["source"] == "steam"


def test_reports_unifidecks_own_pin_once_steam_has_been_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state every game lands in after its first launch."""
    _patch_helpers(
        monkeypatch, steam="", global_default="", saved="GE-Proton7-55",
    )
    result = describe_effective_proton(_GAME, 1234)
    assert result["tool_name"] == "GE-Proton7-55"
    assert result["source"] == "pin"


def test_steam_wins_over_a_stale_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same order as selector tiers 1 and 2 — the live value is fresher."""
    _patch_helpers(
        monkeypatch, steam="proton_9", global_default="", saved="GE-Proton7-55",
    )
    assert describe_effective_proton(_GAME, 1234)["tool_name"] == "proton_9"


def test_shows_the_name_a_human_would_recognise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steam stores "proton_9"; nobody would recognise that on screen."""
    _patch_helpers(monkeypatch, steam="proton_9", global_default="", saved="")
    result = describe_effective_proton(_GAME, 1234)
    assert result["display_name"] == "Proton 9.0 (Beta)"


# ── and what it must stay quiet about ──────────────────────────────────
def test_says_nothing_when_the_user_chose_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal case: the plugin picks GE-Proton and that is unremarkable."""
    result = describe_effective_proton(_GAME, 1234)
    assert result == {"tool_name": "", "display_name": "", "source": ""}


def test_ignores_steams_global_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bazzite ships one. It is not a choice about THIS game, and treating
    it as one would put the line under every game in the library."""
    _patch_helpers(
        monkeypatch,
        steam="Proton-CachyOS Latest",
        global_default="Proton-CachyOS Latest",
        saved="",
    )
    assert describe_effective_proton(_GAME, 1234)["tool_name"] == ""


def test_ignores_the_steam_linux_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forcing SLR is legitimate but it is not a Proton."""
    _patch_helpers(
        monkeypatch,
        steam="steamlinuxruntime_sniper", global_default="", saved="",
    )
    assert describe_effective_proton(_GAME, 1234)["tool_name"] == ""


def test_says_nothing_when_the_chosen_tool_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launcher falls through to its own default when a tool cannot be
    resolved, so naming it here would tell the user something untrue."""
    _patch_helpers(monkeypatch, steam="GE-Proton9-26", global_default="", saved="")
    _patch_resolution(monkeypatch, resolves=False)
    assert describe_effective_proton(_GAME, 1234)["tool_name"] == ""


def test_falls_back_to_the_pin_when_there_is_no_shortcut_appid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """appid 0 means the shortcut was not found; the pin is still readable."""
    _patch_helpers(
        monkeypatch, steam="proton_9", global_default="", saved="GE-Proton7-55",
    )
    result = describe_effective_proton(_GAME, 0)
    assert result["tool_name"] == "GE-Proton7-55"


def test_a_broken_lookup_costs_a_line_of_ui_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_helpers(monkeypatch, steam="GE-Proton7-55", global_default="", saved="")

    def boom(_tool: str) -> None:
        raise OSError("steam library unreadable")

    import unifideck.launcher.proton.infrastructure.selector as selector
    monkeypatch.setattr(selector, "resolve_proton_path", boom)
    assert describe_effective_proton(_GAME, 1234) == effective_proton._NONE
