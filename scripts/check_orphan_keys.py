#!/usr/bin/env python3
"""scripts/check_orphan_keys.py — Validate translation-key coverage.

Two independent checks, either of which can fail the run:

1. Orphan check — literal keys used in the codebase (``t("key")`` /
   ``i18nKey="key"``) that are NOT declared in a locale file.

2. Completeness check — keys declared in the en-US source of truth that are
   MISSING from another locale (so i18next silently falls back to English).
   This catches keys the orphan scan cannot see because they reach ``t()``
   indirectly via a helper (e.g. ``t(statusLabelKey(...))``) rather than as a
   string literal.

Exits non-zero if either check finds a problem.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
LOCALES_DIR = SRC_ROOT / "i18n" / "locales"
SOURCE_LOCALE = "en-US"

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

    # Parse every locale once: {locale_name: {flat_key: value}}.
    flat_by_locale: dict[str, dict[str, str]] = {}
    for path in locale_files:
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[check_orphan_keys] error: failed to parse {path}: {e}", file=sys.stderr)
            return 2
        flat_by_locale[path.stem] = flatten_json(data)

    if SOURCE_LOCALE not in flat_by_locale:
        print(f"[check_orphan_keys] error: source locale {SOURCE_LOCALE}.json not found", file=sys.stderr)
        return 2

    used_keys_map = scan_frontend_files()
    used_keys = set(used_keys_map.keys())

    # --- Check 1: orphan keys (used in code, undeclared in a locale) ---
    locale_orphans: dict[str, list[str]] = {}
    for locale_name, flat_data in flat_by_locale.items():
        declared_keys = set(flat_data.keys())
        missing = sorted(
            k for k in used_keys
            if k not in declared_keys and not k.endswith("._comment")
        )
        if missing:
            locale_orphans[locale_name] = missing

    # --- Check 2: completeness vs en-US source of truth ---
    source_keys = {
        k for k in flat_by_locale[SOURCE_LOCALE] if not k.endswith("._comment")
    }
    locale_incomplete: dict[str, list[str]] = {}
    for locale_name, flat_data in flat_by_locale.items():
        if locale_name == SOURCE_LOCALE:
            continue
        missing = sorted(source_keys - set(flat_data.keys()))
        if missing:
            locale_incomplete[locale_name] = missing

    if not locale_orphans and not locale_incomplete:
        print(
            f"[check_orphan_keys] OK — {len(used_keys_map)} used keys and "
            f"{len(source_keys)} {SOURCE_LOCALE} keys verified across "
            f"{len(locale_files)} languages. No orphans, no missing translations."
        )
        return 0

    if locale_orphans:
        print(
            "[check_orphan_keys] FAIL — translation keys are used in code but NOT declared in target locales:",
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

    if locale_incomplete:
        print(
            f"\n[check_orphan_keys] FAIL — keys present in {SOURCE_LOCALE} are MISSING "
            f"from these locales (they will fall back to English):",
            file=sys.stderr,
        )
        for locale_name, missing in sorted(locale_incomplete.items()):
            print(f"\n[{locale_name}] Missing {len(missing)} keys:", file=sys.stderr)
            for key in missing:
                print(f"  {key}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
