"""Moving a wrapper store's session between prefixes.

Every test here is a guard, because the failure modes are asymmetric: not
copying a session costs the user a login prompt, while copying the *wrong* way
signs them out of prefixes that were working, and copying too much corrupts
per-prefix state that happens to live in the same files.

The scenario driving all of it was measured on this Deck on 2026-08-11:
``.bnet-auth`` and ``.template`` byte-identical and frozen at 08:57, while the
game prefix's client had rewritten every session file at 21:15. Twelve hours
of token rotation that never came back, and the user saw
``BLZBNTBGS80000023`` — "Your login session has expired" — on every install
and launch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from unifideck.launcher import wine_registry as wr
from unifideck.launcher import wrapper_session as ws

from _wine_session import CANARY_SECTION, CONFIG, COOKIES, LEDGER, VAULT
from _wine_session import token_of, write_registry

SPEC = ws.SPECS["battlenet"]


def _write(prefix: Path, rel: str, data: bytes, *, mtime: float | None = None) -> Path:
    path = prefix / "drive_c" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _make_prefix(
    root: Path,
    name: str,
    *,
    session: bool = True,
    mtime: float = 1000.0,
    identity: str | None = "GUID-A",
    vault: bytes = b"vault",
    token: str = "tok",
    registry: bool = True,
    stamp: int | None = None,
) -> Path:
    """A prefix with (or without) a signed-in session.

    The registry token is part of "has a session": the login token is a Wine
    registry key, so a files-only prefix is signed OUT however complete its
    ``AppData`` looks.
    """
    prefix = root / name
    if session:
        _write(prefix, VAULT, vault, mtime=mtime)
        _write(prefix, LEDGER, b"ledger", mtime=mtime)
        _write(prefix, COOKIES, b"cookies", mtime=mtime)
        if registry:
            write_registry(
                prefix,
                stamp=int(stamp if stamp is not None else mtime),
                token=token,
            )
    if identity is not None:
        _write(
            prefix, CONFIG,
            json.dumps({"Client": {"GaClientId": identity}}).encode(),
            mtime=mtime,
        )
    (prefix / "drive_c").mkdir(parents=True, exist_ok=True)
    return prefix


# --------------------------------------------------------------------------
# reading a prefix
# --------------------------------------------------------------------------


def test_drive_c_resolves_through_the_umu_self_symlink(tmp_path: Path) -> None:
    """umu makes ``pfx -> .``, so both spellings are the same directory.

    The registry has to be found through the same indirection, or the token
    is invisible in exactly the layout umu actually produces.
    """
    prefix = _make_prefix(tmp_path, "game", registry=False)
    write_registry(prefix.parent / "game_flat", stamp=1000)
    # Rebuild flat: user.reg at the prefix root, pfx a self-symlink onto it.
    (prefix / "user.reg").write_text(
        (prefix.parent / "game_flat" / "pfx" / "user.reg").read_text(),
        encoding="utf-8",
    )
    (prefix / "pfx").symlink_to(".")
    assert ws.resolve_drive_c(prefix) is not None
    assert wr.registry_path(prefix) is not None
    assert ws.has_session(SPEC, prefix) is True


def test_has_session_needs_the_vault_not_merely_the_ledger(tmp_path: Path) -> None:
    """``CachedData.db`` outlives a sign-out, so it cannot be the evidence.

    Measured: its ``login_cache`` (name, battle_tag, account id) was identical
    between the auth prefix and a prefix whose token had rotated. Trusting it
    would report "signed in" forever — the same trap that makes the store keep
    a separate signed-out marker file.
    """
    prefix = _make_prefix(tmp_path, "game", session=False)
    _write(prefix, LEDGER, b"ledger")
    assert ws.has_session(SPEC, prefix) is False


def test_has_session_rejects_an_empty_vault(tmp_path: Path) -> None:
    prefix = _make_prefix(tmp_path, "game", session=False)
    _write(prefix, VAULT, b"")
    assert ws.has_session(SPEC, prefix) is False


def test_fingerprint_tracks_the_vault(tmp_path: Path) -> None:
    """A rotated token makes the prefix newer."""
    prefix = _make_prefix(tmp_path, "game", mtime=1000.0)
    before = ws.fingerprint(SPEC, prefix)
    _write(prefix, VAULT, b"rotated", mtime=2000.0)
    assert ws.fingerprint(SPEC, prefix) > before


def test_fingerprint_ignores_the_licence_ledger(tmp_path: Path) -> None:
    """``CachedData.db`` must not make a prefix look like it has a newer token.

    Found by the install tests: the ledger is licence and telemetry state that
    the client rewrites on its own schedule, so counting it made an auth
    prefix whose ledger had just been written look newer than a game prefix
    holding a freshly rotated token. The capture was then skipped and the
    token lost — the exact failure this module exists to prevent, reintroduced
    by measuring the wrong files.
    """
    prefix = _make_prefix(tmp_path, "game", mtime=1000.0)
    before = ws.fingerprint(SPEC, prefix)
    _write(prefix, LEDGER, b"ledger-rewritten-much-later", mtime=9000.0)
    assert ws.fingerprint(SPEC, prefix) == before


def test_a_rotated_token_wins_over_a_freshly_written_ledger(tmp_path: Path) -> None:
    """The regression above, stated as the capture decision it broke."""
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"stale")
    _write(auth, LEDGER, b"ledger", mtime=9000.0)
    game = _make_prefix(tmp_path, "game", mtime=2000.0, vault=b"rotated")

    assert ws.capture(SPEC, game, auth) is True
    assert (auth / "drive_c" / VAULT).read_bytes() == b"rotated"


def test_fingerprint_ignores_the_game_directory(tmp_path: Path) -> None:
    """The game lives inside these prefixes and is not session material."""
    prefix = _make_prefix(tmp_path, "game")
    before = ws.fingerprint(SPEC, prefix)
    huge = prefix / "drive_c" / ws.GAMES_DIR_NAME / "Diablo" / "game.mpq"
    huge.parent.mkdir(parents=True)
    huge.write_bytes(b"x" * 4096)
    assert ws.fingerprint(SPEC, prefix) == before


# --------------------------------------------------------------------------
# capture — the direction that was missing entirely
# --------------------------------------------------------------------------


def test_capture_brings_a_rotated_session_back_to_auth(tmp_path: Path) -> None:
    """The reported bug, reproduced and fixed in one test."""
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"old")
    game = _make_prefix(tmp_path, "game", mtime=2000.0, vault=b"rotated")

    assert ws.capture(SPEC, game, auth) is True
    assert (auth / "drive_c" / VAULT).read_bytes() == b"rotated"


def test_capture_refuses_an_older_source(tmp_path: Path) -> None:
    """A prefix that has not run must not roll auth back."""
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, vault=b"current")
    stale = _make_prefix(tmp_path, "stale", mtime=1000.0, vault=b"ancient")

    assert ws.capture(SPEC, stale, auth) is False
    assert (auth / "drive_c" / VAULT).read_bytes() == b"current"


def test_capture_refuses_a_signed_out_source(tmp_path: Path) -> None:
    """A prefix the user signed out of must never overwrite auth.

    Newer on disk, but empty of a session: without this guard the sign-out
    would propagate itself into the source of truth.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"good")
    signed_out = _make_prefix(tmp_path, "out", session=False, mtime=9000.0)

    assert ws.capture(SPEC, signed_out, auth) is False
    assert (auth / "drive_c" / VAULT).read_bytes() == b"good"


