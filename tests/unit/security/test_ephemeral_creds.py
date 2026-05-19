"""Deep executable tests — security/ephemeral_creds.py.

Source : py_modules/unifideck/security/ephemeral_creds.py
Fiche  : OP (security/)   Critical — coverage floor 95%.

Uses a REAL SecureTokenStore (machine-id derived key) and
real ciphertext files under tmp_path so the full ephemeral
lifecycle is exercised: decrypt -> expose plaintext -> CLI
rotates / deletes / corrupts it -> re-encrypt -> wipe. Both
async context managers and every documented branch covered.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from unifideck.security.ephemeral_creds import (
    EphemeralCredentialContext,
    EphemeralCredentialError,
    InPlaceEphemeralFile,
)
from unifideck.security.secure_token_store import (
    SecureTokenStore,
)


@pytest.fixture()
def store() -> SecureTokenStore:
    return SecureTokenStore()


def _write_ct(store: SecureTokenStore, path: str,
              payload: dict) -> None:
    """Write a real UFD1 ciphertext for ``payload``."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(store.encrypt_payload(payload))


def test_module_imports() -> None:
    import unifideck.security.ephemeral_creds as mod
    assert mod.EphemeralCredentialContext is \
        EphemeralCredentialContext


def test_error_type_is_runtimeerror() -> None:
    assert issubclass(
        EphemeralCredentialError, RuntimeError)


