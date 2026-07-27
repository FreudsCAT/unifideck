"""Guard test — scripts/validate_event_schemas.py.

The event-schema validator is a CI gate: if it silently breaks
(stale schema, broken extraction, wrong exit code) a whole
class of event-contract regressions stops being caught. This
module pins its behaviour:

1. against the *real* repo source it must report success
   (exit 0) — i.e. CANONICAL_SCHEMA is in sync with the code;
2. every CANONICAL_SCHEMA key is a real Events enum member
   (the failure mode that made the original script useless:
   it declared AUTH_STARTED / ARTWORK_READY which no longer
   existed, so the comparison never matched them);
3. the three failure classes each flip the exit code to 1
   (unexpected kwarg, phantom schema key, emitted-but-
   undeclared), so the gate cannot pass while broken;
4. extraction noise is filtered — the priority dispatcher's
   ``bus.emit(item.event, ...)`` must NOT surface as a
   phantom event named ``"event"``.

Resolution of the repo root is robust (env var → walk up from
the unifideck package → known locations) because the suite
runs out-of-tree. If the script or repo can't be located the
test SKIPS rather than fails — a missing checkout is an
environment issue, not a regression. (Note: the strict CI
forbids skips, so in CI this never silently skips; locally it
degrades gracefully.)
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


# Files/dirs that only ever exist at a repo checkout root.
# Used to recognise the root by structure instead of by a
# hard-coded absolute path, so this works in CI, in a local
# clone, in a worktree, or wherever the suite is unpacked.
def _find_script() -> Path | None:
    """Locate scripts/validate_event_schemas.py via the
    shared, structurally-resolved repo root (no hard-coded
    paths)."""
    from tests.unit._repo_root import find_repo_file

    return find_repo_file("scripts/validate_event_schemas.py")


@pytest.fixture(scope="module")
def script_path() -> Path:
    p = _find_script()
    if p is None:
        pytest.skip(
            "scripts/validate_event_schemas.py not found "
            "(set UNIFIDECK_REPO_ROOT to the checkout root)")
    return p


@pytest.fixture(scope="module")
def script_module(script_path: Path):
    """Import the script as a module so its functions and
    CANONICAL_SCHEMA can be inspected directly."""
    spec = importlib.util.spec_from_file_location(
        "_ves_under_test", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120,
    )


# ========================================================= #
# 1. Clean run against the real source
# ========================================================= #
def test_passes_against_real_source(
    script_path: Path,
) -> None:
    """The committed CANONICAL_SCHEMA matches the codebase:
    exit 0 and the success marker on stdout."""
    res = _run(script_path)
    assert res.returncode == 0, (
        f"validator failed against real source:\n"
        f"{res.stdout}\n{res.stderr}")
    assert "event schemas valid" in res.stdout


# ========================================================= #
# 2. Every schema key is a real enum member
# ========================================================= #
def test_schema_keys_are_real_events(
    script_module,
) -> None:
    """No phantom keys (the AUTH_STARTED / ARTWORK_READY
    failure mode that silently neutered the old script)."""
    phantom = (
        set(script_module.CANONICAL_SCHEMA)
        - script_module.VALID_EVENTS
    )
    assert not phantom, (
        f"CANONICAL_SCHEMA has non-enum keys: "
        f"{sorted(phantom)}")
    assert script_module.validate_schema_keys() == 0


def _load_with_patched_schema(
    script_path: Path, old: str, new: str,
):
    """Import the script as a fresh module with one source
    substitution applied, WITHOUT changing its ROOT (so it
    still resolves the real py_modules/). Returns the module.
    """
    src = script_path.read_text()
    patched = src.replace(old, new)
    assert patched != src, "anchor not found — script changed?"
    import types

    mod = types.ModuleType("_ves_patched")
    mod.__file__ = str(script_path)  # keep ROOT resolution
    code = compile(patched, str(script_path), "exec")
    exec(code, mod.__dict__)  # noqa: S102 - trusted local file
    return mod


# ========================================================= #
# 3. The three failure classes each fail the gate
# ========================================================= #
def test_unexpected_kwarg_fails(
    script_path: Path,
) -> None:
    """Narrowing a declared contract below what is actually
    emitted must fail (main() returns 1)."""
    mod = _load_with_patched_schema(
        script_path,
        '"GAME_UNINSTALLED":             {"game_id", "store"},',
        '"GAME_UNINSTALLED":             {"game_id"},')
    assert mod.main() == 1


def test_phantom_schema_key_fails(
    script_path: Path,
) -> None:
    """A CANONICAL_SCHEMA key absent from the Events enum
    must fail fast (main() returns 1) via
    validate_schema_keys()."""
    mod = _load_with_patched_schema(
        script_path,
        '"ACCOUNT_SWITCHED":             {"active_user_id", "new_user"},',
        '"ACCOUNT_SWITCHED":             {"active_user_id", "new_user"},\n'
        '    "DEFINITELY_NOT_AN_EVENT":      {"x"},')
    assert "DEFINITELY_NOT_AN_EVENT" in mod.CANONICAL_SCHEMA
    assert mod.validate_schema_keys() == 1
    assert mod.main() == 1


def test_emitted_but_undeclared_fails(
    script_path: Path,
) -> None:
    """Removing a really-emitted event from the schema must
    surface it as emitted-but-undeclared (main() returns 1)."""
    mod = _load_with_patched_schema(
        script_path,
        '    "STORE_LOGOUT":                 {"store"},\n', "")
    assert "STORE_LOGOUT" not in mod.CANONICAL_SCHEMA
    assert mod.main() == 1


# ========================================================= #
# 4. Extraction noise is filtered
# ========================================================= #
def test_extraction_noise_is_filtered(
    script_module,
) -> None:
    """The priority dispatcher's ``bus.emit(item.event, ...)``
    resolves to the bogus name ``"event"``; walk_sources must
    drop it (and any other non-enum name) rather than report a
    phantom event."""
    target = (
        script_module.ROOT / "py_modules" / "unifideck"
    )
    actual = script_module.walk_sources(target)
    assert "event" not in actual
    # everything surviving the filter is a real enum member
    assert set(actual) <= script_module.VALID_EVENTS
