"""user_config_path — resolve the user overrides JSON path.

Centralised XDG-compliant resolver so every caller (ConfigManager
bootstrap, ConfigValidator, debug tooling) lands on the same file.
The file may not exist on first run; callers must handle that
gracefully (ConfigManager merges nothing, ConfigValidator reports
no error — missing overrides are by design not an error, see
TC-VAL-08).

Resolution precedence:

  1. ``UNIFIDECK_USER_CONFIG`` environment variable — useful for
     tests and alternate deployments (dev sandbox, multi-profile
     setups). Expanded for ``~`` so ``~/my-overrides.json`` works.
  2. ``$XDG_CONFIG_HOME/unifideck/config.json`` if
     ``XDG_CONFIG_HOME`` is set in the environment.
  3. ``~/.config/unifideck/config.json`` — the canonical location
     on Steam Deck (SteamOS honours the XDG Base Directory spec).

Separated from ``main.py`` so the boot orchestrator in
``bootstrap/boot.py`` can import it directly and tests can stub
the resolver without monkey-patching module globals on ``main``.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_user_config_path() -> str:
    """Return the absolute path to the user overrides config file.

    See module docstring for the resolution precedence. This is
    a pure function — it only reads environment variables and
    resolves ``~``; no disk I/O, no mutation.
    """
    env = os.environ.get("UNIFIDECK_USER_CONFIG")
    if env:
        return str(Path(env).expanduser())
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return str(Path(xdg) / "unifideck" / "config.json")
    return str(Path("~/.config/unifideck/config.json").expanduser())