def test_capture_never_writes_the_template(tmp_path: Path) -> None:
    """The template is a golden image; only sign-in/out may change it.

    Enforced structurally — ``capture`` takes exactly one destination — so
    this test pins the call site's contract rather than a branch.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"old")
    template = _make_prefix(tmp_path, "template", mtime=1000.0, vault=b"old")
    game = _make_prefix(tmp_path, "game", mtime=2000.0, vault=b"rotated")

    assert ws.capture(SPEC, game, auth) is True
    assert (template / "drive_c" / VAULT).read_bytes() == b"old"


def test_capture_from_auth_into_itself_is_a_noop(tmp_path: Path) -> None:
    auth = _make_prefix(tmp_path, "auth")
    assert ws.capture(SPEC, auth, auth) is False


# --------------------------------------------------------------------------
# inject — what makes an idle prefix open signed in
# --------------------------------------------------------------------------


def test_inject_refreshes_a_stale_game_prefix(tmp_path: Path) -> None:
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, vault=b"current")
    game = _make_prefix(tmp_path, "game", mtime=1000.0, vault=b"cloned-months-ago")

    assert ws.inject(SPEC, auth, game) is True
    assert (game / "drive_c" / VAULT).read_bytes() == b"current"


def test_inject_leaves_a_newer_target_alone(tmp_path: Path) -> None:
    """A client that just rotated its own token must not be reset."""
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"older")
    game = _make_prefix(tmp_path, "game", mtime=2000.0, vault=b"just-rotated")

    assert ws.inject(SPEC, auth, game) is False
    assert (game / "drive_c" / VAULT).read_bytes() == b"just-rotated"


def test_inject_refuses_when_auth_has_no_session(tmp_path: Path) -> None:
    """Nothing to deliver, and overwriting a working target would sign out."""
    auth = _make_prefix(tmp_path, "auth", session=False)
    game = _make_prefix(tmp_path, "game", mtime=1000.0, vault=b"working")

    assert ws.inject(SPEC, auth, game) is False
    assert (game / "drive_c" / VAULT).read_bytes() == b"working"


def test_inject_seeds_a_target_that_has_no_session_yet(tmp_path: Path) -> None:
    """A clone that Wine has initialised but nobody has signed into."""
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"current", token="live")
    fresh = _make_prefix(tmp_path, "fresh", session=False, mtime=1000.0)
    # Initialised: it has a registry, just no Battle.net keys in it.
    (fresh / "pfx").mkdir(parents=True, exist_ok=True)
    (fresh / "pfx" / "user.reg").write_text(
        f"WINE REGISTRY Version 2\n\n{CANARY_SECTION} 1\n\"Locale\"=\"0409\"\n",
        encoding="utf-8",
    )

    assert ws.inject(SPEC, auth, fresh) is True
    assert (fresh / "drive_c" / VAULT).read_bytes() == b"current"
    assert token_of(fresh) == "live"


def test_inject_refuses_a_prefix_wine_has_never_initialised(tmp_path: Path) -> None:
    """No ``user.reg`` means no prefix yet, and the token has nowhere to go.

    Delivering the files without the token would leave the prefix looking
    signed in and behaving signed out — the state that produced
    ``ERROR_TOKEN_NOT_FOUND (49)``.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0)
    bare = tmp_path / "bare"
    (bare / "drive_c").mkdir(parents=True)

    assert ws.inject(SPEC, auth, bare) is False
    assert not (bare / "drive_c" / VAULT).exists()


