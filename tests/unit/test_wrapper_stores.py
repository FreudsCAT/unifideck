"""The wrapper-store predicates, and the invariant they exist to protect.

A *wrapper store* runs a vendor Windows client inside the prefix. The
structural consequence is that the game's files live inside the prefix, so
a prefix reset destroys user data rather than costing a rebuild.

This module exists because that question used to be asked as a bare
``store == "ubisoft"`` in five places, and on 2026-08-01 two of them
disagreed: ``prefix_setup`` borrowed managed GE-Proton for a winetricks
verb, ``prefix_init`` saw the Proton family change and wiped the prefix,
and Rayman Origins was deleted. The borrow was for a step
``apply_prefix_compat`` skips for Ubisoft anyway.

The tests below therefore assert two different things: that the predicates
behave, and that the real call sites actually route through them.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from unifideck.launcher import wrapper_stores as ws

REPO = Path(__file__).parent.parent.parent
LAUNCHER = REPO / "py_modules/unifideck/launcher"
PROTON = LAUNCHER / "proton"


@pytest.mark.parametrize("store", sorted(ws.WRAPPER_STORES))
def test_wrapper_stores_own_their_installs_and_skip_generic_compat(store: str) -> None:
    assert ws.is_wrapper_store(store)
    assert ws.prefix_owns_game_install(store)
    assert ws.skips_generic_compat(store)
    assert ws.uses_manual_download_phase(store)


@pytest.mark.parametrize("store", ["epic", "gog", "amazon", "microsoft", "steam"])
def test_non_wrapper_stores_are_excluded(store: str) -> None:
    """A false positive here means we skip a reset a store genuinely needs."""
    assert not ws.is_wrapper_store(store)
    assert not ws.prefix_owns_game_install(store)
    assert not ws.skips_generic_compat(store)
    assert not ws.uses_manual_download_phase(store)


@pytest.mark.parametrize("store", [None, "", "  ", "UBISOFT", "Battlenet"])
def test_unknown_or_miscased_values_are_false(store: str | None) -> None:
    """Store ids are lowercase everywhere; never match loosely."""
    assert not ws.is_wrapper_store(store)
    assert not ws.prefix_owns_game_install(store)


def test_battlenet_and_ubisoft_are_both_wrapper_stores() -> None:
    assert {"ubisoft", "battlenet"} <= ws.WRAPPER_STORES


def test_predicates_are_separate_functions_not_one_alias() -> None:
    """They answer different questions and are expected to diverge.

    EA App installs some titles to Program Files *outside* the prefix, so it
    would be a wrapper store that does not own its installs.
    """
    names = {"is_wrapper_store", "prefix_owns_game_install", "skips_generic_compat"}
    assert names <= set(dir(ws))
    assert len({id(getattr(ws, n)) for n in names}) == len(names)


def test_module_is_stdlib_only() -> None:
    """Imported from the launcher, which runs under system Python 3.10-3.14."""
    source = (LAUNCHER / "wrapper_stores.py").read_text()
    imports = [
        line for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    ]
    assert imports == []


# --------------------------------------------------------------------------
# the call sites must actually route through the predicates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "predicate"),
    [
        pytest.param("compat/prefix_init.py", "prefix_owns_game_install", id="prefix-reset-guard"),
        pytest.param("prefix_setup.py", "prefix_owns_game_install", id="prefix-setup-guard"),
        pytest.param("compat/__init__.py", "skips_generic_compat", id="generic-compat-skip"),
    ],
)
def test_call_sites_use_the_shared_predicate(relative: str, predicate: str) -> None:
    source = (PROTON / relative).read_text()
    assert predicate in source, f"{relative} no longer routes through {predicate}"
    assert '== "ubisoft"' not in source, (
        f"{relative} reintroduced a bare store comparison — that divergence "
        f"is what deleted a user's game on 2026-08-01"
    )


def test_prefix_init_guard_returns_true_for_every_wrapper_store() -> None:
    """The actual guard, exercised — not just its source text."""
    from unifideck.launcher.proton.compat.prefix_init import _prefix_owns_game_install

    class _Ctx:
        def __init__(self, store: str) -> None:
            self.store = store

    class _Plan:
        def __init__(self, store: str) -> None:
            self.context = _Ctx(store)

    for store in ws.WRAPPER_STORES:
        assert _prefix_owns_game_install(_Plan(store)) is True
    for store in ("epic", "gog", "amazon"):
        assert _prefix_owns_game_install(_Plan(store)) is False


def test_guard_tolerates_a_context_without_a_store_attribute() -> None:
    from unifideck.launcher.proton.compat.prefix_init import _prefix_owns_game_install

    class _Plan:
        context = object()

    assert _prefix_owns_game_install(_Plan()) is False


def test_predicates_accept_a_plain_string_not_a_context() -> None:
    """Keeps them usable from services/, which has no LaunchContext."""
    for fn in (ws.is_wrapper_store, ws.prefix_owns_game_install,
               ws.skips_generic_compat, ws.uses_manual_download_phase):
        assert inspect.signature(fn).parameters.keys() == {"store"}
