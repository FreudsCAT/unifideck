"""compat-tool bridge: making our Proton visible to a sandboxed Protontricks.

``compat_bridge`` gets Protontricks to the *prefix*; this bridge gets it to
the *Proton*. Without it, a distro-packaged tool under
``/usr/share/steam/compatibilitytools.d`` — CachyOS's ``proton-cachyos``, the
exact case in the field report — is invisible to the Protontricks Flatpak
(Flatpak ignores filesystem grants under ``/usr``), so it fails with "Active
Proton installation could not be found automatically".

Two behaviours carry the design and are pinned here:

* the link registers the tool under the name in the **target's own**
  ``compatibilitytool.vdf``, not the link's name — which is why the link may
  carry an ownership-proving prefix without breaking the name match Steam's
  config requires;
* a tool already inside the sandbox's allowlist (``~/.steam``,
  ``~/.local/share/Steam`` — the GE-Proton/ProtonUp-Qt case) is skipped, so
  no tool is ever registered twice.

Every test redirects ``BRIDGE_ROOT`` at a tmp_path — the module constant
points at the real data dir.
"""
from __future__ import annotations

import pytest

from unifideck.core import compat_tool_bridge as ctb

TOOL_NAME = "proton-cachyos-11.0-20260703-slr-x86_64"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """``(bridge_root, system_dir)`` with the module constant redirected."""
    bridge_root = tmp_path / "protontricks-tools"
    system_dir = tmp_path / "usr-share-steam" / "compatibilitytools.d"
    system_dir.mkdir(parents=True)
    monkeypatch.setattr(ctb, "BRIDGE_ROOT", bridge_root)
    return bridge_root, system_dir


def make_tool(parent, name: str, internal_name: str = TOOL_NAME):
    """A compat tool shaped the way a distro package installs one."""
    tool = parent / name
    tool.mkdir(parents=True)
    (tool / ctb.TOOL_MANIFEST).write_text(
        '"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
        f'    "{internal_name}"\n'
        '    {\n      "install_path" "."\n'
        '      "from_oslist"  "windows"\n      "to_oslist"    "linux"\n'
        "    }\n  }\n}\n",
        encoding="utf-8",
    )
    (tool / "proton").touch()
    return tool


# --------------------------------------------------------------------------
# link_tool — the happy path
# --------------------------------------------------------------------------
def test_link_creates_then_is_idempotent(env):
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")

    assert ctb.link_tool(tool, TOOL_NAME) == "created"
    assert ctb.link_tool(tool, TOOL_NAME) == "noop"

    link = bridge_root / ctb.link_name(TOOL_NAME)
    assert link.is_symlink() and link.resolve() == tool.resolve()


def test_link_satisfies_protontricks_discovery(env):
    """The globs Protontricks actually applies must both resolve.

    ``get_custom_compat_tool_installations_in_dir`` globs
    ``*/compatibilitytool.vdf`` and then treats ``install_path "."`` as the
    manifest's parent — so the manifest must be reachable *through* the link.
    """
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    ctb.link_tool(tool, TOOL_NAME)

    found = list(bridge_root.glob(f"*/{ctb.TOOL_MANIFEST}"))
    assert len(found) == 1
    assert found[0].parent.resolve() == tool.resolve()
    assert (found[0].parent / "proton").is_file()


def test_internal_name_comes_from_the_target_not_the_link(env):
    """The ownership prefix must not change the name Protontricks registers.

    Protontricks reads the internal name from the manifest's ``compat_tools``
    key. If it used the directory name instead, the ``unifideck-bridge-``
    prefix would break the match against Steam's ``CompatToolMapping`` and
    the bridge would be worse than useless.
    """
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    ctb.link_tool(tool, TOOL_NAME)

    link = bridge_root / ctb.link_name(TOOL_NAME)
    assert link.name != TOOL_NAME
    assert link.name.startswith(ctb.LINK_PREFIX)
    assert f'"{TOOL_NAME}"' in (link / ctb.TOOL_MANIFEST).read_text()


def test_repoints_a_stale_link(env):
    bridge_root, system_dir = env
    old = make_tool(system_dir, "proton-cachyos-old")
    new = make_tool(system_dir, "proton-cachyos-new")

    assert ctb.link_tool(old, TOOL_NAME) == "created"
    assert ctb.link_tool(new, TOOL_NAME) == "repointed"

    link = bridge_root / ctb.link_name(TOOL_NAME)
    assert link.resolve() == new.resolve()


def test_link_name_neutralises_path_separators(env):
    """A tool name is a Steam config value, not a trusted filename."""
    _, system_dir = env
    tool = make_tool(system_dir, "odd")
    assert ctb.link_tool(tool, "../escape") == "created"
    assert "/" not in ctb.link_name("../escape")


