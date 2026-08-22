"""Amazon, Ubisoft and Battle.net must launch in the user's language too.

The Epic path has its own suite (``test_epic_launch_language.py``). These
cover the other stores whose handlers build a ``ConfigManager`` of
their own inside the launcher process, because the same two defects hit
all of them:

* the bundled ``config.json`` was looked up at ``<plugin>/defaults/`` only,
  which does not exist on a Decky CLI install (the CLI flattens
  ``defaults/`` to the plugin root), so nothing but the hardcoded
  ``_FALLBACK`` was merged;
* no ``user_path`` was passed, so the language the user picked was never
  read whatever the layout.

Both tests drive the *real* ``ConfigManager`` over a fixture laid out the
way an installed plugin is, with the machine pinned to a language the
user did not choose — so a fallback can never masquerade as success.
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.utils.locale import get_unifideck_locale


def _installed_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_locale: str | None,
) -> Path:
    """Lay down the packaged layout + user config; return the plugin dir."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir(parents=True)
    repo_defaults = Path(__file__).resolve().parents[2] / "defaults/config.json"
    # The Decky CLI layout: defaults/config.json flattened to the install
    # root. This is what a plugin installed from a CLI-built zip looks
    # like, and the hardcoded nested path found nothing in it.
    (plugin_dir / "config.json").write_text(
        repo_defaults.read_text(encoding="utf-8"), encoding="utf-8",
    )
    user_cfg = tmp_path / "user_config.json"
    if user_locale is not None:
        user_cfg.write_text(
            json.dumps({"ui": {"locale": user_locale}}), encoding="utf-8",
        )
    monkeypatch.setenv("UNIFIDECK_USER_CONFIG", str(user_cfg))
    monkeypatch.setenv("HOME", str(tmp_path))       # no registry.vdf here
    # Pin what the resolver sees as the machine's locale. Patching
    # ``getlocale`` rather than ``LANG``: Python reads the process locale
    # set at startup, so setting the env mid-test proves nothing.
    import unifideck.utils.locale as locale_module
    monkeypatch.setattr(
        locale_module._locale, "getlocale", lambda: ("en_US", "UTF-8"),
    )
    return plugin_dir


def _plan(plugin_dir: Path, work_dir: Path, store: str) -> ProtonLaunchPlan:
    """A minimal launch plan (same shape as test_epic_launch_language)."""
    return ProtonLaunchPlan(
        context=types.SimpleNamespace(
            game_id="abc123", store=store,
            exe_path=work_dir / "game.exe",
            work_dir=work_dir,
            plugin_dir=plugin_dir,
        ),
        state=types.SimpleNamespace(wrappers=[], game_args=[], umu_id=None),
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=plugin_dir / "bin/umu/umu/umu-run",
        prefix_path=work_dir / "prefix",
        env={},
        on_process_start=None,
    )


def _capture_config(
    monkeypatch: pytest.MonkeyPatch, func_name: str,
) -> list[Any]:
    """Record the ConfigManager each handler hands to language setup.

    The handlers swallow every exception around language setup — a launch
    must never be blocked by it — so assertions have to happen outside the
    captured call, not inside it.
    """
    seen: list[Any] = []
    import unifideck.launcher.proton.language_setup as ls

    def _record(*_args: Any, config: Any = None, **_kw: Any) -> bool:
        seen.append(config)
        return True

    monkeypatch.setattr(ls, func_name, _record)
    return seen


# ── Ubisoft ────────────────────────────────────────────────────────────
def test_ubisoft_honours_the_language_picked_in_the_unifideck_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unifideck.launcher.proton.handlers.ubisoft import _apply_language_setup

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, "es-ES")
    seen = _capture_config(monkeypatch, "apply_ubisoft_language")

    _apply_language_setup(_plan(plugin_dir, tmp_path, "ubisoft"))

    assert len(seen) == 1
    assert get_unifideck_locale(seen[0]) == "es-ES"


def test_ubisoft_falls_back_to_the_machine_when_nothing_was_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unifideck.launcher.proton.handlers.ubisoft import _apply_language_setup

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, None)
    seen = _capture_config(monkeypatch, "apply_ubisoft_language")

    _apply_language_setup(_plan(plugin_dir, tmp_path, "ubisoft"))

    assert get_unifideck_locale(seen[0]) == "en-US"


