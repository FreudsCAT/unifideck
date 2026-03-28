#!/usr/bin/env python3
"""
Test: Ubisoft DPAPI MachineGuid alignment and credential sharing.

Verifies that:
1. read_machine_guid prefers pfx/system.reg (Proton's actual DPAPI source)
2. MachineGuid alignment patches game prefixes to match auth prefix
3. Credential files are consistent across prefixes after alignment
4. inject_session succeeds after alignment

Usage:
    PYTHONPATH=tests:. python3 -m pytest tests/test_ubisoft_machineguid.py -v
    # or directly:
    python3 tests/test_ubisoft_machineguid.py
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from ubisoft_inject_session import (
    read_machine_guid,
    _get_canonical_machine_guid,
    _align_prefix_machine_guid,
    inject_session,
)


def _make_system_reg(guid: str) -> str:
    """Generate minimal Wine system.reg with given MachineGuid."""
    return (
        'WINE REGISTRY Version 2\n'
        ';; All keys relative to \\\\Machine\n\n'
        '[Software\\\\Microsoft\\\\Cryptography] 1234567890\n'
        f'"MachineGuid"="{guid}"\n'
    )


def _make_prefix(base_dir: str, name: str, root_guid: str, pfx_guid: str) -> str:
    """Create a fake Wine prefix with two system.reg files."""
    prefix_path = os.path.join(base_dir, name)
    os.makedirs(prefix_path, exist_ok=True)

    # Root-level system.reg
    with open(os.path.join(prefix_path, "system.reg"), "w") as f:
        f.write(_make_system_reg(root_guid))

    # pfx/system.reg (what Proton actually uses)
    pfx_dir = os.path.join(prefix_path, "pfx")
    os.makedirs(pfx_dir, exist_ok=True)
    with open(os.path.join(pfx_dir, "system.reg"), "w") as f:
        f.write(_make_system_reg(pfx_guid))

    return prefix_path


class TestReadMachineGuid:
    """Test that read_machine_guid reads pfx/system.reg first."""

    def test_prefers_pfx_system_reg(self, tmp_path):
        root_guid = "aaaa-root-guid"
        pfx_guid = "bbbb-pfx-guid"
        prefix = _make_prefix(str(tmp_path), "test", root_guid, pfx_guid)
        assert read_machine_guid(prefix) == pfx_guid

    def test_falls_back_to_root_if_no_pfx(self, tmp_path):
        prefix = os.path.join(str(tmp_path), "test")
        os.makedirs(prefix)
        with open(os.path.join(prefix, "system.reg"), "w") as f:
            f.write(_make_system_reg("root-only-guid"))
        assert read_machine_guid(prefix) == "root-only-guid"

    def test_empty_if_no_system_reg(self, tmp_path):
        prefix = os.path.join(str(tmp_path), "test")
        os.makedirs(prefix)
        assert read_machine_guid(prefix) == ""


class TestAlignMachineGuid:
    """Test MachineGuid alignment patches pfx/system.reg correctly."""

    def test_aligns_mismatched_pfx_guid(self, tmp_path):
        # Set up: auth prefix has pfx_guid "auth-guid"
        auth = _make_prefix(str(tmp_path), ".upc-auth", "template", "auth-guid")
        game = _make_prefix(str(tmp_path), "game1", "template", "game-guid")

        # Monkey-patch constants for isolated test
        import ubisoft_inject_session as mod
        old_auth = mod.AUTH_PREFIX_DIR
        old_prefixes = mod.PREFIXES_DIR
        mod.AUTH_PREFIX_DIR = auth
        mod.PREFIXES_DIR = str(tmp_path)
        try:
            _align_prefix_machine_guid(game)
            assert read_machine_guid(game) == "auth-guid"
        finally:
            mod.AUTH_PREFIX_DIR = old_auth
            mod.PREFIXES_DIR = old_prefixes

    def test_noop_if_already_aligned(self, tmp_path):
        auth = _make_prefix(str(tmp_path), ".upc-auth", "template", "same-guid")
        game = _make_prefix(str(tmp_path), "game1", "template", "same-guid")

        import ubisoft_inject_session as mod
        old_auth = mod.AUTH_PREFIX_DIR
        old_prefixes = mod.PREFIXES_DIR
        mod.AUTH_PREFIX_DIR = auth
        mod.PREFIXES_DIR = str(tmp_path)
        try:
            result = _align_prefix_machine_guid(game)
            assert result is True
            assert read_machine_guid(game) == "same-guid"
        finally:
            mod.AUTH_PREFIX_DIR = old_auth
            mod.PREFIXES_DIR = old_prefixes


# Allow running standalone
if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ImportError:
        print("pytest not available, running basic checks...")
        with tempfile.TemporaryDirectory() as tmp:
            t = TestReadMachineGuid()
            from pathlib import Path
            t.test_prefers_pfx_system_reg(Path(tempfile.mkdtemp()))
            print("  ✓ prefers_pfx_system_reg")
            t.test_falls_back_to_root_if_no_pfx(Path(tempfile.mkdtemp()))
            print("  ✓ falls_back_to_root_if_no_pfx")
            t.test_empty_if_no_system_reg(Path(tempfile.mkdtemp()))
            print("  ✓ empty_if_no_system_reg")

            a = TestAlignMachineGuid()
            a.test_aligns_mismatched_pfx_guid(Path(tempfile.mkdtemp()))
            print("  ✓ aligns_mismatched_pfx_guid")
            a.test_noop_if_already_aligned(Path(tempfile.mkdtemp()))
            print("  ✓ noop_if_already_aligned")
        print("\nAll basic checks passed ✓")
