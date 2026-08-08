"""compatdata bridge: linking, pruning, and the never-delete-user-data guards.

The bridge is what makes Unifideck prefixes visible to Protontricks, which
resolves a non-Steam shortcut's prefix *only* at
``steamapps/compatdata/<appid>/pfx``. Its risky half is deletion: the same
appid range holds prefixes for the user's own non-Steam shortcuts, and a
sloppy prune would wipe a live prefix. Those guards are what most of these
cases pin down.

Every test redirects ``PREFIX_ROOT`` at a tmp_path — the module constant
points at the real data dir.
"""
from __future__ import annotations

import pytest

from unifideck.core import compat_bridge


@pytest.fixture
def env(tmp_path, monkeypatch):
    """``(prefix_root, steam_root)`` with the module constant redirected."""
    prefix_root = tmp_path / "prefixes"
    steam_root = tmp_path / "steam"
    prefix_root.mkdir()
    (steam_root / "steamapps" / "compatdata").mkdir(parents=True)
    monkeypatch.setattr(compat_bridge, "PREFIX_ROOT", prefix_root)
    return prefix_root, steam_root


def make_prefix(prefix_root, name: str):
    """A prefix shaped the way umu leaves one: ``pfx -> .`` plus ``pfx.lock``."""
    prefix = prefix_root / name
    prefix.mkdir(parents=True)
    (prefix / "pfx").symlink_to(".", target_is_directory=True)
    (prefix / "pfx.lock").touch()
    return prefix


def test_link_creates_then_is_idempotent(env):
    prefix_root, steam_root = env
    prefix = make_prefix(prefix_root, "Sugar")

    assert compat_bridge.link_prefix(prefix, 123, steam_root) == "created"
    assert compat_bridge.link_prefix(prefix, 123, steam_root) == "noop"

    link = compat_bridge.compatdata_link(steam_root, 123)
    assert link.is_symlink() and link.resolve() == prefix.resolve()


def test_link_satisfies_protontricks_gates(env):
    """The two checks protontricks actually applies must both pass."""
    prefix_root, steam_root = env
    prefix = make_prefix(prefix_root, "Sugar")
    compat_bridge.link_prefix(prefix, 123, steam_root)

    pfx = compat_bridge.compatdata_link(steam_root, 123) / "pfx"
    assert pfx.is_dir()
    assert (pfx.parent / "pfx.lock").is_file()


def test_signed_appid_is_normalised_to_unsigned(env):
    """games.map stores a signed int; Steam names the dir with the unsigned one."""
    prefix_root, steam_root = env
    prefix = make_prefix(prefix_root, "Sugar")
    compat_bridge.link_prefix(prefix, -487067706, steam_root)

    assert (steam_root / "steamapps/compatdata/3807899590").is_symlink()


def test_link_repoints_a_stale_bridge(env):
    prefix_root, steam_root = env
    old = make_prefix(prefix_root, "old")
    new = make_prefix(prefix_root, "new")

    compat_bridge.link_prefix(old, 123, steam_root)
    assert compat_bridge.link_prefix(new, 123, steam_root) == "repointed"
    assert compat_bridge.compatdata_link(steam_root, 123).resolve() == new.resolve()


def test_link_displaces_a_real_dir_instead_of_deleting_it(env):
    """A real Steam prefix on our appid is renamed aside — never removed."""
    prefix_root, steam_root = env
    prefix = make_prefix(prefix_root, "Sugar")
    squatter = steam_root / "steamapps/compatdata/123"
    squatter.mkdir()
    (squatter / "precious.reg").write_text("keep me")

    assert compat_bridge.link_prefix(prefix, 123, steam_root) == "displaced"

    moved = squatter.with_name("123" + compat_bridge.DISPLACED_SUFFIX)
    assert (moved / "precious.reg").read_text() == "keep me"
    assert compat_bridge.compatdata_link(steam_root, 123).resolve() == prefix.resolve()


def test_link_skips_when_prefix_absent_or_appid_missing(env):
    prefix_root, steam_root = env
    assert compat_bridge.link_prefix(prefix_root / "nope", 1, steam_root) == "skipped"
    assert compat_bridge.link_prefix(prefix_root, None, steam_root) == "skipped"
    assert compat_bridge.link_prefix(prefix_root, 1, None) == "skipped"