def test_identity_mismatch_blocks_both_directions(tmp_path: Path) -> None:
    """The token is bound to the client instance that minted it.

    Measured: copying the vault without a matching ``Client.GaClientId``
    produced a password form (``browser state changed: LoginCredential``);
    with it the client signed straight in. A mismatch means the copy would be
    rejected anyway, so refusing keeps a working prefix working.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, identity="GUID-A")
    alien = _make_prefix(
        tmp_path, "alien", mtime=3000.0, identity="GUID-B", vault=b"alien",
    )

    assert ws.inject(SPEC, auth, alien) is False
    assert ws.capture(SPEC, alien, auth) is False


def test_unreadable_identity_does_not_block_the_copy(tmp_path: Path) -> None:
    """A prefix mid-clone has no config yet; refusing would strand it."""
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, identity="GUID-A")
    fresh = _make_prefix(
        tmp_path, "fresh", session=False, mtime=1000.0, identity=None,
    )
    (fresh / "pfx").mkdir(parents=True, exist_ok=True)
    (fresh / "pfx" / "user.reg").write_text(
        "WINE REGISTRY Version 2\n", encoding="utf-8",
    )

    assert ws.inject(SPEC, auth, fresh) is True


def test_the_client_config_is_never_copied_as_a_file(tmp_path: Path) -> None:
    """It mixes settings with per-prefix state, so it moves key by key.

    Measured across a rotation: the identity keys (``GaClientId``,
    ``AutoLogin``, ``SavedAccountNames``) were byte-identical in every tier,
    while what actually differed was ``Client.Install.DefaultInstallPath`` and
    the per-game ``LastPlayed``. A file copy would carry those; the session pass
    must not touch the file at all beyond reading the identity off it.

    ``launcher/wrapper_prefs`` owns the key-by-key half, and
    ``test_wrapper_prefs`` covers it. What this asserts is the boundary: the
    per-prefix keys survive an inject.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0)
    game = _make_prefix(tmp_path, "game", mtime=1000.0)
    _write(
        game, CONFIG,
        json.dumps({
            "Client": {
                "GaClientId": "GUID-A",
                "Install": {"DefaultInstallPath": "D:/Games"},
            },
            "Games": {"d1": {"LastPlayed": "1786402946"}},
        }).encode(),
        mtime=1000.0,
    )

    assert ws.inject(SPEC, auth, game) is True
    kept = json.loads((game / "drive_c" / CONFIG).read_text())
    assert kept["Client"]["Install"]["DefaultInstallPath"] == "D:/Games"
    assert kept["Games"]["d1"]["LastPlayed"] == "1786402946"


