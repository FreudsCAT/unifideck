from __future__ import annotations
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
_ENV_TOKEN_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
_LSFG_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)$")
@dataclass
class ParsedOptions:
    """Parsed options."""
    wrappers: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    env_overrides: dict[str, str] = field(default_factory=dict)
    lsfg_requested: bool = False
def parse_launch_options(raw: str) -> ParsedOptions:
    """Parse launch options."""
    result = ParsedOptions()
    if not raw or not raw.strip():
        return result
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    remaining: list[str] = []
    for tok in tokens:
        m = _ENV_TOKEN_RE.match(tok)
        if m:
            result.env_overrides[m.group(1)] = m.group(2)
        else:
            remaining.append(tok)
    home = os.path.expanduser("~")
    lsfg_filtered: list[str] = []
    for tok in remaining:
        expanded = (
            tok.replace("~", home, 1)
            if tok.startswith("~") else tok
        )
        if expanded.endswith("/lsfg"):
            result.lsfg_requested = True
        else:
            lsfg_filtered.append(tok)
    if result.env_overrides.get("LSFG") == "1":
        result.lsfg_requested = True
    if result.env_overrides.get("ENABLE_LSFG") == "1":
        result.lsfg_requested = True
    _split_tokens_around_command(lsfg_filtered, result)
    return result

def _split_tokens_around_command(
    tokens: list[str], result: ParsedOptions,
) -> None:

    """Split tokens around command."""
    found_cmd = False
    for tok in tokens:
        if tok == "%command%":
            found_cmd = True
            continue
        if tok == "#%command%":
            continue
        if found_cmd:
            result.game_args.append(tok)
        else:
            result.wrappers.append(tok)
    if (
        not found_cmd
        and result.wrappers
        and not result.game_args
    ):
        result.game_args = result.wrappers
        result.wrappers = []
def apply_lsfg_env(
    opts: ParsedOptions,
    lsfg_script: Path | None = None,
) -> dict[str, str]:
    """Apply lsfg env."""
    if not opts.lsfg_requested:
        return {}
    if lsfg_script is None:
        lsfg_script = Path(os.path.expanduser("~/lsfg"))
    if not lsfg_script.is_file():
        return {}
    overlay: dict[str, str] = {"ENABLE_LSFG": "1"}
    try:
        content = lsfg_script.read_text(
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return overlay
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith("#")
            or line.startswith("#!")
        ):
            continue
        if line.startswith("exec "):
            continue
        if not line.startswith("export "):
            continue
        kv = line[len("export "):]
        if "=" not in kv:
            continue
        key, _, value = kv.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        if _LSFG_KEY_RE.match(key):
            overlay[key] = value
    return overlay