"""config/i18n_schema.py — i18n locale configuration schema.
# OP-11c | config/i18n_schema.py | Depends: (none)
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class LocaleEntry:
    tag: str
    deepl_code: str | None
    name: str
    rtl: bool = False


def get_supported_locales(config=None) -> list[LocaleEntry]:
    """Return list of supported locales from config."""
    raise NotImplementedError("OP-11c")