# --------------------------------------------------------------------------
# purge — making sign-out mean it
# --------------------------------------------------------------------------


def test_purge_removes_the_session_but_not_the_game(tmp_path: Path) -> None:
    prefix = _make_prefix(tmp_path, "game")
    game_file = prefix / "drive_c" / ws.GAMES_DIR_NAME / "Diablo" / "game.mpq"
    game_file.parent.mkdir(parents=True)
    game_file.write_bytes(b"payload")

    assert ws.purge(SPEC, prefix) > 0
    assert ws.has_session(SPEC, prefix) is False
    assert game_file.exists()


# --------------------------------------------------------------------------
# the prefix index the launcher reads
# --------------------------------------------------------------------------


def test_prefix_index_roundtrips_and_merges_per_store(tmp_path: Path) -> None:
    """One file serves every wrapper store, so a write must not clobber peers."""
    ws.write_prefix_index("battlenet", auth=tmp_path / "a", template=tmp_path / "t")
    ws.write_prefix_index("ubisoft", auth=tmp_path / "ua", template=tmp_path / "ut")

    assert ws.auth_prefix("battlenet") == tmp_path / "a"
    assert ws.template_prefix("battlenet") == tmp_path / "t"
    assert ws.auth_prefix("ubisoft") == tmp_path / "ua"


def test_prefix_index_path_follows_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved per call, not captured at import.

    A module-level constant is evaluated before pytest's autouse fixture
    redirects ``HOME``, and the first run of this suite duly wrote pytest temp
    paths into the real user's data directory — the leak ``tests/conftest.py``
    exists to prevent for the launcher's event file.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert ws.prefix_index_path() == tmp_path / "xdg" / "unifideck" / "wrapper_prefixes.json"


def test_missing_index_reports_no_prefixes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))
    assert ws.auth_prefix("battlenet") is None


def test_unknown_store_has_no_spec() -> None:
    assert ws.spec_for("epic") is None
    assert ws.spec_for(None) is None


