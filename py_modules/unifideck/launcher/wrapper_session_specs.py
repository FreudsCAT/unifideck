"""What a wrapper store's session consists of, per store.

py_modules/unifideck/launcher/wrapper_session_specs.py

The data half of session propagation; ``wrapper_session`` is the behaviour
half. Split because they change for different reasons and at different rates:
the mechanism is stable, while these declarations track whatever a vendor
client happens to do this release.

**What is generic is the mechanism; what lives here is the evidence.** A
generic guess about which files constitute a session, or about what a
signed-out prefix looks like, would be exactly the mistake the guards exist to
prevent — the same reason ``stores/shared/prefix_placement`` refuses to own
``remover``/``holds_game``. Adding a store is a row in :data:`SPECS`.

Measured Battle.net layout (client build 17651, 2026-08-11).

**The login token is a registry key, not a file.** That is the single fact
that makes this work, and getting it wrong is why a first attempt shipped a
session the server answered with ``ERROR_TOKEN_NOT_FOUND (49)``. The client's
own log says it plainly: ``BattleNetLogin::DeleteToken(): Deleting registry
token``. The keys live in ``user.reg`` under ``Software\\Blizzard
Entertainment\\Battle.net\\`` — ``UnifiedAuth`` (the token),
``EncryptionKey``, ``Identity``. Those are exactly the three names the
``stores/battlenet/prefix/manager.py`` docstring recorded from its on-device
experiment; searching for them under ``AppData`` finds nothing because they
were never files. ``launcher/wine_registry`` moves them.

It is also why a whole-prefix ``rsync`` clone opens signed in: it carries
``user.reg``. Any copy that moves only the client's files cannot.

The files that travel with the token, and why each is classified as it is:

    AppData/Local/Battle.net/Account/<accountid>/account.db
        128 KB fixed size, fully encrypted; 12 of its 32 pages differed
        between the auth prefix and a prefix whose client had run. Rotates
        with the session, but is not the token itself. Fixed size means a
        shrink test — Ubisoft's logout signature — cannot work here.
    AppData/Local/Battle.net/BrowserCaches/{common,<accountid>}/
        CEF cookie jars: bnet.pam, bnet.extra, web.id, JSESSIONID, login.key.
        The web half of the session.
    AppData/Local/Battle.net/CachedData.db
        NOT the token, and never evidence: its ``login_cache`` and
        ``key_value_store`` were identical across a rotation, and
        ``battlenet/store.py`` documents the licence ledger as surviving a
        sign-out. Carried for ownership freshness only.
    AppData/Roaming/Battle.net/Battle.net.config
        **Never copied.** ``Client.GaClientId`` is identical in every tier
        because the prefixes are clones, so it is an *identity to verify*, not
        state to move. The same file holds per-prefix
        ``Install.DefaultInstallPath`` and per-game ``LastPlayed``, which
        copying would corrupt.

Stdlib-only; runs under the SYSTEM python (3.10-3.14).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

GAMES_DIR_NAME = "games"


def resolve_drive_c(prefix: Path | str) -> Path | None:
    """Resolve a prefix's ``drive_c`` across both layouts, or None.

    umu creates ``pfx -> .`` as a self-symlink, so ``<prefix>/drive_c`` and
    ``<prefix>/pfx/drive_c`` are the same directory — and both spellings
    occur in the wild. The naive combine is what made Ubisoft's recovery path
    fail to find a ``upc.exe`` that was genuinely present.
    """
    root = Path(prefix)
    modern = root / "pfx" / "drive_c"
    if modern.is_dir():
        return modern
    legacy = root / "drive_c"
    return legacy if legacy.is_dir() else None


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """One wrapper store's session material and how to judge it.

    Paths are ``drive_c``-relative and may contain a single ``*`` component
    (Battle.net scopes its vault by numeric account id, which we discover
    rather than hardcode).
    """

    store: str
    files: tuple[str, ...]
    trees: tuple[str, ...] = ()
    # Subset of ``files`` whose presence (non-empty) proves a session exists.
    # Deliberately narrower than ``files``: Battle.net's CachedData.db travels
    # with the session but survives a sign-out, so trusting it as evidence
    # would report "signed in" forever.
    evidence: tuple[str, ...] = ()
    # ``user.reg`` key prefixes holding the token. Moved surgically, section by
    # section, because the same file carries the installed game's own paths.
    # A store with none leaves this empty and nothing registry-related runs.
    registry_keys: tuple[str, ...] = ()
    # Reads the prefix-bound identity the session is cryptographically tied
    # to. Injection is refused across a mismatch. Per-store because the
    # sources are unrelated: a JSON key for Battle.net, a registry key for
    # Ubisoft's DPAPI vault.
    identity: Callable[[Path], str | None] | None = field(
        default=None, compare=False,
    )

    def expand(self, drive_c: Path, patterns: tuple[str, ...]) -> list[Path]:
        """Resolve ``patterns`` against ``drive_c``, expanding any ``*``."""
        found: list[Path] = []
        for pattern in patterns:
            if "*" in pattern:
                found.extend(sorted(drive_c.glob(pattern)))
            else:
                found.append(drive_c / pattern)
        return found


def read_gaclientid(prefix: Path) -> str | None:
    """Battle.net's client-instance id, the value its token is bound to.

    Measured: copying the vault without this produced a password form
    (``browser state changed: LoginCredential``); with it the client signed
    straight in. It is identical across our tiers because they are clones, so
    a mismatch means the prefix is not one of ours and its vault would be
    rejected anyway.
    """
    drive_c = resolve_drive_c(prefix)
    if drive_c is None:
        return None
    config = (
        drive_c
        / "users/steamuser/AppData/Roaming/Battle.net/Battle.net.config"
    )
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    client = data.get("Client")
    if not isinstance(client, dict):
        return None
    value = client.get("GaClientId")
    return value if isinstance(value, str) and value else None


_BNET_LOCAL = "users/steamuser/AppData/Local/Battle.net"

# Exactly the three keys the on-device experiment in ``manager.py`` named, and
# no more. The sibling ``Launch Options`` key under the same parent is
# deliberately excluded: it carries per-game subkeys (``Launch Options\\OSI``
# appeared once a game had been launched), and spreading one game's launch
# options into every other prefix is not something a session transplant should
# do. ``UnifiedAuth`` is the token; its section timestamp is the rotation clock.
_BNET_REG = "Software\\\\Blizzard Entertainment\\\\Battle.net\\\\"

# One row per wrapper store. Ubisoft joins this table when its private
# ``session/`` package is ported onto the shared layer; its ``identity``
# reader is the ``system.reg`` MachineGuid probe that guards its DPAPI vault.
SPECS: dict[str, SessionSpec] = {
    "battlenet": SessionSpec(
        store="battlenet",
        files=(
            f"{_BNET_LOCAL}/Account/*/account.db",
            f"{_BNET_LOCAL}/CachedData.db",
        ),
        trees=(f"{_BNET_LOCAL}/BrowserCaches",),
        evidence=(f"{_BNET_LOCAL}/Account/*/account.db",),
        registry_keys=(
            f"{_BNET_REG}UnifiedAuth",
            f"{_BNET_REG}EncryptionKey",
            f"{_BNET_REG}Identity",
        ),
        identity=read_gaclientid,
    ),
}


def spec_for(store: str | None) -> SessionSpec | None:
    """The session spec for ``store``, or None when it has no session."""
    return SPECS.get(store) if store else None


# ── the prefix index the backend writes for us ─────────────────────────────


