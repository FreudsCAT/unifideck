"""Volumetry gates runner with grandfathered allowlist.

Five subcommands drive the complexity.yml workflow:

    python3 tools/volumetry_check.py files
        Scan every *.py under py_modules/unifideck/ plus main.py
        at repo root. Fail if any file exceeds the cap (550 LOC,
        800 for main.py) UNLESS that file is listed in
        FILES_ALLOWLIST with a current-size baseline. If a
        listed file has shrunk below the cap, fail with a
        cleanup reminder — the allowlist can only ever contract.

    python3 tools/volumetry_check.py functions
        Fail on any function over 80 lines not in
        FUNCTIONS_ALLOWLIST, warn for functions in 50-80 range.

    python3 tools/volumetry_check.py locals
        Fail on any function with > 15 distinct local variables
        not in LOCALS_ALLOWLIST. Correlates strongly with SRP
        violations — a function juggling 20 locals orchestrates
        too much state.

    python3 tools/volumetry_check.py nesting
        Fail on any function with nested if/for/while/with/try
        depth > 4 not in NESTING_ALLOWLIST. Deep nesting signals
        interleaved flows, a classic SRP smell.

    python3 tools/volumetry_check.py fanout
        Fail on any function with > 10 distinct external call
        targets not in FANOUT_ALLOWLIST. Common builtins and
        logger methods are excluded from the count (list in
        FANOUT_EXCLUDED). High fan-out = orchestrator doing
        many things = candidate for decomposition into helpers.

The allowlists below are the exhaustive baseline snapshot of
the new_architecture tree as of 2026-04-18. Each entry carries
the measured size at snapshot time so future tightening passes
(e.g. "drop cap from 550 to 400") can use the same pattern.

Tracked in technical doc §7.8 for incremental refactoring.

── Scope filter (2026-05-14) ──────────────────────────────────────
Before this revision, ``_iter_py_files`` walked ``py_modules/``
as a whole — including the vendored libs (``pip/``, ``urllib3/``,
``charset_normalizer/``, ``websockets/``, …). On a real CI run
this produced ~88 file-size violations on third-party code that
we don't own.

The flake8 steps in ``.github/workflows/complexity.yml`` had
already been fixed (in May 2026) to scope to
``py_modules/unifideck/`` explicitly; this script is being
brought in line with that policy.

Defence in depth: even if a vendor directory ever leaks into
``unifideck/`` (a real risk during package surgery), the walk
filters out any path component listed in ``_VENDOR_DIRS`` so the
gate stays focused on first-party code.

The same pass migrated the module to ``pathlib.Path`` to align
with the project-wide PTH cascade policy.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, Tuple

# ────────────────────────────────────────────────────────────
# Scope
# ────────────────────────────────────────────────────────────

# Repo-relative root containing every first-party Python file.
# Vendored libraries live elsewhere under ``py_modules/`` and are
# explicitly out of scope of every gate below — see the module
# docstring for the rationale.
SCOPE_ROOT = "py_modules/unifideck"

# Defence in depth: directory names that must NEVER be scanned,
# even if they somehow end up under SCOPE_ROOT (e.g. accidental
# ``cp -r`` during package surgery). Mirrors ``.flake8``'s
# ``extend-exclude`` list — keep both in sync if you add new
# vendors.
_VENDOR_DIRS = frozenset({
    "pip", "urllib3", "charset_normalizer", "websockets",
    "idna", "certifi", "requests", "_vendor",
    "__pycache__",
})

# ────────────────────────────────────────────────────────────
# Caps
# ────────────────────────────────────────────────────────────

FILE_CAP_DEFAULT = 550
FILE_CAP_MAIN_PY = 800
FUNC_FAIL = 80
FUNC_WARN = 50
LOCALS_CAP = 15
NESTING_CAP = 4
FANOUT_CAP = 10

# Fan-out exclusion: builtins, logger methods, and ubiquitous
# container ops. Calls to these don't signal "this function does
# N distinct things" — they are the hygiene of any single-purpose
# function (logging, string building, dict manipulation). Excluding
# them lets the metric catch real orchestrators.
FANOUT_EXCLUDED = frozenset({
    # builtins
    "len", "str", "int", "bool", "float", "list", "dict", "set",
    "tuple", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "print", "repr", "hash", "id", "type", "iter",
    "next", "range", "enumerate", "min", "max", "sum", "any",
    "all", "sorted", "reversed", "zip", "map", "filter",
    # logger methods
    "debug", "info", "warning", "error", "exception", "critical",
    "log",
    # common string/collection ops
    "format", "join", "strip", "rstrip", "lstrip", "split",
    "splitlines", "startswith", "endswith", "lower", "upper",
    "replace", "encode", "decode",
    "get", "setdefault", "update", "items", "keys", "values",
    "copy", "pop", "append", "extend", "insert", "remove",
    "clear", "sort", "reverse",
    "read", "write", "close", "open", "exists",
})

# ────────────────────────────────────────────────────────────
# Grandfathered allowlists
# ────────────────────────────────────────────────────────────
# Key = relative path from repo root. Value = baseline LOC as
# measured on 2026-04-18. Interpretation:
#   - File/function present in list AND current size <= baseline:
#     tolerated (grandfathered).
#   - File/function present in list AND current size > baseline:
#     FAIL — we tolerate the debt but not its growth.
#   - File/function present in list AND current size <= cap:
#     FAIL with cleanup reminder — entry must be removed from
#     the list since the file is now compliant.
#   - File/function NOT in list AND current size > cap:
#     FAIL — standard volumetry violation.

FILES_ALLOWLIST: Dict[str, int] = {
    # FILES_ALLOWLIST is now EMPTY — every pre-refactor fat file
    # has been decomposed into a subpackage of focused modules.
    # services/shortcut_service.py graduated from the allowlist
    # on 2026-04-19: the 661 LOC monolith was split into a
    # 5-file subpackage at services/shortcut/. Largest file is
    # service.py at ~390 LOC. Public API preserved: callers use
    # ``from unifideck.services.shortcut import ShortcutService``.
    # services/service_bootstrap.py graduated from the allowlist
    # on 2026-04-19: the 686 LOC monolith was split into a
    # 7-file subpackage at services/bootstrap/. Largest file is
    # service_defs.py at ~180 LOC, all others under 150.
    # services/security_service.py graduated from the allowlist on
    # 2026-04-18: the 620 LOC monolith was split into an 11-file
    # subpackage at services/security/ with 4 handler mixins in
    # services/security/mixins/. The largest file is service.py at
    # ~264 LOC, all others under 200.
    # auth/edge_browser.py graduated from the allowlist on 2026-04-18:
    # the 753 LOC monolith plus its 3 previously-extracted helper
    # modules (edge_cdp_client, edge_installer, edge_profile) were
    # reorganised into a single subpackage at auth/edge_browser/.
    # All 7 files in the subpackage are under the 550 LOC cap
    # (largest: edge.py at ~379 LOC).
    # launcher/proton/language_setup.py graduated from the allowlist
    # on 2026-04-18: the 598 LOC monolith was split into a 7-file
    # subpackage at launcher/proton/language_setup/. All new files
    # are under the 550 LOC cap (largest: ubisoft.py at ~210 LOC).
    # services/launcher_service.py graduated from the allowlist on
    # 2026-04-18: the 821 LOC monolith was split into a 6-file
    # subpackage at services/launcher/. All new files are under
    # the 550 LOC cap.
    # main.py graduated from the allowlist on 2026-04-18 Phase 2.2:
    # the mixin extractions (observability + security + download)
    # brought it from 1250 LOC down to 779, below the 800 cap.
    # Entry removed per allowlist contract — listed files must
    # stay above their respective cap or be cleaned up.
}

# Key = "<path>::<function_name>". Value = baseline LOC.
# Baseline snapshot 2026-04-18 (post-docstring-autofix): 38
# functions over 80L cap.
FUNCTIONS_ALLOWLIST: Dict[str, int] = {
    # ``stores/gog/store.py::__init__`` graduated on 2026-05-14:
    # the function shrank to 67 lines (down from baseline 87)
    # after extracting helper construction into module-level
    # builders. Removed per allowlist contract — grandfathered
    # entries must stay above the cap or be cleaned up.
}

# Key = "<path>::<function_name>". Value = baseline count.
LOCALS_ALLOWLIST: Dict[str, int] = {
}

NESTING_ALLOWLIST: Dict[str, int] = {
}

FANOUT_ALLOWLIST: Dict[str, int] = {
    # ``stores/gog/store.py::__init__`` graduated on 2026-06-30: the
    # construction was split into ``_build_core_components`` (always-on
    # submodules) and a shared ``_build_gogdl_submodules`` (also used by
    # ``_rebuild_auth_after_injection``), bringing fan-out from 15 to 8.
    # Removed per allowlist contract — grandfathered entries must stay above
    # the cap or be cleaned up.
}


def _iter_py_files() -> list[str]:
    """Enumerate the Python files under scope.

    Scope is :py:data:`SCOPE_ROOT` (the first-party ``unifideck``
    package) plus ``main.py`` at repo root. Vendored libraries
    elsewhere under ``py_modules/`` are intentionally excluded —
    we don't own them, their size and complexity are not our
    problem.

    Defence in depth: any path that traverses a directory whose
    name is in :py:data:`_VENDOR_DIRS` is filtered out, so a
    vendor that ever leaks into ``unifideck/`` (e.g. accidental
    ``cp -r`` during package surgery) is still skipped.

    Returns ``list[str]`` rather than ``list[Path]`` so the
    allowlist lookups (keyed by string repo-relative path) work
    unchanged.
    """
    files: list[str] = ["main.py"] if Path("main.py").exists() else []
    root = Path(SCOPE_ROOT)
    if not root.is_dir():
        # Defensive fallback: caller invoked from the wrong cwd
        # or the scope root doesn't exist yet. Don't crash — the
        # downstream check will report no violations.
        return files
    for path in root.rglob("*.py"):
        # ``rglob`` materialises descendants without exposing
        # an in-place pruning hook, so we filter on the path
        # parts after the fact. For unifideck/ (a few hundred
        # files) this is essentially free, and if a vendor ever
        # lands here it's filtered cleanly.
        if any(part in _VENDOR_DIRS for part in path.parts):
            continue
        files.append(str(path))
    return files


def check_files() -> int:
    """Run the file-size gate. Return 0 on success, 1 on
    violation. Emits GitHub Actions annotations."""
    violations = 0
    seen_listed = set()

    for path in _iter_py_files():
        with open(path, encoding="utf-8") as f:
            loc = sum(1 for _ in f)

        cap = FILE_CAP_MAIN_PY if path == "main.py" else FILE_CAP_DEFAULT

        if path in FILES_ALLOWLIST:
            seen_listed.add(path)
            baseline = FILES_ALLOWLIST[path]
            if loc <= cap:
                print(
                    f"::error file={path}::Grandfathered file is "
                    f"now {loc} LOC (<= {cap} cap). Remove from "
                    f"FILES_ALLOWLIST in tools/volumetry_check.py.",
                )
                violations += 1
            elif loc > baseline:
                print(
                    f"::error file={path}::File grew to {loc} LOC "
                    f"(baseline was {baseline}). Grandfathered "
                    f"entries can only shrink — refactor or split.",
                )
                violations += 1
        elif loc > cap:
            print(
                f"::error file={path}::File exceeds cap "
                f"({loc} > {cap} LOC). Split or refactor.",
            )
            violations += 1

    for listed in FILES_ALLOWLIST:
        if listed not in seen_listed:
            print(
                f"::warning::Allowlisted file {listed} not found "
                f"on disk. Remove from FILES_ALLOWLIST.",
            )

    return 1 if violations else 0


def _iter_functions():
    """Yield (path, function_node) for every function under
    scope."""
    for path in _iter_py_files():
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, n


def _count_function_lines(n) -> int:
    return getattr(n, "end_lineno", n.lineno) - n.lineno


def _count_locals(fn) -> int:
    """Count distinct local variable names excluding arguments."""
    args = {a.arg for a in fn.args.args}
    args.update(a.arg for a in fn.args.kwonlyargs)
    if fn.args.vararg:
        args.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        args.add(fn.args.kwarg.arg)

    locals_: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    locals_.add(tgt.id)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            if isinstance(n.target, ast.Name):
                locals_.add(n.target.id)
        elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            locals_.add(n.target.id)
        elif isinstance(n, ast.comprehension):
            if isinstance(n.target, ast.Name):
                locals_.add(n.target.id)
        elif isinstance(n, ast.withitem):
            if n.optional_vars and isinstance(
                n.optional_vars, ast.Name,
            ):
                locals_.add(n.optional_vars.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            locals_.add(n.name)
    return len(locals_ - args)


def _nesting_depth(fn) -> int:
    """Max depth of nested control-flow blocks inside fn body."""
    NESTING = (
        ast.If, ast.For, ast.AsyncFor, ast.While,
        ast.With, ast.AsyncWith, ast.Try,
    )

    def rec(node, depth: int = 0) -> int:
        m = depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, NESTING):
                m = max(m, rec(child, depth + 1))
            else:
                m = max(m, rec(child, depth))
        return m

    return rec(fn, 0)


def _fan_out(fn) -> int:
    """Count distinct call targets excluding builtins + logger
    + common collection/string ops."""
    seen: set[str] = set()
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        target: str | None = None
        if isinstance(n.func, ast.Name):
            target = n.func.id
        elif isinstance(n.func, ast.Attribute):
            target = n.func.attr
        if target and target not in FANOUT_EXCLUDED:
            seen.add(target)
    return len(seen)


def _gate_metric(
    metric_name: str,
    cap: int,
    allowlist: Dict[str, int],
    measure,
    warn_threshold: int | None = None,
) -> int:
    """Unified implementation for the per-function metrics
    (functions, locals, nesting, fanout).

    Args:
        metric_name: Human label used in error/warning messages
            (e.g. "lines", "locals", "nesting depth", "fan-out").
        cap: Hard limit — above this, fail unless allowlisted.
        allowlist: Maps "path::fnname" to baseline value.
        measure: Callable that takes a FunctionDef node and
            returns the measured int.
        warn_threshold: If set, emit a warning (non-fail) for
            values strictly above this threshold but below cap.
    """
    violations = 0
    seen_listed: set[str] = set()

    for path, fn in _iter_functions():
        value = measure(fn)
        key = f"{path}::{fn.name}"
        line = fn.lineno

        if key in allowlist:
            seen_listed.add(key)
            baseline = allowlist[key]
            if value <= cap:
                print(
                    f"::error file={path},line={line}::"
                    f"Grandfathered function {fn.name}() now has "
                    f"{metric_name}={value} (<= {cap} cap). "
                    f"Remove from the allowlist.",
                )
                violations += 1
            elif value > baseline:
                print(
                    f"::error file={path},line={line}::"
                    f"{fn.name}() {metric_name} grew to {value} "
                    f"(baseline {baseline}). Refactor or split.",
                )
                violations += 1
        elif value > cap:
            print(
                f"::error file={path},line={line}::"
                f"{fn.name}() {metric_name}={value} "
                f"(> {cap} cap). Split or refactor.",
            )
            violations += 1
        elif warn_threshold is not None and value > warn_threshold:
            print(
                f"::warning file={path},line={line}::"
                f"{fn.name}() {metric_name}={value} "
                f"(approaching {cap} cap).",
            )

    for listed in allowlist:
        if listed not in seen_listed:
            print(
                f"::warning::Allowlisted entry {listed} not found. "
                f"Remove from the allowlist.",
            )

    return 1 if violations else 0


def check_functions() -> int:
    return _gate_metric(
        "lines", FUNC_FAIL, FUNCTIONS_ALLOWLIST,
        _count_function_lines, FUNC_WARN,
    )


def check_locals() -> int:
    return _gate_metric(
        "locals", LOCALS_CAP, LOCALS_ALLOWLIST, _count_locals,
    )


def check_nesting() -> int:
    return _gate_metric(
        "nesting depth", NESTING_CAP, NESTING_ALLOWLIST,
        _nesting_depth,
    )


def check_fanout() -> int:
    return _gate_metric(
        "fan-out", FANOUT_CAP, FANOUT_ALLOWLIST, _fan_out,
    )


CHECKS = {
    "files": check_files,
    "functions": check_functions,
    "locals": check_locals,
    "nesting": check_nesting,
    "fanout": check_fanout,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in CHECKS:
        print(
            "usage: python3 tools/volumetry_check.py "
            "{" + "|".join(CHECKS) + "}",
            file=sys.stderr,
        )
        return 2
    return CHECKS[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