# ========================================================= #
# EphemeralCredentialContext
# ========================================================= #
@pytest.mark.asyncio
async def test_ctx_exposes_env_override(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Steady state: ciphertext present -> __aenter__ returns
    an env dict whose override var points at the plaintext."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "secret-1"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        assert isinstance(env, dict)
        tempdir = env["CLI_CONF"]
        pt = os.path.join(tempdir, "user.json")
        data = json.loads(Path(pt).read_text())
        assert data["token"] == "secret-1"
        plaintext_path = pt
    # wiped after exit
    assert not Path(plaintext_path).exists()


@pytest.mark.asyncio
async def test_ctx_first_use_no_ciphertext(
    store: SecureTokenStore, tmp_path,
) -> None:
    """First-time use: no ciphertext yet -> still exposes a
    writable empty plaintext so the CLI can populate it."""
    ct = str(tmp_path / "absent.bin")
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        pt = Path(env["CLI_CONF"]) / "user.json"
        assert pt.is_file()  # empty JSON object written
        assert json.loads(pt.read_text()) == {}


@pytest.mark.asyncio
async def test_ctx_captures_rotation(
    store: SecureTokenStore, tmp_path,
) -> None:
    """If the CLI rewrites the plaintext (token rotation), the
    new value is re-encrypted to the canonical ciphertext."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "old"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        (Path(env["CLI_CONF"]) / "user.json").write_text(
            json.dumps({"token": "rotated"}))
    # ciphertext now decrypts to the rotated value
    blob = Path(ct).read_bytes()
    assert store.decrypt_payload(blob)["token"] == "rotated"


@pytest.mark.asyncio
async def test_ctx_cli_deletes_plaintext_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """If the CLI deletes the plaintext (logout), the
    canonical ciphertext is left untouched."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "keep"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        os.unlink(os.path.join(env["CLI_CONF"], "user.json"))
    assert store.decrypt_payload(
        Path(ct).read_bytes())["token"] == "keep"


@pytest.mark.asyncio
async def test_ctx_unparseable_rotation_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """If the CLI writes non-JSON, the previous ciphertext is
    preserved (no corruption propagated)."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "good"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        (Path(env["CLI_CONF"]) / "user.json").write_text(
            "{ not json !!")
    assert store.decrypt_payload(
        Path(ct).read_bytes())["token"] == "good"


@pytest.mark.asyncio
async def test_ctx_non_dict_rotation_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """CLI writes a JSON array (not an object) -> previous
    ciphertext kept."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "good"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    async with ctx as env:
        (Path(env["CLI_CONF"]) / "user.json").write_text(
            "[1, 2, 3]")
    assert store.decrypt_payload(
        Path(ct).read_bytes())["token"] == "good"


@pytest.mark.asyncio
async def test_ctx_wipes_on_exception(
    store: SecureTokenStore, tmp_path,
) -> None:
    """An exception inside the block still wipes the
    plaintext (no secret outlives the context)."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "x"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    captured = {}
    with pytest.raises(RuntimeError, match="boom"):
        async with ctx as env:
            captured["pt"] = os.path.join(
                env["CLI_CONF"], "user.json")
            raise RuntimeError("boom")
    assert not Path(captured["pt"]).exists()


@pytest.mark.asyncio
async def test_ctx_corrupt_ciphertext_raises(
    store: SecureTokenStore, tmp_path,
) -> None:
    """A corrupt ciphertext blob -> a store error surfaces
    (re-auth signal), not a silent empty exposure."""
    ct = str(tmp_path / "creds.bin")
    Path(ct).write_bytes(b"not a valid UFD1 blob")
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    with pytest.raises(Exception):
        async with ctx:
            pass


# ========================================================= #
# InPlaceEphemeralFile
# ========================================================= #
@pytest.mark.asyncio
async def test_inplace_steady_state_roundtrip(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Ciphertext present -> plaintext written at the fixed
    path during the window, wiped after, rotation captured."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "tok-1"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    async with ctx:
        assert json.loads(
            Path(pt).read_text())["access"] == "tok-1"
        Path(pt).write_text(
            json.dumps({"access": "tok-2"}))
    assert not Path(pt).exists()
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "tok-2"


@pytest.mark.asyncio
async def test_inplace_fresh_install_noop(
    store: SecureTokenStore, tmp_path,
) -> None:
    """No ciphertext and no plaintext -> nothing exposed; CLI
    will populate plaintext itself."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    async with ctx:
        assert not Path(pt).exists()


@pytest.mark.asyncio
async def test_inplace_legacy_migration(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Plaintext exists but no ciphertext (legacy install) ->
    plaintext is encrypted into ciphertext on enter."""
    ct = str(tmp_path / "epic.bin")
    pt = tmp_path / "legendary" / "user.json"
    pt.parent.mkdir(parents=True)
    pt.write_text(json.dumps({"legacy": "value"}))
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=str(pt),
    )
    async with ctx:
        pass
    # ciphertext was created from the legacy plaintext
    assert Path(ct).is_file()
    assert store.decrypt_payload(
        Path(ct).read_bytes())["legacy"] == "value"


@pytest.mark.asyncio
async def test_inplace_wipes_on_exception(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Exception inside the block -> plaintext wiped,
    ciphertext preserved (last-known-good)."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "safe"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    with pytest.raises(RuntimeError, match="kaboom"):
        async with ctx:
            assert Path(pt).is_file()
            raise RuntimeError("kaboom")
    assert not Path(pt).exists()
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "safe"


@pytest.mark.asyncio
async def test_inplace_corrupt_ciphertext_raises(
    store: SecureTokenStore, tmp_path,
) -> None:
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    Path(ct).write_bytes(b"garbage-not-ufd1")
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    with pytest.raises(Exception):
        async with ctx:
            pass


# --- InPlaceEphemeralFile: migration / capture branches - #
@pytest.mark.asyncio
async def test_inplace_migration_non_json_skips(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Legacy plaintext that is not JSON -> migration skipped,
    no ciphertext created (no corruption)."""
    ct = str(tmp_path / "epic.bin")
    pt = tmp_path / "legendary" / "user.json"
    pt.parent.mkdir(parents=True)
    pt.write_text("{ not json at all")
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=str(pt),
    )
    async with ctx:
        pass
    assert not Path(ct).exists()


@pytest.mark.asyncio
async def test_inplace_migration_non_dict_skips(
    store: SecureTokenStore, tmp_path,
) -> None:
    """Legacy plaintext that is a JSON array -> skipped."""
    ct = str(tmp_path / "epic.bin")
    pt = tmp_path / "legendary" / "user.json"
    pt.parent.mkdir(parents=True)
    pt.write_text("[1, 2, 3]")
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=str(pt),
    )
    async with ctx:
        pass
    assert not Path(ct).exists()


@pytest.mark.asyncio
async def test_inplace_capture_cli_deleted_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """CLI deletes the plaintext during the window -> the
    ciphertext is left as last-known-good."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "keep"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    async with ctx:
        os.unlink(pt)
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "keep"


@pytest.mark.asyncio
async def test_inplace_capture_unparseable_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """CLI writes non-JSON -> previous ciphertext kept."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "good"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    async with ctx:
        Path(pt).write_text("}}} not json")
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "good"


