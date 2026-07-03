from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path

from .matchers import smart_match_locale
from .resolver import _DEFAULT_LANGUAGE, LOCALE_MAP

logger = logging.getLogger(__name__)
def _resolve_prefix(prefix_path: str) -> str:
    """Resolve prefix."""
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_registry_prefix,
    )
    return str(resolve_registry_prefix(prefix_path))
def _atomic_write_text(path: str, content: str) -> None:
    """Atomic write text."""
    target_dir = str(Path(path).parent) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".reg.", suffix=".tmp", dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp_path).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()
        raise

def _update_user_reg(
    prefix_path: str,
    lcid: str, slanguage: str, locale_name: str, scountry: str,
) -> bool:

    """Update user reg."""
    user_reg = str(Path(prefix_path) / "user.reg")
    if not Path(user_reg).exists():
        logger.warning(
            "[language_setup] user.reg missing at %s — prefix not "
            "initialised yet", user_reg,
        )
        return False
    with Path(user_reg).open(encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    section_header = "[Control Panel\\\\International]"
    new_values = {
        "Locale": lcid,
        "LocaleName": locale_name,
        "sLanguage": slanguage,
        "sCountry": scountry,
    }
    if section_header in content:
        section_start = content.index(section_header)
        body_start = section_start + len(section_header)
        next_section = re.search(r"\n\[", content[body_start:])
        section_end = (
            body_start + next_section.start()
            if next_section else len(content)
        )
        section_body = content[body_start:section_end]
        for key, value in new_values.items():
            pattern = rf'^"{re.escape(key)}"="[^"]*"'
            replacement = f'"{key}"="{value}"'
            new_body, count = re.subn(
                pattern, replacement, section_body, flags=re.MULTILINE,
            )
            if count > 0:
                section_body = new_body
            else:
                section_body = (
                    section_body.rstrip("\n") + f'\n"{key}"="{value}"\n'
                )
        content = (
            content[:body_start] + section_body + content[section_end:]
        )
    else:
        section = f"\n{section_header}\n"
        for key, value in new_values.items():
            section += f'"{key}"="{value}"\n'
        content += section
    _atomic_write_text(user_reg, content)
    logger.info(
        "[language_setup] wrote locale=%s to %s",
        locale_name, user_reg,
    )
    return True
def _apply_windows_locale(prefix_path: str, language: str) -> bool:
    """Apply windows locale."""
    resolved_prefix = _resolve_prefix(prefix_path)
    locale = smart_match_locale(language)
    if locale is None:
        logger.info(
            "[language_setup] no locale mapping for %s, using %s",
            language, _DEFAULT_LANGUAGE,
        )
        locale = LOCALE_MAP[_DEFAULT_LANGUAGE]
    return _update_user_reg(resolved_prefix, *locale)