def test_prune_removes_only_dangling_bridges(env):
    prefix_root, steam_root = env
    alive = make_prefix(prefix_root, "alive")
    doomed = make_prefix(prefix_root, "doomed")
    compat_bridge.link_prefix(alive, 111, steam_root)
    compat_bridge.link_prefix(doomed, 222, steam_root)

    for child in doomed.iterdir():
        child.unlink()
    doomed.rmdir()

    assert compat_bridge.prune_dead_bridges(steam_root) == 1
    assert compat_bridge.compatdata_link(steam_root, 111).is_symlink()
    assert not compat_bridge.compatdata_link(steam_root, 222).is_symlink()


def test_prune_leaves_real_dirs_and_foreign_symlinks_alone(env, tmp_path):
    """The user's own prefixes live in the same appid range — never touch them."""
    _, steam_root = env
    compatdata = steam_root / "steamapps" / "compatdata"
    real = compatdata / "2936399003"
    real.mkdir()
    (real / "user.reg").write_text("user data")
    foreign = compatdata / "2407186659"
    foreign.symlink_to(tmp_path / "somewhere-else", target_is_directory=True)

    assert compat_bridge.prune_dead_bridges(steam_root) == 0
    assert (real / "user.reg").is_file()
    assert foreign.is_symlink()


def test_unlink_refuses_real_dirs_and_foreign_symlinks(env, tmp_path):
    _, steam_root = env
    compatdata = steam_root / "steamapps" / "compatdata"
    real = compatdata / "555"
    real.mkdir()
    (real / "user.reg").write_text("user data")
    foreign = compatdata / "666"
    foreign.symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    assert compat_bridge.unlink_prefix(555, steam_root) is False
    assert (real / "user.reg").is_file()
    assert compat_bridge.unlink_prefix(666, steam_root) is False
    assert foreign.is_symlink()


def test_unlink_removes_our_own_bridge(env):
    prefix_root, steam_root = env
    prefix = make_prefix(prefix_root, "Sugar")
    compat_bridge.link_prefix(prefix, 123, steam_root)

    assert compat_bridge.unlink_prefix(123, steam_root) is True
    assert not compat_bridge.compatdata_link(steam_root, 123).is_symlink()
    assert prefix.is_dir()  # the prefix itself survives


def test_unlink_is_idempotent_when_nothing_is_there(env):
    _, steam_root = env
    assert compat_bridge.unlink_prefix(999, steam_root) is True


# ── prefixes outside PREFIX_ROOT (user-picked storage bases) ──────


def test_ubisoft_prefix_on_another_base_counts_as_ours(env, tmp_path):
    """``~/Games/prefixes/ubisoft/80`` is ours even though it isn't under the root.

    Ubisoft records a per-game prefix under whatever install base the user
    picked, so ``PREFIX_ROOT`` alone never matched one — leaving those bridges
    unprunable and a dangling link outliving the prefix forever.
    """
    _, steam_root = env
    prefix = tmp_path / "Games" / "prefixes" / "ubisoft" / "80"
    prefix.mkdir(parents=True)

    assert compat_bridge.link_prefix(prefix, 3124767362, steam_root) == "created"
    assert compat_bridge.unlink_prefix(3124767362, steam_root) is True
    assert prefix.is_dir()  # only the bridge went


def test_dangling_ubisoft_bridge_is_prunable(env, tmp_path):
    """The case that motivated this: the prefix is gone, the link must follow."""
    _, steam_root = env
    prefix = tmp_path / "Games" / "prefixes" / "ubisoft" / "80"
    prefix.mkdir(parents=True)
    compat_bridge.link_prefix(prefix, 3124767362, steam_root)
    prefix.rmdir()

    assert compat_bridge.prune_dead_bridges(steam_root) == 1


def test_a_dir_deeper_inside_a_prefixes_tree_is_not_ours(env, tmp_path):
    """The match is the prefix itself, not anything nested under one."""
    _, steam_root = env
    inner = tmp_path / "Games" / "prefixes" / "ubisoft" / "80" / "drive_c" / "users"
    inner.mkdir(parents=True)
    link = steam_root / "steamapps" / "compatdata" / "777"
    link.symlink_to(inner, target_is_directory=True)

    assert compat_bridge.unlink_prefix(777, steam_root) is False
    assert link.is_symlink()
