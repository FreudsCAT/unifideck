"""Host Vulkan ICD inventory — is a 32-bit driver actually installed?

py_modules/unifideck/utils/vulkan.py

The Battle.net client is PE32 i386, so its Wine process needs a 32-bit
Vulkan driver on the host. Proton cannot supply one: pressure-vessel
*symlinks* the host's, and the Steam runtime image ships none::

    steamrt4/…/overrides/lib/i386-linux-gnu/vulkan/libvulkan_radeon.so
        -> /run/host/usr/lib32/libvulkan_radeon.so

So the question has to be answered against the host, and answered
**correctly** — the first attempt guessed from ICD *filenames* (a
``*.json`` containing ``i686`` or ``32``) and told a CachyOS user with a
working driver that they had none. Steam's own ``shader_log.txt`` on that
machine, 42 seconds before ours said otherwise::

    [21:51:07] Detected 32-bit RADV Vulkan driver AMD RADV PHOENIX / 733563a5…

Filenames cannot answer this. NVIDIA ships one ``nvidia_icd.json`` covering
both word sizes, distros place ICDs in any of the loader's search
directories, and ``VK_DRIVER_FILES`` can move them anywhere at all. So this
module reads the ICD manifests the loader would read and asks each driver
library what it *is*, via the one byte in the ELF header that says so.

Three verdicts, not two. "I could not tell" is a real answer and must not
be confused with "no": the caller's job is to avoid blocking on ignorance.

Stdlib only, never raises. Imported from the out-of-process launcher under
the SYSTEM python (3.10-3.14) as well as from the support bundle.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# e_ident[EI_CLASS]: 1 = ELFCLASS32, 2 = ELFCLASS64.
_ELF_MAGIC = b"\x7fELF"
_ELFCLASS32 = 1
_ELFCLASS64 = 2

# Where a bare soname (``libvulkan_radeon.so`` with no directory) may live.
# 32-bit first: this module exists to find those.
_LIB_DIRS = (
    "/usr/lib32",
    "/usr/lib/i386-linux-gnu",
    "/usr/lib",
    "/usr/lib64",
    "/usr/lib/x86_64-linux-gnu",
)

# Last resort when a manifest's library cannot be resolved on disk. This is
# the heuristic that caused the bug, kept only as a per-entry fallback so
# hosts it *does* work on (SteamOS: ``radeon_icd.i686.json``) never regress.
_NAME_HINTS_32 = ("i686", "i386", "32")


class Vulkan32(Enum):
    """Whether the host can run a 32-bit Vulkan client."""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IcdRecord:
    """One ICD manifest and what its driver library turned out to be."""

    manifest: str
    library_path: str = ""
    resolved: str = ""
    elf_class: int = 0
    name_hint_32: bool = False

    @property
    def is_32bit(self) -> bool:
        if self.elf_class:
            return self.elf_class == _ELFCLASS32
        return self.name_hint_32

    @property
    def decided_by_elf(self) -> bool:
        return bool(self.elf_class)


def _word_size(icd: IcdRecord) -> str:
    """``32`` / ``64`` when the ELF said so, ``32?`` / ``??`` when it did not."""
    if icd.decided_by_elf:
        return "32" if icd.is_32bit else "64"
    return "32?" if icd.name_hint_32 else "??"


@dataclass(frozen=True, slots=True)
class Vulkan32Report:
    """The verdict plus the evidence, so a log line can explain itself."""

    verdict: Vulkan32
    dirs: list[str] = field(default_factory=list)
    icds: list[IcdRecord] = field(default_factory=list)

    def summary(self) -> str:
        if not self.icds:
            return f"{self.verdict.value} (no ICD manifests in {len(self.dirs)} dir(s))"
        found = ", ".join(f"{Path(icd.manifest).name}/{_word_size(icd)}" for icd in self.icds)
        return f"{self.verdict.value} ({len(self.icds)} ICD(s): {found})"


def _env_paths(name: str) -> list[Path]:
    raw = os.environ.get(name, "")
    return [Path(part) for part in raw.split(":") if part]


def explicit_icd_files() -> list[Path]:
    """Manifests named directly by the loader's override variables.

    ``VK_DRIVER_FILES`` (and its deprecated alias ``VK_ICD_FILENAMES``)
    replace the search path entirely rather than adding to it, which is
    exactly how a host with drivers installed can still have none visible.
    """
    files = _env_paths("VK_DRIVER_FILES") or _env_paths("VK_ICD_FILENAMES")
    return [path for path in files if path.suffix == ".json"]


def icd_search_dirs() -> list[Path]:
    """The loader's ``vulkan/icd.d`` directories, in loader order.

    Mirrors the Vulkan loader's documented Linux search path rather than
    the three fixed directories this used to check.
    """
    home = Path.home()
    roots: list[Path] = []
    roots.extend(_env_paths("XDG_CONFIG_HOME") or [home / ".config"])
    roots.extend(_env_paths("XDG_CONFIG_DIRS") or [Path("/etc/xdg")])
    roots.extend((Path("/etc"), Path("/usr/local/etc")))
    roots.extend(_env_paths("XDG_DATA_HOME") or [home / ".local" / "share"])
    roots.extend(_env_paths("XDG_DATA_DIRS") or [])
    roots.extend((Path("/usr/local/share"), Path("/usr/share")))

    dirs: list[Path] = []
    for root in roots:
        candidate = root / "vulkan" / "icd.d"
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _manifest_files(dirs: list[Path]) -> list[Path]:
    """Manifests to read: the override list if set, else the search path.

    ``VK_DRIVER_FILES`` *replaces* the search path rather than extending
    it, so honouring it means ignoring the directories entirely — that is
    precisely the configuration in which a host with drivers installed
    still presents none to the loader.
    """
    explicit = explicit_icd_files()
    if explicit:
        return explicit
    files: list[Path] = []
    for directory in dirs:
        try:
            entries = sorted(directory.glob("*.json"))
        except OSError:
            continue
        files.extend(entry for entry in entries if entry not in files)
    return files


def _library_path(manifest: Path) -> str:
    """``ICD.library_path`` from a manifest, or "" if it has none."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    icd = data.get("ICD") if isinstance(data, dict) else None
    library = icd.get("library_path") if isinstance(icd, dict) else None
    return library if isinstance(library, str) else ""


