"""Regression: the Epic launch must use the language chosen in Unifideck.

Bug report: an Epic title that ships no language packs and takes its
language from whatever the Epic Games Launcher passes it always started
in English, even with Spanish selected in the Unifideck UI.

The chain is ``ui.locale`` (config.json) → ``_resolve_epic_language`` →
``legendary launch --language <xx>`` → ``-epiclocale=<xx>`` on the game's
command line (legendary/core.py ``get_launch_parameters``). It broke at
the second link: ``_resolve_epic_language`` imported
``resolve_user_config_path`` from ``unifideck.config``, but that name is
only exported by the ``unifideck.config.user_config_path`` submodule —
the package ``__all__`` does not re-export it. Every launch therefore
raised ImportError inside the ``try``, the blanket ``except Exception``
swallowed it, and the function returned its ``"en"`` fallback.

Because the failure was a swallowed exception, the tests below assert on
BOTH the returned value and the absence of the fallback log record: a
future refactor that silently breaks the import again would still return
"es"… no, it would return "en", and the log assertion pins down *why*.
"""
from __future__ import annotations

import json
import logging
import types
from pathlib import Path

import pytest

from unifideck.launcher.proton.compat import epic as compat_epic
from unifideck.launcher.proton.handlers.epic import (
    _build_legendary_argv,
    _resolve_epic_language,
)
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

REPO_ROOT = Path(__file__).resolve().parents[2]


def _plan(plugin_dir: Path) -> ProtonLaunchPlan:
    return ProtonLaunchPlan(
        context=types.SimpleNamespace(
            game_id="abc123", store="epic",
            exe_path=Path("/install/abc123.exe"),
            work_dir=Path("/install"),
            plugin_dir=plugin_dir,
        ),
        state=types.SimpleNamespace(wrappers=[], game_args=[], umu_id=None),
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        prefix_path=Path("/tmp/prefix"),  # noqa: S108
        env={},
        on_process_start=None,
    )


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A plugin dir carrying the real ``defaults/config.json``.

    ``get_unifideck_locale`` validates the saved tag against the
    ``i18n.locales`` catalogue, so the shipped defaults have to be the
    ones under test — a stub would make the test pass for the wrong
    reason.
    """
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "config.json").write_text(
        (REPO_ROOT / "defaults" / "config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "UNIFIDECK_USER_CONFIG", str(tmp_path / "user-config.json"),
    )
    return tmp_path


def _write_user_config(plugin_dir: Path, payload: dict) -> None:
    (plugin_dir / "user-config.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def test_resolves_the_language_saved_by_the_ui(plugin_dir, caplog):
    """``ui.locale`` is the key the frontend writes — it must be honoured."""
    _write_user_config(plugin_dir, {"ui": {"locale": "es-ES"}})

    with caplog.at_level(logging.ERROR):
        lang = _resolve_epic_language(_plan(plugin_dir))

    assert lang == "es"
    # The old bug produced the right-looking fallback for the wrong reason.
    assert "language resolution" not in caplog.text


def test_legacy_ui_language_key_still_works(plugin_dir):
    _write_user_config(plugin_dir, {"ui": {"language": "fr-FR"}})

    assert _resolve_epic_language(_plan(plugin_dir)) == "fr"


def test_auto_falls_through_instead_of_pinning_english(plugin_dir):
    """``auto`` is the frontend's "not chosen yet" sentinel, not a tag."""
    _write_user_config(plugin_dir, {"ui": {"locale": "auto"}})

    # No explicit choice → system/source fallback, never a crash.
    assert len(_resolve_epic_language(_plan(plugin_dir))) == 2


def test_argv_passes_the_resolved_language_to_legendary(
    plugin_dir, monkeypatch,
):
    """``--language es`` is what makes legendary emit ``-epiclocale=es``."""
    monkeypatch.setattr(compat_epic, "detect_offline", lambda: False)
    _write_user_config(plugin_dir, {"ui": {"locale": "es-ES"}})

    argv = _build_legendary_argv(_plan(plugin_dir), "/plugin/bin/legendary")

    assert argv[argv.index("--language") + 1] == "es"
