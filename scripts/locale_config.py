"""scripts/locale_config.py — Shared loader for the i18n section
of defaults/config.json.

Used by three consumers to avoid duplicating the schema and the
validation logic:
  1. scripts/translate_at_build.py — reads the list of target
     locales and their DeepL API codes
  2. scripts/gen_locale_imports.py — reads the same list to
     generate src/i18n/locales.generated.ts with static imports
  3. py_modules/unifideck/core/config_manager.py — validates the
     schema when the backend plugin loads config.json, so a
     malformed config fails the plugin at startup rather than
     at runtime in the frontend

The validation rules are intentionally strict: any deviation
from the expected schema raises LocaleConfigError with a clear
message identifying exactly which entry is wrong and why.

Module layout:
  - Module constants (regex pattern, required fields)
  - Public exceptions + dataclasses (Locale, LocaleConfig)
  - Public loaders (load_from_path, load_from_dict)
  - Private single-responsibility validators (one per rule)

Each validator is a small, independently-testable function
that raises ``LocaleConfigError`` or returns silently. The
public ``load_from_dict`` sequences them in order; adding a
new rule is a one-line addition to that sequence plus a new
private validator.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ══════════════════════════════════════════════════════════════
# Module constants
# ══════════════════════════════════════════════════════════════

# Regex for BCP 47 language tag validation. Matches the common
# form lang[-REGION] used in the project: 2 lowercase letters,
# optionally followed by a hyphen and 2 uppercase letters.
# Intentionally excludes the wider BCP 47 grammar (script
# subtags like zh-Hans, variant subtags, extended tags) because
# our file-naming convention doesn't support them — DeepL's own
# script variants (zh-Hans / zh-Hant) are handled by the
# ``deepl_code`` field, not by the ``tag`` field.
BCP47_TAG_PATTERN = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

# Every locale entry must supply these four fields. Listed as a
# tuple (rather than inline) so the "missing field" validator
# iterates once without duplicating the list.
_REQUIRED_FIELDS: tuple[str, ...] = ("tag", "deepl_code", "name", "rtl")


# ══════════════════════════════════════════════════════════════
# Public exception + dataclasses
# ══════════════════════════════════════════════════════════════


class LocaleConfigError(ValueError):
    """Raised when the i18n section of config.json is malformed.

    The message always identifies the specific entry and the
    specific validation rule that was violated, so the operator
    can fix the config without reading this module's source.
    """


@dataclass(frozen=True)
class Locale:
    """One entry in the ``i18n.locales`` list.

    Immutable so we can safely share instances across consumers
    and iterate over them repeatedly without mutation surprises.
    """

    tag: str
    deepl_code: str | None
    name: str
    rtl: bool

    @property
    def is_source(self) -> bool:
        """True for the source locale (``deepl_code is None``).

        The source locale is the one DeepL translates FROM.
        Validation guarantees exactly one exists per
        ``LocaleConfig``.
        """
        return self.deepl_code is None


@dataclass(frozen=True)
class LocaleConfig:
    """The full parsed and validated i18n section of config.json.

    Exposes convenient accessors so consumers don't re-implement
    "find the source entry" or "list target entries" logic.
    """

    locales: list[Locale]

    @property
    def source(self) -> Locale:
        """Return the source locale (``deepl_code is None``).

        Validation guarantees exactly one exists on a validated
        instance, so this never raises in normal use.
        """
        for loc in self.locales:
            if loc.is_source:
                return loc
        # Unreachable on a validated instance — defensive only.
        raise LocaleConfigError("no source locale found (internal bug)")

    @property
    def targets(self) -> list[Locale]:
        """Return every non-source locale, in declaration order.

        Same order as the config file — the LanguageSelector
        dropdown iterates this list directly.
        """
        return [loc for loc in self.locales if not loc.is_source]

    @property
    def all_tags(self) -> list[str]:
        """Every tag in declaration order.

        Callers that want the exact config order should iterate
        over ``locales`` directly; this is the convenience
        shortcut for building a quick lookup list.
        """
        return [loc.tag for loc in self.locales]

    def get(self, tag: str) -> Locale | None:
        """Look up a locale by tag, or return ``None`` if missing."""
        for loc in self.locales:
            if loc.tag == tag:
                return loc
        return None


# ══════════════════════════════════════════════════════════════
# Public loaders
# ══════════════════════════════════════════════════════════════


def load_from_path(config_path: Path) -> LocaleConfig:
    """Load + validate the i18n section of a config.json file.

    Raises ``LocaleConfigError`` on missing file, invalid JSON,
    or any schema violation. The message identifies the exact
    problem so the operator can fix it without reading source.
    """
    if not config_path.is_file():
        raise LocaleConfigError(
            f"config file not found: {config_path}",
        )
    try:
        with config_path.open() as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise LocaleConfigError(
            f"config file is not valid JSON: {config_path}: {e}",
        ) from e
    return load_from_dict(raw)


def load_from_dict(raw: dict[str, Any]) -> LocaleConfig:
    """Extract + validate the i18n section from a loaded dict.

    Used by consumers that already have the config in memory
    (e.g. backend ``ConfigManager``). Sequence of SRP
    validators — each raises with a precise message.
    """
    locales_raw = _extract_locales_list(raw)
    parsed = [_parse_entry(i, e) for i, e in enumerate(locales_raw)]
    _validate_unique_tags(parsed)
    _validate_single_source(parsed)
    return LocaleConfig(locales=parsed)


# ══════════════════════════════════════════════════════════════
# Private validators — one responsibility per function
# ══════════════════════════════════════════════════════════════


def _extract_locales_list(raw: dict[str, Any]) -> list[Any]:
    """Return the ``i18n.locales`` list or raise with context.

    Validates the outer shape: presence of ``i18n`` key, dict
    type, presence of ``locales`` key, list type, non-empty.
    Returns the raw list for ``_parse_entry`` to walk.
    """
    if "i18n" not in raw:
        raise LocaleConfigError("config is missing the 'i18n' section")
    i18n = raw["i18n"]
    if not isinstance(i18n, dict):
        raise LocaleConfigError(
            f"'i18n' must be an object, got {type(i18n).__name__}",
        )
    if "locales" not in i18n:
        raise LocaleConfigError("config.i18n is missing the 'locales' list")
    locales = i18n["locales"]
    if not isinstance(locales, list):
        raise LocaleConfigError(
            "config.i18n.locales must be a list, "
            f"got {type(locales).__name__}",
        )
    if not locales:
        raise LocaleConfigError(
            "config.i18n.locales must contain at least one entry",
        )
    return locales


def _parse_entry(index: int, entry: Any) -> Locale:
    """Validate one raw entry and build a ``Locale``.

    Wraps the three sub-validators
    (``_check_entry_is_dict`` → ``_check_required_fields``
    → ``_check_field_types_and_values``) so the call sites
    read as a short pipeline.
    """
    _check_entry_is_dict(index, entry)
    _check_required_fields(index, entry)
    _check_field_types_and_values(index, entry)
    return Locale(
        tag=entry["tag"],
        deepl_code=entry["deepl_code"],
        name=entry["name"],
        rtl=entry["rtl"],
    )


def _check_entry_is_dict(index: int, entry: Any) -> None:
    """Each entry must be a JSON object."""
    if not isinstance(entry, dict):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}] must be an object, "
            f"got {type(entry).__name__}",
        )


def _check_required_fields(index: int, entry: dict[str, Any]) -> None:
    """Every entry must carry all four required keys."""
    for field_name in _REQUIRED_FIELDS:
        if field_name not in entry:
            raise LocaleConfigError(
                f"config.i18n.locales[{index}] is missing "
                f"required field '{field_name}'",
            )


def _check_field_types_and_values(
    index: int, entry: dict[str, Any],
) -> None:
    """Type + value checks for each field.

    Split out of ``_parse_entry`` so the per-field rules stay
    easy to scan. Raises on the first violation with a message
    that names the specific field.
    """
    _check_tag(index, entry["tag"])
    _check_deepl_code(index, entry["deepl_code"])
    _check_name(index, entry["name"])
    _check_rtl(index, entry["rtl"])


def _check_tag(index: int, tag: Any) -> None:
    """``tag`` must be a BCP-47-ish string (see ``BCP47_TAG_PATTERN``)."""
    if not isinstance(tag, str):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].tag must be a string, "
            f"got {type(tag).__name__}",
        )
    if not BCP47_TAG_PATTERN.match(tag):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].tag '{tag}' is not a "
            "valid BCP 47 tag. Expected format: 'xx' or 'xx-YY' "
            "(2 lowercase letters, optionally followed by a "
            "hyphen and 2 uppercase letters). Examples: 'ar', "
            "'fr-FR', 'zh-TW'.",
        )


def _check_deepl_code(index: int, deepl_code: Any) -> None:
    """``deepl_code`` must be a non-empty string or None (source)."""
    if deepl_code is None:
        return
    if not isinstance(deepl_code, str):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].deepl_code must be a "
            f"string or null, got {type(deepl_code).__name__}",
        )
    if not deepl_code.strip():
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].deepl_code is an empty "
            "string — use null to mark the source locale or "
            "provide a non-empty DeepL API code",
        )


def _check_name(index: int, name: Any) -> None:
    """``name`` must be a non-empty string (native-script name)."""
    if not isinstance(name, str):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].name must be a string, "
            f"got {type(name).__name__}",
        )
    if not name.strip():
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].name is empty — provide "
            "the native-script name (e.g. 'Français', 'Japanese')",
        )


def _check_rtl(index: int, rtl: Any) -> None:
    """``rtl`` must be a boolean. No implicit truthy/falsy accepted."""
    if not isinstance(rtl, bool):
        raise LocaleConfigError(
            f"config.i18n.locales[{index}].rtl must be a boolean, "
            f"got {type(rtl).__name__}",
        )


def _validate_unique_tags(locales: list[Locale]) -> None:
    """Every tag must appear exactly once in the list."""
    seen: set[str] = set()
    for loc in locales:
        if loc.tag in seen:
            raise LocaleConfigError(
                f"config.i18n.locales tag '{loc.tag}' is "
                "duplicated — each tag must appear exactly once",
            )
        seen.add(loc.tag)


def _validate_single_source(locales: list[Locale]) -> None:
    """Exactly one entry must have ``deepl_code=None`` (the source)."""
    source_count = sum(1 for loc in locales if loc.is_source)
    if source_count == 0:
        raise LocaleConfigError(
            "config.i18n.locales must have exactly one entry with "
            "deepl_code=null (the source locale). Found zero. "
            "Typically this is en-US.",
        )
    if source_count > 1:
        raise LocaleConfigError(
            "config.i18n.locales must have exactly one entry with "
            f"deepl_code=null (the source locale). Found "
            f"{source_count}. Only one locale can be the source.",
        )
