"""Propagating a wrapper store's launcher *settings* between Wine prefixes.

py_modules/unifideck/launcher/wrapper_prefs.py

The smaller sibling of ``wrapper_session``, and the same shape of problem. A
wrapper store runs a vendor client inside the prefix, Unifideck gives every game
its own prefix, and the client keeps **both** its session and its settings in
whichever prefix it ran in. ``wrapper_session`` moves the session. Nothing moved
the settings, so the reported bug was a launcher language that "reverts to the
default every time you launch a game": each game's client had its own copy of
the settings file, and a change made in one reached no other.

Three things make this a separate module rather than more of ``wrapper_session``:

* **It is a merge, not a copy.** The settings file is per-prefix *and* shared:
  the same JSON holds the user's language next to this prefix's install path and
  this game's ``LastPlayed``. Copying the file corrupts the second kind. So the
  copy is key by key, minus a denylist the store declares in
  :class:`~unifideck.launcher.wrapper_session_specs.PrefsSpec`.
* **The session's ordering rule is the wrong rule.** ``inject`` refuses when the
  source's session is not newer than the target's, and the auth prefix's token is
  routinely *older* than a game prefix's because the game prefix rotated it last.
  Gating settings on that would skip the merge in the normal case, which is the
  bug. Settings are ordered by their own file's mtime instead.
* Volumetry: ``wrapper_session`` is near the file cap.

**Newest change wins, with the auth prefix as the hub.** A prefix that just ran
merges its settings back to auth on teardown; a prefix about to run takes auth's
on the way in. Both legs refuse to write over a *newer* destination, and that
refusal is load-bearing rather than an optimisation: the launcher can be
SIGKILLed (the Steam stop button and the QAM "X" both take that path), so a
capture can be missed, and without the guard the next launch would push auth's
stale settings back over the change the user had just made locally.

Stdlib-only, and it must not import ``wrapper_session`` - that module imports
this one. Runs under the SYSTEM python (3.10-3.14), not Decky's bundled 3.11.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from unifideck.launcher.wrapper_session_specs import (
    PrefsSpec,
    SessionSpec,
    resolve_drive_c,
)

logger = logging.getLogger(__name__)

__all__ = [
    "bootstrapper_locale",
    "config_path",
    "ensure_locale_seeded",
    "merge",
    "read_prefs",
]

# One key, any name. Matches ``SessionSpec.expand``'s convention for a path
# component discovered at runtime rather than known.
_WILDCARD = "*"

# BCP-47 tags to Battle.net's own locale codes. Every code the client build
# under test supports is listed; an unrecognised tag is left alone (the seed
# is a no-op), which is safer than guessing.
_BNET_UI_LOCALES: dict[str, str] = {
    "en-US": "enUS",
    "de-DE": "deDE",
    "fr-FR": "frFR",
    "es-ES": "esES",
    "it-IT": "itIT",
    "pt-BR": "ptBR",
    "ru-RU": "ruRU",
    "pl-PL": "plPL",
    "ko-KR": "koKR",
    "zh-CN": "zhCN",
    "zh-TW": "zhTW",
    "ja-JP": "jaJP",
    "es-MX": "esMX",
}

# Written into the auth prefix the first time the plugin locale is seeded, so
# an explicit user choice that happens to be enUS is never overwritten.
_LOCALE_SEED_MARKER = ".unifideck_battlenet_locale_seeded.v1"

# What the bootstrapper falls back to. Its own fallback, per the strings in
# the installer binary, is "Fallback region not found in config, us".
_DEFAULT_BNET_LOCALE = "enUS"

# ── prefix index ───────────────────────────────────────────────────────────

# Maintained here rather than in ``wrapper_session`` for the same reason
# ``prefix_index_path`` was first written there: the two halves of this
# package must not import each other. The inline path is 4 lines rather than
# an import cycle.


def _index_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "unifideck" / "wrapper_prefixes.json"


def _read_index_locale(store: str) -> str | None:
    """The plugin's locale, as written by the backend into the prefix index."""
    try:
        raw = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    row = raw.get(store) if isinstance(raw, dict) else None
    value = row.get("locale") if isinstance(row, dict) else None
    return value if isinstance(value, str) and value else None


def bootstrapper_locale(store: str) -> str:
    """The locale to hand the vendor bootstrapper for ``store``.

    Distinct from the seeding path below in one way that matters: an
    unrecognised tag falls back to :data:`_DEFAULT_BNET_LOCALE` rather than
    being left alone. Seeding a preference is optional and a no-op is fine;
    this value is a **command-line argument the installer blocks on**. Launched
    without a usable locale the Battle.net bootstrapper stops on
    ``STATE_SELECT_LANGUAGE``, and in Gaming Mode that wizard has no gamescope
    session to render into, so it waits behind everything for the full
    30-minute timeout while the user looks at a Sign In button that did
    nothing. Passing a locale the client does not ship risks exactly that, so
    only codes known to :data:`_BNET_UI_LOCALES` are ever passed through.

    Why this is worth doing at all, given it was hardcoded ``enUS`` and worked:
    the bootstrapper derives its *region* from the locale (``Configuration:
    locale=enUS region=US``) and warms the Agent's content store for that
    region before anyone has logged in. A non-US account then invalidates the
    whole warm-up on first login, which is what
    ``launcher/wrapper_client_cache`` exists to stop being re-paid. Matching
    the locale to the user gets the region right more often at no cost.
    """
    tag = _read_index_locale(store)
    return _BNET_UI_LOCALES.get(tag or "", _DEFAULT_BNET_LOCALE)