def _resolve_library(manifest: Path, library: str) -> Path | None:
    """Locate the driver ``.so`` a manifest points at.

    ``library_path`` is either absolute, relative to the manifest, or a
    bare soname left to the dynamic linker — all three appear in the wild.
    """
    if not library:
        return None
    candidate = Path(library)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    if "/" in library:
        relative = manifest.parent / candidate
        return relative if relative.is_file() else None
    for lib_dir in _LIB_DIRS:
        found = Path(lib_dir) / library
        if found.is_file():
            return found
    return None


def elf_class(path: Path) -> int:
    """``1`` for a 32-bit ELF, ``2`` for 64-bit, ``0`` if it is not one."""
    try:
        with path.open("rb") as handle:
            header = handle.read(5)
    except OSError:
        return 0
    if len(header) < 5 or header[:4] != _ELF_MAGIC:
        return 0
    return header[4] if header[4] in (_ELFCLASS32, _ELFCLASS64) else 0


def _record(manifest: Path) -> IcdRecord:
    library = _library_path(manifest)
    resolved = _resolve_library(manifest, library)
    return IcdRecord(
        manifest=str(manifest),
        library_path=library,
        resolved=str(resolved) if resolved else "",
        elf_class=elf_class(resolved) if resolved else 0,
        name_hint_32=any(hint in manifest.name for hint in _NAME_HINTS_32),
    )


def scan_icds() -> list[IcdRecord]:
    """Every ICD manifest the loader would see, with its driver's word size."""
    return [_record(manifest) for manifest in _manifest_files(icd_search_dirs())]


def detect_32bit_vulkan() -> Vulkan32Report:
    """Whether a 32-bit Vulkan driver is installed. Never raises.

    ``UNKNOWN`` when no manifest could be read *or* none of them resolved
    to a library we could classify — the caller must treat that as "carry
    on", never as "no". Refusing to install because we could not tell is
    the failure this module was written to end.
    """
    dirs = [str(path) for path in icd_search_dirs()]
    try:
        icds = scan_icds()
    except OSError:
        logger.warning("[vulkan] ICD scan failed — reporting unknown", exc_info=True)
        return Vulkan32Report(Vulkan32.UNKNOWN, dirs, [])

    if any(icd.is_32bit for icd in icds):
        verdict = Vulkan32.PRESENT
    elif any(icd.decided_by_elf for icd in icds):
        verdict = Vulkan32.ABSENT
    else:
        verdict = Vulkan32.UNKNOWN
    return Vulkan32Report(verdict, dirs, icds)


def as_dict(report: Vulkan32Report) -> dict[str, object]:
    """JSON-ready form for the support bundle's environment report."""
    has_32bit: bool | None = None
    if report.verdict is Vulkan32.PRESENT:
        has_32bit = True
    elif report.verdict is Vulkan32.ABSENT:
        has_32bit = False
    return {
        "verdict": report.verdict.value,
        "has_32bit": has_32bit,
        "dirs": report.dirs,
        "icds": [
            {
                "manifest": icd.manifest,
                "library_path": icd.library_path,
                "resolved": icd.resolved,
                "elf_class": icd.elf_class,
                "is_32bit": icd.is_32bit,
                "decided_by": "elf" if icd.decided_by_elf else "filename",
            }
            for icd in report.icds
        ],
    }