# ── Amazon ─────────────────────────────────────────────────────────────
async def test_amazon_honours_the_language_picked_in_the_unifideck_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import unifideck.launcher.proton.handlers.generic as generic

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, "es-ES")
    seen = _capture_config(monkeypatch, "apply_amazon_language")

    async def _no_launch(*_args: Any, **_kw: Any) -> int:
        return 0

    monkeypatch.setattr(generic, "run_umu_with_retry", _no_launch)

    await generic._amazon_launch(_plan(plugin_dir, tmp_path, "amazon"))

    assert len(seen) == 1
    assert get_unifideck_locale(seen[0]) == "es-ES"


async def test_amazon_falls_back_to_the_machine_when_nothing_was_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import unifideck.launcher.proton.handlers.generic as generic

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, None)
    seen = _capture_config(monkeypatch, "apply_amazon_language")

    async def _no_launch(*_args: Any, **_kw: Any) -> int:
        return 0

    monkeypatch.setattr(generic, "run_umu_with_retry", _no_launch)

    await generic._amazon_launch(_plan(plugin_dir, tmp_path, "amazon"))

    assert get_unifideck_locale(seen[0]) == "en-US"


# ── Battle.net ─────────────────────────────────────────────────────────
#
# The store that was never wired into ``language_setup`` at all. Measured on
# this Deck 2026-08-23 with the plugin set to German: every Battle.net prefix
# still reported ``LocaleName="en-US"`` and ``sCountry="United States"``.
# Distinct from the client's own UI language, which ``wrapper_locale`` seeds
# into ``Battle.net.config``; this is what a Blizzard *game* reads from
# Windows when it picks its own default.
async def test_battlenet_honours_the_language_picked_in_the_unifideck_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unifideck.launcher.proton.handlers import battlenet_bootstrap

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, "de-DE")
    seen = _capture_config(monkeypatch, "apply_battlenet_language")

    assert await battlenet_bootstrap.ensure_language(
        _plan(plugin_dir, tmp_path, "battlenet"),
    )

    assert len(seen) == 1
    assert get_unifideck_locale(seen[0]) == "de-DE"


async def test_battlenet_falls_back_to_the_machine_when_nothing_was_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unifideck.launcher.proton.handlers import battlenet_bootstrap

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, None)
    seen = _capture_config(monkeypatch, "apply_battlenet_language")

    await battlenet_bootstrap.ensure_language(
        _plan(plugin_dir, tmp_path, "battlenet"),
    )

    assert get_unifideck_locale(seen[0]) == "en-US"


async def test_battlenet_language_setup_never_fails_a_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A game in the wrong language beats a game that will not start."""
    import unifideck.launcher.proton.language_setup as ls
    from unifideck.launcher.proton.handlers import battlenet_bootstrap

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, "de-DE")

    def _boom(*_args: Any, **_kw: Any) -> bool:
        raise OSError("user.reg is not writable")

    monkeypatch.setattr(ls, "apply_battlenet_language", _boom)

    assert await battlenet_bootstrap.ensure_language(
        _plan(plugin_dir, tmp_path, "battlenet"),
    ) is False


async def test_battlenet_writes_the_locale_into_user_reg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the real registry writer, not a captured call.

    The four keys below are the ones the Battle.net prefixes on this Deck were
    measured holding at ``en-US`` / ``ENU`` / ``United States``.
    """
    from unifideck.launcher.proton.handlers import battlenet_bootstrap

    plugin_dir = _installed_plugin(tmp_path, monkeypatch, "de-DE")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "user.reg").write_text(
        'WINE REGISTRY Version 2\n\n'
        '[Control Panel\\\\International] 1785947765\n'
        '"Locale"="00000409"\n'
        '"LocaleName"="en-US"\n'
        '"sLanguage"="ENU"\n'
        '"sCountry"="United States"\n',
        encoding="utf-8",
    )

    assert await battlenet_bootstrap.ensure_language(
        _plan(plugin_dir, tmp_path, "battlenet"),
    )

    written = (prefix / "user.reg").read_text(encoding="utf-8")
    assert '"Locale"="00000407"' in written
    assert '"LocaleName"="de-DE"' in written
    assert '"sLanguage"="DEU"' in written
    assert '"sCountry"="Germany"' in written