# ── locale seeding ─────────────────────────────────────────────────────────


def _install_section_key(config: dict[str, Any]) -> str | None:
    """The top-level key that names the client's install section, or None.

    The measured layout stores launcher settings under a hash of the client's
    install path (``5a61123b37cafce1``). Discovered rather than hardcoded: a
    future client build could change the hash, and ``Client`` / ``Games`` are
    the only two fixed section names.
    """
    for key, value in config.items():
        if key not in ("Client", "Games") and isinstance(value, dict) and "Client" in value:
            return key
    return None


def _seed_marker_path(auth_prefix: Path) -> Path:
    return Path(auth_prefix) / _LOCALE_SEED_MARKER


def ensure_locale_seeded(spec: SessionSpec, auth_prefix: Path) -> bool:
    """Write the plugin's locale into ``auth_prefix`` once, on the first launch.

    Battle.net's factory default is ``enUS``, and the plugin already picks a
    locale for the install-language modal. Without this seed the client stays
    at ``enUS`` until the user finds the setting buried in the launcher —
    which is the exact complaint the modal was built to prevent.

    Marker-gated: once stamped, never re-seeded. An explicit user change
    captures back to auth through the normal merge, and a later reset to
    ``enUS`` must be kept rather than silently reverted by the seed.

    Never raises. A fresh auth prefix whose Wine has not yet run has no config
    file to seed, and that is a no-op rather than a failure — the next launch
    that reaches this function will have one.
    """
    prefs = spec.prefs
    if prefs is None:
        return False
    auth = Path(auth_prefix)
    if _seed_marker_path(auth).exists():
        return False
    locale = _read_index_locale(spec.store)
    if locale is None:
        return False
    bnet_locale = _BNET_UI_LOCALES.get(locale)
    if bnet_locale is None or bnet_locale == "enUS":
        # Nothing to do (it is the client's own default), but stamp the marker
        # so the check above is one stat from here on.
        _stamp_seed_marker(auth)
        return False
    path = config_path(spec, auth)
    if path is None:
        return False
    config = _load(path)
    if not config:
        return False
    section = _install_section_key(config)
    if section is None:
        _stamp_seed_marker(auth)
        return False
    section_client = config[section].get("Client")
    if not isinstance(section_client, dict):
        _stamp_seed_marker(auth)
        return False
    current = section_client.get("Language")
    if current != "enUS":
        # Already explicitly set (by the user or a previous capture) — do not
        # overwrite. The marker ensures we never re-enter this function.
        _stamp_seed_marker(auth)
        return False
    if current == bnet_locale:
        # Should not happen (bnet_locale != enUS), but be correct if it does.
        _stamp_seed_marker(auth)
        return False
    section_client["Language"] = bnet_locale
    if not _atomic_write(path, config):
        return False
    _stamp_seed_marker(auth)
    logger.info(
        "[wrapper_prefs] %s: seeded launcher language %s into auth (%s)",
        spec.store, bnet_locale, auth.name,
    )
    return True


def _stamp_seed_marker(auth: Path) -> None:
    """Write the marker. A failure here is a warning, not an error."""
    try:
        _seed_marker_path(auth).write_text("", encoding="utf-8")
    except OSError as exc:
        logger.warning("[wrapper_prefs] cannot stamp the seed marker: %s", exc)


def config_path(spec: SessionSpec, prefix: Path | str) -> Path | None:
    """Where ``prefix`` keeps the vendor client's settings, or None.

    None covers both "this store has no settings file" and "this prefix has no
    ``drive_c`` yet", which are the same thing to every caller here.
    """
    prefs = spec.prefs
    if prefs is None:
        return None
    drive_c = resolve_drive_c(prefix)
    return None if drive_c is None else drive_c / prefs.file


