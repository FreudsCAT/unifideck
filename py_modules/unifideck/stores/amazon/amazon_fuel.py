"""amazon_fuel.py — Parse ``fuel.json`` to locate game executables.

# OP-49f | py_modules/unifideck/stores/amazon/amazon_fuel.py | Depends: OP-49c

Amazon Games installs ship a ``fuel.json`` manifest at the install
root (or under ``game/`` / ``Game/``). It points at the playable
executable via ``Main.Command``. This module reads the manifest with
permissive JSON-with-comments parsing and falls back to a largest-exe
heuristic when fuel.json is missing or malformed.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)
_COMMENT_RE = re.compile(r'//.*$', re.MULTILINE)
_SKIP_EXE_PATTERNS: tuple[str, ...] = (
    'unins', 'setup', 'install', 'crash', 'redist', 'vcredist',
)


def candidate_fuel_dirs(install_path: str) -> list[str]:
    """Candidate fuel dirs."""
    if not install_path or not os.path.isdir(install_path):
        return []
    candidates: list[str] = [install_path]
    for sub in ('game', 'Game'):
        candidate = os.path.join(install_path, sub)
        if candidate not in candidates and os.path.isdir(candidate):
            candidates.append(candidate)
    try:
        for entry in os.listdir(install_path):
            subdir = os.path.join(install_path, entry)
            if os.path.isdir(subdir) and subdir not in candidates:
                candidates.append(subdir)
    except OSError:
        pass
    return candidates


def parse_fuel_json_content(content: str) -> dict | None:
    """Parse fuel JSON content."""
    if not content:
        return None
    cleaned = _COMMENT_RE.sub('', content)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.debug('[amazon_fuel] parse failed: %s', e)
        return None
    return data if isinstance(data, dict) else None


def extract_main_command(fuel_data: dict) -> str | None:
    """Extract main command."""
    if not isinstance(fuel_data, dict):
        return None
    main = fuel_data.get('Main')
    if not isinstance(main, dict):
        return None
    command = main.get('Command')
    return command if isinstance(command, str) and command else None


def find_exe_from_fuel(install_path: str) -> str | None:
    """Find exe from fuel.

    Tries every candidate dir in turn; first directory with a parsable
    ``fuel.json`` whose ``Main.Command`` points at an existing file
    wins. Falls back to the largest non-installer .exe in the install
    tree (rooted at ``install_path``) when no manifest resolves.
    """
    if not install_path:
        return None
    for directory in candidate_fuel_dirs(install_path):
        fuel_path = os.path.join(directory, 'fuel.json')
        if not os.path.isfile(fuel_path):
            continue
        try:
            content = Path(fuel_path).read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            logger.debug('[amazon_fuel] read %s: %s', fuel_path, e)
            continue
        data = parse_fuel_json_content(content)
        if data is None:
            continue
        command = extract_main_command(data)
        if not command:
            continue
        exe_path = os.path.join(directory, command)
        if os.path.isfile(exe_path):
            logger.info(
                '[amazon_fuel] resolved exe from %s: %s',
                fuel_path, exe_path,
            )
            return exe_path
        logger.debug(
            '[amazon_fuel] command %r missing under %s', command, directory,
        )
    return _find_largest_exe(install_path)


def _find_largest_exe(install_path: str) -> str | None:
    """Find largest exe fallback."""
    if not install_path or not os.path.isdir(install_path):
        return None
    candidates: list[tuple[int, str]] = []
    for pattern in ('*.exe', '**/*.exe'):
        for path in glob.glob(
            os.path.join(install_path, pattern), recursive=True,
        ):
            basename = os.path.basename(path).lower()
            if any(skip in basename for skip in _SKIP_EXE_PATTERNS):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            candidates.append((size, path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    logger.info('[amazon_fuel] fallback exe: %s', candidates[0][1])
    return candidates[0][1]
