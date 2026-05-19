"""Deep executable tests — security/secure_io.py.

Source : py_modules/unifideck/security/secure_io.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Hardened atomic file I/O: O_NOFOLLOW reads, world-writable
parent refusal, stale-tmp safe-removal, O_EXCL temp +
os.replace. Real temp files + symlinks + injected OSError
drive every defence branch.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import unifideck.security.secure_io as SIO
from unifideck.security.secure_io import (
    SecureIOError,
    _best_effort_unlink,
    _clear_stale_tmp,
    _ensure_parent_dir,
    secure_read_bytes,
    secure_write_atomic,
)


def test_module_imports() -> None:
    assert SIO.secure_read_bytes is secure_read_bytes


# ========================================================= #
# secure_read_bytes
# ========================================================= #
def test_read_ok(tmp_path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert secure_read_bytes(str(f)) == b"hello"


def test_read_missing(tmp_path) -> None:
    with pytest.raises(SecureIOError,
                       match="refused to read"):
        secure_read_bytes(str(tmp_path / "none"))


def test_read_symlink_refused(tmp_path) -> None:
    target = tmp_path / "real"
    target.write_bytes(b"secret")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(SecureIOError):
        secure_read_bytes(str(link))


def test_read_fdopen_oserror(
    tmp_path, monkeypatch,
) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")

    def _boom(fd, *a: Any, **k: Any):
        raise OSError("read fault")

    monkeypatch.setattr(SIO.os, "fdopen", _boom)
    with pytest.raises(SecureIOError,
                       match="failed to read"):
        secure_read_bytes(str(f))


# ========================================================= #
# _ensure_parent_dir
# ========================================================= #
def test_ensure_parent_creates(tmp_path) -> None:
    p = tmp_path / "deep" / "nested"
    _ensure_parent_dir(str(p), 0o700)
    assert p.is_dir()


def test_ensure_parent_mkdir_oserror(
    tmp_path, monkeypatch,
) -> None:
    def _boom(self, *a: Any, **k: Any):
        raise OSError("mkdir denied")

    monkeypatch.setattr(Path, "mkdir", _boom)
    with pytest.raises(SecureIOError,
                       match="cannot create parent"):
        _ensure_parent_dir(
            str(tmp_path / "x"), 0o700)


def test_ensure_parent_stat_oserror(
    tmp_path, monkeypatch,
) -> None:
    """The explicit os.stat(parent) after a successful
    mkdir fails -> SecureIOError('cannot stat parent').
    Path.mkdir is stubbed to a no-op so its internal
    exist_ok stat does not interfere with the targeted
    os.stat patch."""
    p = tmp_path / "d"
    p.mkdir()
    real_stat = SIO.os.stat
    state = {"after_mkdir": False}

    def _fake_mkdir(self, *a: Any, **k: Any) -> None:
        state["after_mkdir"] = True

    def _selective(path, *a: Any, **k: Any):
        if (state["after_mkdir"]
                and str(path) == str(p)):
            raise OSError("stat denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)
    monkeypatch.setattr(SIO.os, "stat", _selective)
    with pytest.raises(SecureIOError,
                       match="cannot stat parent"):
        _ensure_parent_dir(str(p), 0o700)


def test_ensure_parent_world_writable(
    tmp_path,
) -> None:
    p = tmp_path / "ww"
    p.mkdir()
    os.chmod(p, 0o777)
    with pytest.raises(
        SecureIOError, match="world-writable"):
        _ensure_parent_dir(str(p), 0o700)


# ========================================================= #
# _clear_stale_tmp
# ========================================================= #
def test_clear_stale_tmp_absent(tmp_path) -> None:
    _clear_stale_tmp(
        str(tmp_path / "none.tmp"))  # no-op


def test_clear_stale_tmp_regular_owned(
    tmp_path,
) -> None:
    t = tmp_path / "x.tmp"
    t.write_bytes(b"leftover")
    _clear_stale_tmp(str(t))
    assert not t.exists()


def test_clear_stale_tmp_lstat_oserror(
    tmp_path, monkeypatch,
) -> None:
    def _boom(p):
        raise OSError("lstat denied")

    monkeypatch.setattr(SIO.os, "lstat", _boom)
    with pytest.raises(SecureIOError,
                       match="cannot lstat"):
        _clear_stale_tmp(str(tmp_path / "x.tmp"))


def test_clear_stale_tmp_symlink_refused(
    tmp_path,
) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    link = tmp_path / "x.tmp"
    link.symlink_to(real)
    with pytest.raises(
        SecureIOError, match="non-regular"):
        _clear_stale_tmp(str(link))


def test_clear_stale_tmp_foreign_uid(
    tmp_path, monkeypatch,
) -> None:
    t = tmp_path / "x.tmp"
    t.write_bytes(b"x")
    monkeypatch.setattr(
        SIO.os, "getuid", lambda: 999999)
    with pytest.raises(
        SecureIOError, match="owned by"):
        _clear_stale_tmp(str(t))


def test_clear_stale_tmp_unlink_oserror(
    tmp_path, monkeypatch,
) -> None:
    t = tmp_path / "x.tmp"
    t.write_bytes(b"x")

    def _boom(p):
        raise OSError("unlink denied")

    monkeypatch.setattr(SIO.os, "unlink", _boom)
    with pytest.raises(SecureIOError,
                       match="cannot unlink stale"):
        _clear_stale_tmp(str(t))


# ========================================================= #
# _best_effort_unlink
# ========================================================= #
def test_best_effort_unlink_ok(tmp_path) -> None:
    f = tmp_path / "x"
    f.write_text("x")
    _best_effort_unlink(str(f))
    assert not f.exists()


def test_best_effort_unlink_swallows(
    tmp_path,
) -> None:
    _best_effort_unlink(
        str(tmp_path / "none"))  # swallowed


# ========================================================= #
# secure_write_atomic (integration)
# ========================================================= #
def test_write_atomic_ok(tmp_path) -> None:
    dest = tmp_path / "sub" / "out.bin"
    secure_write_atomic(str(dest), b"payload")
    assert dest.read_bytes() == b"payload"
    assert (os.stat(dest).st_mode & 0o777) == 0o600


def test_write_atomic_overwrites(tmp_path) -> None:
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"old")
    secure_write_atomic(str(dest), b"new")
    assert dest.read_bytes() == b"new"


def test_write_atomic_clears_stale_tmp(
    tmp_path,
) -> None:
    dest = tmp_path / "out.bin"
    stale = Path(str(dest) + ".tmp")
    stale.write_bytes(b"crash leftover")
    secure_write_atomic(str(dest), b"fresh")
    assert dest.read_bytes() == b"fresh"
    assert not stale.exists()


def test_write_atomic_world_writable_refused(
    tmp_path,
) -> None:
    d = tmp_path / "ww"
    d.mkdir()
    os.chmod(d, 0o777)
    with pytest.raises(
        SecureIOError, match="world-writable"):
        secure_write_atomic(
            str(d / "out.bin"), b"x")


def test_write_atomic_tmp_create_oserror(
    tmp_path, monkeypatch,
) -> None:
    dest = tmp_path / "out.bin"

    def _boom(*a: Any, **k: Any):
        raise OSError("open denied")

    monkeypatch.setattr(SIO.os, "open", _boom)
    with pytest.raises(SecureIOError,
                       match="cannot create temp"):
        secure_write_atomic(str(dest), b"x")


def test_write_atomic_commit_oserror(
    tmp_path, monkeypatch,
) -> None:
    dest = tmp_path / "out.bin"

    def _boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(SIO.os, "replace", _boom)
    with pytest.raises(SecureIOError,
                       match="failed to commit"):
        secure_write_atomic(str(dest), b"x")
    # tmp cleaned up best-effort
    assert not Path(str(dest) + ".tmp").exists()


def test_write_atomic_no_parent(tmp_path) -> None:
    """A bare filename (no dirname) skips parent handling."""
    import os as _os

    cwd = _os.getcwd()
    try:
        _os.chdir(tmp_path)
        secure_write_atomic("bare.bin", b"data")
        assert (tmp_path / "bare.bin").read_bytes() \
            == b"data"
    finally:
        _os.chdir(cwd)