def read_prefs(spec: SessionSpec, prefix: Path | str) -> dict[str, Any] | None:
    """``prefix``'s settings as a dict, or None when there are none to read.

    Never raises. A prefix mid-clone has no file, a client killed mid-write can
    leave a truncated one, and neither is worth an exception on a path that runs
    on every launch.
    """
    path = config_path(spec, prefix)
    if path is None:
        return None
    return _load(path)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _excluded(trail: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    """Whether the dotted path ``trail`` is covered by ``exclude``.

    A pattern covers its whole subtree, so ``Games`` answers True for
    ``Games.d1.LastPlayed`` and the walk never descends into it.
    """
    for pattern in exclude:
        parts = pattern.split(".")
        if len(parts) > len(trail):
            continue
        # Non-strict on purpose: a shorter pattern matches a longer trail, which
        # is what makes a pattern cover its subtree.
        if all(
            p in (_WILDCARD, t)
            for p, t in zip(parts, trail, strict=False)
        ):
            return True
    return False


def _merge_into(
    source: dict[str, Any],
    target: dict[str, Any],
    exclude: tuple[str, ...],
    trail: tuple[str, ...] = (),
) -> int:
    """Merge ``source`` into ``target`` in place. Returns values written.

    Additive: a key the target has and the source does not survives untouched.
    That is the same choice ``wrapper_session._copy_session`` documents, for the
    same reason - the destination's own keys include facts about the game
    installed in it, and a pass that reached too far would be unrecoverable
    where a stale preference is merely wrong until the user changes it again.
    """
    written = 0
    for key, value in source.items():
        here = (*trail, key)
        if _excluded(here, exclude):
            continue
        current = target.get(key)
        if isinstance(value, dict):
            written += _merge_branch(value, target, key, current, exclude, here)
        elif current != value:
            target[key] = value
            written += 1
    return written


def _merge_branch(
    value: dict[str, Any],
    target: dict[str, Any],
    key: str,
    current: Any,
    exclude: tuple[str, ...],
    here: tuple[str, ...],
) -> int:
    """Merge one nested section, creating it in ``target`` only if non-empty.

    A section whose every key is excluded must not appear in the destination at
    all: writing an empty ``"Games": {}`` into a prefix would be a change to the
    file for no reason, and the client rewrites what it does not recognise.
    """
    if isinstance(current, dict):
        return _merge_into(value, current, exclude, here)
    branch: dict[str, Any] = {}
    written = _merge_into(value, branch, exclude, here)
    if written:
        target[key] = branch
    return written


def _source_is_newer(source: Path, target: Path) -> bool:
    """Whether ``source``'s settings are the more recent of the two.

    A target with no readable settings file counts as older: a freshly cloned
    prefix should take the current settings, not keep whatever the clone froze.
    """
    try:
        source_mtime = source.stat().st_mtime
    except OSError:
        return False
    try:
        return source_mtime > target.stat().st_mtime
    except OSError:
        return True


def _atomic_write(path: Path, payload: dict[str, Any]) -> bool:
    """Replace ``path`` atomically, matching ``wine_registry._atomic_write``.

    The client reads this file at startup and rewrites it wholesale from memory,
    so a torn read would be a torn settings file rather than a retry.

    The parent is created if missing: a prefix umu has initialised but no client
    has yet run in has a ``drive_c`` and none of the ``AppData`` tree under it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(
            prefix=".bnetcfg.", suffix=".tmp", dir=str(path.parent),
        )
    except OSError as exc:
        logger.warning("[wrapper_prefs] cannot prepare %s: %s", path, exc)
        return False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=4)
            stream.flush()
            os.fsync(stream.fileno())
        Path(tmp_name).replace(path)
    except OSError as exc:
        logger.warning("[wrapper_prefs] could not write %s: %s", path, exc)
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink()
        return False
    return True


def merge(
    spec: SessionSpec,
    source: Path,
    target: Path,
    *,
    target_busy: bool = False,
) -> bool:
    """Carry ``source``'s launcher settings into ``target``. Never raises.

    Refused when the target has a live client (the client rewrites the file from
    memory when it exits, so the write would vanish with every log line still
    saying success - the same failure ``wine_registry.registry_is_writable``
    exists to prevent), and when the target's settings are already at least as
    recent as the source's.

    The caller owns the identity check: this module cannot import
    ``wrapper_session``, and both call sites there already run it.
    """
    prefs = spec.prefs
    if prefs is None or Path(source).resolve() == Path(target).resolve():
        return False
    source_path = config_path(spec, source)
    target_path = config_path(spec, target)
    if source_path is None or target_path is None:
        return False
    if target_busy:
        logger.info(
            "[wrapper_prefs] %s: %s has a live client - not writing settings "
            "underneath it", spec.store, Path(target).name,
        )
        return False
    if not _source_is_newer(source_path, target_path):
        return False
    return _apply(spec, prefs, source_path, target_path, Path(target).name)


def _apply(
    spec: SessionSpec,
    prefs: PrefsSpec,
    source_path: Path,
    target_path: Path,
    label: str,
) -> bool:
    """The merge itself, once every guard has passed."""
    incoming = _load(source_path)
    if not incoming:
        return False
    current = _load(target_path)
    if current is None:
        # No usable file to merge into. Writing a filtered copy is still right:
        # it is what a fresh prefix needs, and the excluded keys are exactly the
        # ones the client regenerates for itself.
        current = {}
    written = _merge_into(incoming, current, prefs.exclude)
    if not written:
        return False
    if not _atomic_write(target_path, current):
        return False
    logger.info(
        "[wrapper_prefs] %s: carried %d launcher setting(s) into %s",
        spec.store, written, label,
    )
    return True