# --------------------------------------------------------------------------
# link_tool — the skip gates
# --------------------------------------------------------------------------
def test_tool_already_visible_to_the_sandbox_is_skipped(env, tmp_path, monkeypatch):
    """GE-Proton under ~/.steam needs no bridge — and must not get one.

    Bridging it would register the same internal name a second time.
    """
    bridge_root, _ = env
    visible_root = tmp_path / "home-steam"
    tool = make_tool(visible_root / "compatibilitytools.d", "GE-Proton11-3")
    monkeypatch.setattr(
        ctb, "_SANDBOX_VISIBLE_ROOTS", (str(visible_root),),
    )

    assert ctb.link_tool(tool, "GE-Proton11-3") == "skipped"
    assert not bridge_root.exists()


def test_official_proton_without_a_manifest_is_skipped(env):
    """Valve's Protons ship only a toolmanifest.vdf — no internal name."""
    bridge_root, system_dir = env
    tool = system_dir / "Proton 11.0"
    tool.mkdir(parents=True)
    (tool / "toolmanifest.vdf").touch()
    (tool / "proton").touch()

    assert ctb.link_tool(tool, "proton_11") == "skipped"
    assert not bridge_root.exists()


def test_link_name_defaults_to_the_directory_name(env):
    """A missing tool name must not block the bridge.

    ``RuntimeState.proton_tool_id`` can be unset on a recovery path, and the
    name is cosmetic anyway — the registered name comes from the manifest.
    """
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")

    assert ctb.link_tool(tool) == "created"
    link = bridge_root / ctb.link_name("proton-cachyos")
    assert link.is_symlink() and link.resolve() == tool.resolve()
    assert f'"{TOOL_NAME}"' in (link / ctb.TOOL_MANIFEST).read_text()


@pytest.mark.parametrize(
    ("tool_dir", "tool_name"),
    [(None, TOOL_NAME), (None, None), ("", TOOL_NAME)],
)
def test_missing_inputs_are_skipped(env, tool_dir, tool_name):
    assert ctb.link_tool(tool_dir, tool_name) == "skipped"


def test_absent_tool_dir_is_skipped(env):
    _, system_dir = env
    assert ctb.link_tool(system_dir / "gone", TOOL_NAME) == "skipped"


# --------------------------------------------------------------------------
# Never touch what isn't ours
# --------------------------------------------------------------------------
def test_real_directory_in_the_way_is_left_alone(env):
    """Absolute rule: a real directory is never displaced or deleted."""
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    squatter = bridge_root / ctb.link_name(TOOL_NAME)
    squatter.mkdir(parents=True)
    (squatter / "user-data").write_text("keep me", encoding="utf-8")

    assert ctb.link_tool(tool, TOOL_NAME) == "failed"
    assert (squatter / "user-data").read_text() == "keep me"


def test_prune_removes_only_dangling_links_we_own(env):
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    ctb.link_tool(tool, TOOL_NAME)

    # A live link, a foreign symlink, and a real directory must all survive.
    bridge_root.joinpath("someone-elses-link").symlink_to(system_dir / "gone")
    bridge_root.joinpath("real-dir").mkdir()
    assert ctb.prune_dead_links() == 0

    # Now the tool disappears (package upgrade renamed the versioned dir).
    (tool / ctb.TOOL_MANIFEST).unlink()
    (tool / "proton").unlink()
    tool.rmdir()

    assert ctb.prune_dead_links() == 1
    assert not (bridge_root / ctb.link_name(TOOL_NAME)).exists()
    assert bridge_root.joinpath("someone-elses-link").is_symlink()
    assert bridge_root.joinpath("real-dir").is_dir()


def test_prune_on_missing_root_is_a_noop(env):
    assert ctb.prune_dead_links() == 0


# --------------------------------------------------------------------------
# bridged_links — what the support bundle reports
# --------------------------------------------------------------------------
def test_bridged_links_reports_resolvability(env):
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    ctb.link_tool(tool, TOOL_NAME)
    bridge_root.joinpath("real-dir").mkdir()

    rows = ctb.bridged_links()
    assert len(rows) == 1
    assert rows[0]["link"] == ctb.link_name(TOOL_NAME)
    assert rows[0]["target"] == str(tool)
    assert rows[0]["target_exists"] is True
    assert rows[0]["has_manifest"] is True


def test_bridged_links_flags_a_dangling_link(env):
    bridge_root, system_dir = env
    tool = make_tool(system_dir, "proton-cachyos")
    ctb.link_tool(tool, TOOL_NAME)
    (tool / ctb.TOOL_MANIFEST).unlink()
    (tool / "proton").unlink()
    tool.rmdir()

    rows = ctb.bridged_links()
    assert rows[0]["target_exists"] is False
    assert rows[0]["has_manifest"] is False


def test_bridged_links_on_missing_root_is_empty(env):
    assert ctb.bridged_links() == []
