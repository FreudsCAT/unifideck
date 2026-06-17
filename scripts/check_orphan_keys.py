#!/usr/bin/env python3
"""scripts/check_orphan_keys.py — Scan frontend files for translation keys missing from en-US.json.

Exits non-zero if any literal keys are used in the codebase but missing from the English source file.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
LOCALES_DIR = SRC_ROOT / "i18n" / "locales"

# Regexes to capture literal string keys in translation calls.
# 1. t("key") or t('key') or t(`key`)
T_REGEX = re.compile(r"\bt\(\s*(?:'([^']+)'|\"([^\"]+)\"|`([^`]+)`)\s*")
# 2. i18nKey="key" or i18nKey='key' or i18nKey={"key"}
I18NKEY_REGEX = re.compile(r"\bi18nKey\s*=\s*(?:['\"]([^'\"]+)['\"]|\{\s*(?:'([^']+)'|\"([^\"]+)\")\s*\})")


def flatten_json(obj: object, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    if not isinstance(obj, dict):
        return flat
    for key, value in obj.items():
        composed = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_json(value, composed))
        elif isinstance(value, str):
            flat[composed] = value
    return flat


def scan_frontend_files() -> dict[str, list[tuple[Path, int]]]:
    """Scan all .ts and .tsx files in src/ and return a map of {key: [(file_path, line_no), ...]}."""
    used_keys: dict[str, list[tuple[Path, int]]] = {}

    for p in SRC_ROOT.rglob("*"):
        if p.suffix not in (".ts", ".tsx"):
            continue
        # Skip locales directory to avoid scanning translation files themselves
        if "i18n/locales" in p.as_posix():
            continue

        try:
            content = p.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[check_orphan_keys] warning: could not read {p}: {e}", file=sys.stderr)
            continue

        for line_idx, line in enumerate(content.splitlines(), start=1):
            # Scan for t(...)
            for match in T_REGEX.finditer(line):
                # Extract first non-empty group
                key = next((g for g in match.groups() if g is not None), None)
                if key and not "${" in key and not "+" in key:
                    used_keys.setdefault(key, []).append((p, line_idx))

            # Scan for i18nKey=...
            for match in I18NKEY_REGEX.finditer(line):
                key = next((g for g in match.groups() if g is not None), None)
                if key and not "${" in key and not "+" in key:
                    used_keys.setdefault(key, []).append((p, line_idx))

    return used_keys


def main() -> int:
    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    if not locale_files:
        print(f"[check_orphan_keys] error: no locale files found in {LOCALES_DIR}", file=sys.stderr)
        return 2

    used_keys_map = scan_frontend_files()
    used_keys = set(used_keys_map.keys())

    # Map of locale_name -> list of missing keys
    locale_orphans: dict[str, list[str]] = {}

    for path in locale_files:
        locale_name = path.stem
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[check_orphan_keys] error: failed to parse {path}: {e}", file=sys.stderr)
            return 2

        flat_data = flatten_json(data)
        declared_keys = set(flat_data.keys())

        missing = sorted(k for k in used_keys if k not in declared_keys)
        # Ignore comment keys
        missing = [o for o in missing if not o.endswith("._comment")]

        if missing:
            locale_orphans[locale_name] = missing

    if not locale_orphans:
        print(f"[check_orphan_keys] OK — verified {len(used_keys_map)} keys across {len(locale_files)} languages. No orphan keys found.")
        return 0

    print(
        f"[check_orphan_keys] FAIL — translation keys are used in code but NOT declared in target locales:",
        file=sys.stderr,
    )
    for locale_name, missing in sorted(locale_orphans.items()):
        print(f"\n[{locale_name}] Missing {len(missing)} keys:", file=sys.stderr)
        for key in missing:
            locations = used_keys_map[key]
            first_file, first_line = locations[0]
            rel_path = first_file.relative_to(REPO_ROOT)
            extra = f" (+{len(locations) - 1} more sites)" if len(locations) > 1 else ""
            print(f"  {key}  →  {rel_path}:{first_line}{extra}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
