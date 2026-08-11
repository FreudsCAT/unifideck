"""Host 32-bit Vulkan detection: verdict by ELF class, never by filename.

The bug these pin: a filename scan (``*.json`` containing ``i686`` or
``32``) reported "no 32-bit Vulkan" on a CachyOS machine whose driver Steam
had enumerated seconds earlier, and that false negative refused the user
the Battle.net client entirely. So the shape that matters is not just
"finds a 32-bit driver" but *how* — a layout with no telltale filename must
still come out ``PRESENT``, and a probe that cannot tell must say
``UNKNOWN`` rather than ``ABSENT``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.utils import vulkan

# Real ELF headers: magic + EI_CLASS. Five bytes is all the probe reads.
_ELF32 = b"\x7fELF\x01" + b"\x00" * 32
_ELF64 = b"\x7fELF\x02" + b"\x00" * 32


def _icd(directory: Path, name: str, library: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / name
    manifest.write_text(
        '{"file_format_version": "1.0.0", "ICD": {"library_path": "%s", '
        '"api_version": "1.3.0"}}' % library,
        encoding="utf-8",
    )
    return manifest


def _isolate(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point every loader search root at ``root`` and nothing else."""
    for var in ("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS", "XDG_DATA_HOME", "XDG_DATA_DIRS"):
        monkeypatch.setenv(var, str(root))
    monkeypatch.delenv("VK_DRIVER_FILES", raising=False)
    monkeypatch.delenv("VK_ICD_FILENAMES", raising=False)
    # The loader's fixed roots are always searched; keep them out of the way.
    monkeypatch.setattr(
        vulkan, "icd_search_dirs", lambda: [root / "vulkan" / "icd.d"],
    )


# --------------------------------------------------------------------------
# ELF class beats the filename
# --------------------------------------------------------------------------


def test_a_32bit_driver_is_found_with_no_hint_in_the_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact regression: the old filename scan would have said ABSENT."""
    icd_dir = tmp_path / "vulkan" / "icd.d"
    (tmp_path / "drivers").mkdir(parents=True)
    (tmp_path / "drivers" / "libvulkan_vendor.so").write_bytes(_ELF32)
    _icd(icd_dir, "vendor_icd.json", str(tmp_path / "drivers" / "libvulkan_vendor.so"))
    _isolate(monkeypatch, tmp_path)

    report = vulkan.detect_32bit_vulkan()

    assert report.verdict is vulkan.Vulkan32.PRESENT
    assert report.icds[0].decided_by_elf


def test_an_i686_filename_over_a_64bit_library_is_not_believed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filename lies here; the ELF header does not."""
    icd_dir = tmp_path / "vulkan" / "icd.d"
    (tmp_path / "drivers").mkdir(parents=True)
    (tmp_path / "drivers" / "lib64.so").write_bytes(_ELF64)
    _icd(icd_dir, "radeon_icd.i686.json", str(tmp_path / "drivers" / "lib64.so"))
    _isolate(monkeypatch, tmp_path)

    assert vulkan.detect_32bit_vulkan().verdict is vulkan.Vulkan32.ABSENT


def test_only_64bit_drivers_report_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    icd_dir = tmp_path / "vulkan" / "icd.d"
    (tmp_path / "drivers").mkdir(parents=True)
    (tmp_path / "drivers" / "lib64.so").write_bytes(_ELF64)
    _icd(icd_dir, "radeon_icd.x86_64.json", str(tmp_path / "drivers" / "lib64.so"))
    _isolate(monkeypatch, tmp_path)

    assert vulkan.detect_32bit_vulkan().verdict is vulkan.Vulkan32.ABSENT


# --------------------------------------------------------------------------
# "cannot tell" is its own answer
# --------------------------------------------------------------------------


def test_no_manifests_at_all_is_unknown_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate(monkeypatch, tmp_path)

    report = vulkan.detect_32bit_vulkan()

    assert report.verdict is vulkan.Vulkan32.UNKNOWN
    assert report.icds == []


def test_an_unresolvable_library_falls_back_to_the_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SteamOS-shaped names still work when the .so cannot be found."""
    icd_dir = tmp_path / "vulkan" / "icd.d"
    _icd(icd_dir, "radeon_icd.i686.json", "/nonexistent/libvulkan_radeon.so")
    _isolate(monkeypatch, tmp_path)

    report = vulkan.detect_32bit_vulkan()

    assert report.verdict is vulkan.Vulkan32.PRESENT
    assert not report.icds[0].decided_by_elf


def test_an_unresolvable_library_with_no_hint_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    icd_dir = tmp_path / "vulkan" / "icd.d"
    _icd(icd_dir, "nvidia_icd.json", "/nonexistent/libGLX_nvidia.so.0")
    _isolate(monkeypatch, tmp_path)

    assert vulkan.detect_32bit_vulkan().verdict is vulkan.Vulkan32.UNKNOWN


# --------------------------------------------------------------------------
# search path
# --------------------------------------------------------------------------


def test_vk_driver_files_replaces_the_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override semantics, not additive — a host with drivers can still
    present none to the loader, and we must see what the loader sees."""
    icd_dir = tmp_path / "vulkan" / "icd.d"
    (tmp_path / "drivers").mkdir(parents=True)
    (tmp_path / "drivers" / "lib32.so").write_bytes(_ELF32)
    (tmp_path / "drivers" / "lib64.so").write_bytes(_ELF64)
    _icd(icd_dir, "vendor_icd.i686.json", str(tmp_path / "drivers" / "lib32.so"))
    only64 = _icd(icd_dir, "vendor_icd.x86_64.json", str(tmp_path / "drivers" / "lib64.so"))
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VK_DRIVER_FILES", str(only64))

    report = vulkan.detect_32bit_vulkan()

    assert report.verdict is vulkan.Vulkan32.ABSENT
    assert len(report.icds) == 1


def test_a_bare_soname_is_resolved_against_the_lib_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lib32 = tmp_path / "lib32"
    lib32.mkdir()
    (lib32 / "libvulkan_radeon.so").write_bytes(_ELF32)
    icd_dir = tmp_path / "vulkan" / "icd.d"
    _icd(icd_dir, "radeon_icd.x86_64.json", "libvulkan_radeon.so")
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(vulkan, "_LIB_DIRS", (str(lib32),))

    assert vulkan.detect_32bit_vulkan().verdict is vulkan.Vulkan32.PRESENT


def test_a_malformed_manifest_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    icd_dir = tmp_path / "vulkan" / "icd.d"
    icd_dir.mkdir(parents=True)
    (icd_dir / "broken.json").write_text("{not json", encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    assert vulkan.detect_32bit_vulkan().verdict is vulkan.Vulkan32.UNKNOWN


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def test_as_dict_maps_the_verdict_to_a_tri_state_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` for UNKNOWN, so the bundle never renders a guess as a fact."""
    _isolate(monkeypatch, tmp_path)

    payload = vulkan.as_dict(vulkan.detect_32bit_vulkan())

    assert payload["has_32bit"] is None
    assert payload["verdict"] == "unknown"
