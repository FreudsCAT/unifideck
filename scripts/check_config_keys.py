#!/usr/bin/env python3
"""scripts/check_config_keys.py — Enforce RUNTIME_REQUIRED_KEYS coverage.

Pre-commit / CI hook. Exits non-zero if any ``config.get("...")``,
``self._config.get("...")``, or ``_cfg(config, "...")`` call in
``py_modules/unifideck/`` references a key that is NOT listed in
``unifideck.config.key_presence.RUNTIME_REQUIRED_KEYS``.

Rationale
---------
After Step 4 of the config-rigor sprint, call sites drop their
hardcoded default arg and rely entirely on ``defaults/config.json``
+ schema validation + the boot-time ``assert_all_keys_resolve``
check. That chain has one blind spot: if a developer adds a
``config.get("new.key")`` in code but forgets to add ``"new.key"``
to ``RUNTIME_REQUIRED_KEYS``, the boot check can't complain because
it doesn't know the key exists. The missing key silently returns
``None`` at runtime.

This script closes that loop by scraping every call site's literal
key string and comparing the set against ``RUNTIME_REQUIRED_KEYS``.
Any diff is a developer error caught before commit.

What it does NOT check
----------------------
* Non-literal keys (``config.get(computed_key)``) — we can't know
  statically. The ``assert_all_keys_resolve`` boot check covers
  the static subset; dynamic keys are the caller's responsibility.
* Presence in ``defaults/config.json`` — that's covered by
  ``assert_all_keys_resolve`` at boot.
* Presence in the JSON Schema — that's covered by
  ``ConfigValidator.validate_config`` at boot.

Usage
-----
    python3 scripts/check_config_keys.py

Intended to be wired into ``.pre-commit-config.yaml`` as a local
Python hook and into CI as a dedicated step.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "py_modules" / "unifideck"

# AST-based literal-string extraction is more robust than regex
# against edge cases (multi-line calls, comments that mention the
# call pattern, f-string fragments). The visitor below walks every
# Call node and records literal-string arguments to the three
# canonical config-reader patterns.

_GET_PATTERNS = (
    # Match: config.get("key") / self._config.get("key") /
    #        settings._config.get("key") / ... .config.get("key")
    # Attribute access chain must end in ``.get`` on an attribute
    # whose name contains "config" (case-insensitive) somewhere.
    "config.get",
)


class _ConfigKeyVisitor(ast.NodeVisitor):
    """Collect literal-string keys from config-reader calls.

    Matches three shapes:

        config.get("...")
        <something>.config.get("...")       — e.g. ``self._config.get``
        _cfg(config, "...")                  — module-local helper
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.keys: list[tuple[str, int]] = []  # (key, line)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        key = self._extract_key(node)
        if key is not None:
            self.keys.append((key, node.lineno))
        self.generic_visit(node)

    @staticmethod
    def _extract_key(node: ast.Call) -> str | None:
        func = node.func
        # Pattern: <expr>.get(...) where the expression is or ends
        # with an attribute named with ``config`` substring.
        if isinstance(func, ast.Attribute) and func.attr == "get":
            owner = func.value
            owner_name = _ConfigKeyVisitor._last_attr_or_name(owner)
            if owner_name and "config" in owner_name.lower():
                if node.args and isinstance(node.args[0], ast.Constant):
                    val = node.args[0].value
                    if isinstance(val, str):
                        return val
        # Pattern: _cfg(config, "...", ...)
        if isinstance(func, ast.Name) and func.id == "_cfg":
            if (
                len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                return node.args[1].value
        return None

    @staticmethod
    def _last_attr_or_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def collect_call_site_keys() -> dict[str, list[tuple[pathlib.Path, int]]]:
    """Walk ``py_modules/unifideck`` and collect every config-key literal.

    Returns a mapping ``{key: [(file, line), ...]}`` so callers can
    report the exact locations of each unregistered key.
    """
    sites: dict[str, list[tuple[pathlib.Path, int]]] = {}
    for p in PY_ROOT.rglob("*.py"):
        # Skip the registry module itself — it defines the expected set,
        # it doesn't read config.
        if p.name == "key_presence.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            # Syntax errors are a separate problem; another hook
            # catches those. We skip them rather than double-report.
            continue
        visitor = _ConfigKeyVisitor(p)
        visitor.visit(tree)
        for key, line in visitor.keys:
            sites.setdefault(key, []).append((p, line))
    return sites


def load_registered_keys() -> set[str]:
    """Parse RUNTIME_REQUIRED_KEYS from key_presence.py without importing.

    Importing the module would drag in the whole typing/logging tree
    and slow the hook. A simple AST walk of the single tuple literal
    is orders of magnitude faster and has no side effects.
    """
    kp_path = PY_ROOT / "config" / "key_presence.py"
    tree = ast.parse(kp_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # Match both ``X = (...)`` and ``X: tuple[...] = (...)`` forms.
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id == "RUNTIME_REQUIRED_KEYS"
            and isinstance(value, ast.Tuple)
        ):
            return {
                elt.value for elt in value.elts
                if isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
            }
    raise RuntimeError(
        "RUNTIME_REQUIRED_KEYS tuple not found in key_presence.py",
    )


def main(argv: Iterable[str] | None = None) -> int:
    sites = collect_call_site_keys()
    registered = load_registered_keys()
    unregistered = sorted(k for k in sites if k not in registered)

    if not unregistered:
        print(
            f"[check_config_keys] OK — {len(sites)} distinct keys, "
            f"all present in RUNTIME_REQUIRED_KEYS.",
        )
        return 0

    print(
        "[check_config_keys] FAIL — the following keys are read by "
        "code but NOT listed in key_presence.RUNTIME_REQUIRED_KEYS:",
        file=sys.stderr,
    )
    for key in unregistered:
        locations = sites[key]
        first_file, first_line = locations[0]
        rel = first_file.relative_to(REPO_ROOT)
        extra = (
            f"  (+{len(locations) - 1} more site(s))"
            if len(locations) > 1 else ""
        )
        print(f"  {key}  →  {rel}:{first_line}{extra}", file=sys.stderr)

    print(
        "\n[check_config_keys] Add each key to "
        "``py_modules/unifideck/config/key_presence.py`` under "
        "``RUNTIME_REQUIRED_KEYS``, then ensure the key is also "
        "declared in ``defaults/config.json`` and "
        "``py_modules/unifideck/config/schema.json``.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