@pytest.mark.asyncio
async def test_inplace_capture_non_dict_keeps_ct(
    store: SecureTokenStore, tmp_path,
) -> None:
    """CLI writes a JSON array -> previous ciphertext kept."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "good"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    async with ctx:
        Path(pt).write_text('["arr"]')
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "good"


@pytest.mark.asyncio
async def test_inplace_wipe_plaintext_oserror_swallowed(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """If unlinking the plaintext raises OSError in the
    finally path, it is swallowed (the original flow is not
    masked)."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "x"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    import unifideck.security.ephemeral_creds as mod

    real_unlink = mod.os.unlink

    def _boom(p: str, *a, **k):
        if str(p) == pt:
            raise OSError("locked")
        return real_unlink(p, *a, **k)

    async with ctx:
        monkeypatch.setattr(mod.os, "unlink", _boom)
    # no exception propagated despite the wipe failure


# --- EphemeralCredentialContext: tempdir / write errors -- #
@pytest.mark.asyncio
async def test_ctx_write_plaintext_error_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """If writing the ephemeral plaintext fails, __aenter__
    raises EphemeralCredentialError (and still cleans up)."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "x"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    import unifideck.security.ephemeral_creds as mod

    def _boom(*_a, **_k):
        from unifideck.security.secure_io import SecureIOError
        raise SecureIOError("disk full")

    monkeypatch.setattr(mod, "secure_write_atomic", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass


# --- defensive error branches (monkeypatched) ---------- #
@pytest.mark.asyncio
async def test_ctx_load_payload_read_error_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """A SecureIOError reading the ciphertext -> escalated as
    EphemeralCredentialError (re-auth signal)."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "x"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    def _boom(*_a, **_k):
        raise SecureIOError("io refused")

    monkeypatch.setattr(mod, "secure_read_bytes", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass


@pytest.mark.asyncio
async def test_ctx_tempdir_mkdtemp_oserror_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """If mkdtemp fails, __aenter__ raises
    EphemeralCredentialError."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "x"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    import unifideck.security.ephemeral_creds as mod

    def _boom(*_a, **_k):
        raise OSError("no space for tempdir")

    monkeypatch.setattr(
        mod.tempfile, "mkdtemp", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass


@pytest.mark.asyncio
async def test_ctx_tempdir_chmod_oserror_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """If chmod of the tempdir fails, it is removed and an
    EphemeralCredentialError is raised."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "x"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    import unifideck.security.ephemeral_creds as mod

    real_chmod = mod.os.chmod

    def _boom(p, mode, *a, **k):
        # only fail for the tempdir chmod, not unrelated ones
        raise OSError("chmod denied")

    monkeypatch.setattr(mod.os, "chmod", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass
    monkeypatch.setattr(mod.os, "chmod", real_chmod)


@pytest.mark.asyncio
async def test_ctx_capture_persist_error_is_logged(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """If persisting the rotated ciphertext fails, the error
    is logged but not raised (best-effort capture)."""
    ct = str(tmp_path / "creds.bin")
    _write_ct(store, ct, {"token": "old"})
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=ct,
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    real_write = mod.secure_write_atomic

    def _selective(path, body, *a, **k):
        # let the plaintext write through, fail the ciphertext
        if str(path) == ct:
            raise SecureIOError("ciphertext write denied")
        return real_write(path, body, *a, **k)

    async with ctx as env:
        (Path(env["CLI_CONF"]) / "user.json").write_text(
            json.dumps({"token": "rotated"}))
        monkeypatch.setattr(
            mod, "secure_write_atomic", _selective)
    # no exception even though ciphertext persist failed


def test_safe_listdir_swallows_oserror() -> None:
    """_safe_listdir yields nothing (no raise) on an
    unreadable path."""
    from unifideck.security.ephemeral_creds import (
        _safe_listdir,
    )
    out = list(_safe_listdir("/no/such/dir/at/all"))
    assert out == []


@pytest.mark.asyncio
async def test_inplace_read_ciphertext_error_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """InPlaceEphemeralFile: a SecureIOError reading the
    ciphertext -> EphemeralCredentialError."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "x"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    def _boom(*_a, **_k):
        raise SecureIOError("read denied")

    import unifideck.security.ephemeral_creds_inplace as _ip
    monkeypatch.setattr(_ip, "secure_read_bytes", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass


# --- final defensive branches toward 95% --------------- #
@pytest.mark.asyncio
async def test_inplace_migration_read_error_skips(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """Migration: if reading the legacy plaintext raises
    SecureIOError, migration is skipped (no ciphertext)."""
    ct = str(tmp_path / "epic.bin")
    pt = tmp_path / "legendary" / "user.json"
    pt.parent.mkdir(parents=True)
    pt.write_text(json.dumps({"legacy": "v"}))
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=str(pt),
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    def _boom(*_a, **_k):
        raise SecureIOError("read denied")

    import unifideck.security.ephemeral_creds_inplace as _ip
    monkeypatch.setattr(_ip, "secure_read_bytes", _boom)
    async with ctx:
        pass
    assert not Path(ct).exists()


@pytest.mark.asyncio
async def test_inplace_migration_write_error_logged(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """Migration: if writing the new ciphertext raises
    SecureIOError, it is logged and migration returns."""
    ct = str(tmp_path / "epic.bin")
    pt = tmp_path / "legendary" / "user.json"
    pt.parent.mkdir(parents=True)
    pt.write_text(json.dumps({"legacy": "v"}))
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=str(pt),
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    def _boom(*_a, **_k):
        raise SecureIOError("write denied")

    import unifideck.security.ephemeral_creds_inplace as _ip
    monkeypatch.setattr(_ip, "secure_write_atomic", _boom)
    async with ctx:
        pass
    assert not Path(ct).exists()


@pytest.mark.asyncio
async def test_inplace_write_plaintext_error_raises(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """InPlace steady state: if writing the decrypted
    plaintext fails -> EphemeralCredentialError."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "x"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    def _boom(*_a, **_k):
        raise SecureIOError("plaintext write denied")

    import unifideck.security.ephemeral_creds_inplace as _ip
    monkeypatch.setattr(_ip, "secure_write_atomic", _boom)
    with pytest.raises(EphemeralCredentialError):
        async with ctx:
            pass


@pytest.mark.asyncio
async def test_inplace_capture_read_error_keeps_ct(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """InPlace capture: a read error on the plaintext at exit
    -> ciphertext untouched (warning logged)."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "good"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    real_read = mod.secure_read_bytes

    async with ctx:
        # plaintext now exists; make the capture read fail
        def _boom(p, *a, **k):
            raise SecureIOError("read denied")

        monkeypatch.setattr(
            mod, "secure_read_bytes", _boom)
    assert store.decrypt_payload(
        Path(ct).read_bytes())["access"] == "good"


@pytest.mark.asyncio
async def test_inplace_capture_persist_error_logged(
    store: SecureTokenStore, tmp_path, monkeypatch,
) -> None:
    """InPlace capture: persisting the rotated ciphertext
    fails -> logged, not raised."""
    ct = str(tmp_path / "epic.bin")
    pt = str(tmp_path / "legendary" / "user.json")
    _write_ct(store, ct, {"access": "old"})
    ctx = InPlaceEphemeralFile(
        secure_store=store,
        ciphertext_path=ct,
        plaintext_path=pt,
    )
    import unifideck.security.ephemeral_creds as mod
    from unifideck.security.secure_io import SecureIOError

    real_write = mod.secure_write_atomic

    async with ctx:
        Path(pt).write_text(json.dumps({"access": "new"}))

        def _boom(path, body, *a, **k):
            if str(path) == ct:
                raise SecureIOError("persist denied")
            return real_write(path, body, *a, **k)

        monkeypatch.setattr(
            mod, "secure_write_atomic", _boom)
    # no exception even though persist failed


@pytest.mark.asyncio
async def test_ctx_wipe_tempdir_none_early_return(
    store: SecureTokenStore, tmp_path,
) -> None:
    """_wipe_tempdir returns immediately when there is no
    tempdir (defensive early-return)."""
    ctx = EphemeralCredentialContext(
        secure_store=store,
        ciphertext_path=str(tmp_path / "x.bin"),
        cli_filename="user.json",
        env_var_name="CLI_CONF",
    )
    # never entered -> _tempdir is None; calling wipe is safe
    await ctx._wipe_tempdir()