def test_a_truncated_file_never_replaces_real_content(tmp_path: Path) -> None:
    """A client killed mid-write leaves a zero-length file behind.

    The material carried alongside a session includes state the store reads
    for other purposes — Battle.net's ``CachedData.db`` is its licence ledger,
    and ``is_available()`` keys on it — so a zero-length copy landing there
    would report the whole store as signed out.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, vault=b"old")
    _write(auth, LEDGER, b"a real ledger", mtime=1000.0)
    game = _make_prefix(tmp_path, "game", mtime=2000.0, vault=b"rotated")
    _write(game, LEDGER, b"", mtime=2000.0)

    assert ws.capture(SPEC, game, auth) is True
    assert (auth / "drive_c" / VAULT).read_bytes() == b"rotated"
    assert (auth / "drive_c" / LEDGER).read_bytes() == b"a real ledger"


# --------------------------------------------------------------------------
# the registry token — the half that a files-only copy silently omitted
# --------------------------------------------------------------------------


def test_files_without_the_registry_token_are_not_a_session(tmp_path: Path) -> None:
    """The bug that shipped, stated as an assertion.

    A prefix with every session *file* and no ``UnifiedAuth`` key is signed
    OUT. Treating it as signed in is what let a capture propagate a tokenless
    session to auth and then to every clone, and the client answered
    ``ERROR_TOKEN_NOT_FOUND (49)``.
    """
    prefix = _make_prefix(tmp_path, "files-only", registry=False)
    assert ws.has_session(SPEC, prefix) is False


def test_capture_moves_the_registry_token(tmp_path: Path) -> None:
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, token="stale")
    game = _make_prefix(tmp_path, "game", mtime=2000.0, token="rotated")

    assert ws.capture(SPEC, game, auth) is True
    assert token_of(auth) == "rotated"


def test_the_registry_merge_keeps_every_unrelated_section(tmp_path: Path) -> None:
    """``user.reg`` also holds the installed game's own paths and the locale.

    Copying the file wholesale would carry the auth prefix's registry over a
    game prefix's, so the transplant is section-by-section.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, token="fresh")
    game = _make_prefix(tmp_path, "game", mtime=1000.0, token="old")
    reg = game / "pfx" / "user.reg"
    before = reg.read_text()

    assert ws.inject(SPEC, auth, game) is True
    after = reg.read_text()
    assert CANARY_SECTION in after
    assert after.count("\n[") == before.count("\n[")
    # The per-game sibling key must not have been replaced by auth's.
    assert '"URI_TOKEN"="per-game"' in after


def test_registry_ordering_beats_file_mtime(tmp_path: Path) -> None:
    """Wine's per-section write time is the rotation clock.

    A prefix whose files were touched later but whose token is older must not
    win: pairing a new-looking file set with an old token is the inconsistent
    state the server rejects.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=1000.0, stamp=9000, token="newertoken")
    game = _make_prefix(tmp_path, "game", mtime=8000.0, stamp=1000, token="oldertoken")

    assert ws.capture(SPEC, game, auth) is False
    assert token_of(auth) == "newertoken"


def test_a_busy_destination_blocks_the_whole_copy(tmp_path: Path) -> None:
    """A live wineserver rewrites the registry from memory when it exits.

    Writing underneath it is discarded with no error, so the copy must fail
    loudly rather than deliver files whose token never landed.
    """
    auth = _make_prefix(tmp_path, "auth", mtime=2000.0, token="fresh")
    game = _make_prefix(tmp_path, "game", mtime=1000.0, token="old")

    assert ws.inject(SPEC, auth, game, target_busy=True) is False
    assert token_of(game) == "old"
    assert (game / "drive_c" / VAULT).read_bytes() == b"vault"


def test_purge_removes_the_registry_token(tmp_path: Path) -> None:
    """Deleting the files but keeping the key is not a sign-out at all."""
    prefix = _make_prefix(tmp_path, "game", mtime=1000.0)

    assert ws.purge(SPEC, prefix) > 0
    assert token_of(prefix) is None
    assert ws.has_session(SPEC, prefix) is False
    # Sign-out must not take the locale with it.
    assert CANARY_SECTION in (prefix / "pfx" / "user.reg").read_text()
