"""Tests for core/io/async_file_ops.py (OP-06a)."""
from __future__ import annotations

import asyncio
import os
import stat

import pytest

from unifideck.core.io import async_file_ops as aio


@pytest.mark.asyncio
async def test_exists_true(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    assert await aio.exists(str(f)) is True


@pytest.mark.asyncio
async def test_exists_false():
    assert await aio.exists("/nonexistent_xyz_test") is False


@pytest.mark.asyncio
async def test_is_file_and_is_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert await aio.is_file(str(f)) is True
    assert await aio.is_dir(str(f)) is False
    assert await aio.is_dir(str(tmp_path)) is True
    assert await aio.is_file(str(tmp_path)) is False


@pytest.mark.asyncio
async def test_listdir(tmp_path):
    (tmp_path / "a").touch()
    (tmp_path / "b").touch()
    entries = await aio.listdir(str(tmp_path))
    assert sorted(entries) == ["a", "b"]


@pytest.mark.asyncio
async def test_listdir_missing():
    assert await aio.listdir("/nonexistent_xyz") == []


@pytest.mark.asyncio
async def test_stat(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("data")
    result = await aio.stat(str(f))
    assert result is not None
    assert result.st_size == 4


@pytest.mark.asyncio
async def test_stat_missing():
    assert await aio.stat("/nonexistent_xyz") is None


@pytest.mark.asyncio
async def test_makedirs(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    assert await aio.makedirs(str(deep)) is True
    assert deep.is_dir()


@pytest.mark.asyncio
async def test_copy_and_move(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("content")
    dst = tmp_path / "dst.txt"
    assert await aio.copy(str(src), str(dst)) is True
    assert dst.read_text() == "content"

    moved = tmp_path / "moved.txt"
    assert await aio.move(str(dst), str(moved)) is True
    assert moved.exists()
    assert not dst.exists()


@pytest.mark.asyncio
async def test_remove_file_and_dir(tmp_path):
    f = tmp_path / "to_remove.txt"
    f.write_text("bye")
    assert await aio.remove(str(f)) is True
    assert not f.exists()

    d = tmp_path / "subdir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    assert await aio.remove(str(d)) is True
    assert not d.exists()


@pytest.mark.asyncio
async def test_read_text_and_write_text(tmp_path):
    f = tmp_path / "rw.txt"
    assert await aio.write_text(str(f), "hello world") is True
    assert await aio.read_text(str(f)) == "hello world"


@pytest.mark.asyncio
async def test_read_text_missing():
    assert await aio.read_text("/nonexistent_xyz") is None


@pytest.mark.asyncio
async def test_write_text_atomic_creates_parents(tmp_path):
    deep = tmp_path / "deep" / "nested" / "file.txt"
    assert await aio.write_text(str(deep), "nested") is True
    assert deep.read_text() == "nested"


@pytest.mark.asyncio
async def test_write_json_chmod(tmp_path):
    f = tmp_path / "secret.json"
    assert await aio.write_json(str(f), {"token": "x"}, mode=0o600) is True
    perms = stat.S_IMODE(os.stat(str(f)).st_mode)
    assert perms == 0o600


@pytest.mark.asyncio
async def test_read_json_returns_dict(tmp_path):
    f = tmp_path / "data.json"
    await aio.write_json(str(f), {"key": "val", "neg": -1, "n": None})
    data = await aio.read_json(str(f))
    assert data == {"key": "val", "neg": -1, "n": None}


@pytest.mark.asyncio
async def test_read_json_corrupt_returns_empty(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{{{not json")
    assert await aio.read_json(str(f)) == {}


@pytest.mark.asyncio
async def test_read_json_missing_returns_empty():
    assert await aio.read_json("/nonexistent_xyz.json") == {}


@pytest.mark.asyncio
async def test_read_json_non_dict_returns_empty(tmp_path):
    f = tmp_path / "array.json"
    f.write_text("[1, 2, 3]")
    assert await aio.read_json(str(f)) == {}


@pytest.mark.asyncio
async def test_write_bytes_and_read(tmp_path):
    f = tmp_path / "binary.dat"
    assert await aio.write_bytes(str(f), b"\x00\x01\x02") is True
    assert f.read_bytes() == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_write_bytes_creates_parents(tmp_path):
    f = tmp_path / "deep" / "nested" / "data.bin"
    assert await aio.write_bytes(str(f), b"hello") is True
    assert f.read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_copy_missing_src(tmp_path):
    assert await aio.copy("/nonexistent", str(tmp_path / "dst")) is False


@pytest.mark.asyncio
async def test_move_missing_src(tmp_path):
    assert await aio.move("/nonexistent", str(tmp_path / "dst")) is False


@pytest.mark.asyncio
async def test_remove_nonexistent():
    assert await aio.remove("/nonexistent_xyz_test") is True  # nothing to remove = success


@pytest.mark.asyncio
async def test_ensure_dir(tmp_path):
    d = tmp_path / "ensured"
    assert await aio.ensure_dir(str(d)) is True
    assert d.is_dir()


@pytest.mark.asyncio
async def test_makedirs_bad_permissions():
    result = await aio.makedirs("/proc/fake_dir_test")
    assert result is False


@pytest.mark.asyncio
async def test_stat_returns_size(tmp_path):
    f = tmp_path / "sized.txt"
    f.write_text("12345")
    s = await aio.stat(str(f))
    assert s is not None
    assert s.st_size == 5


@pytest.mark.asyncio
async def test_write_json_serialization_error(tmp_path):
    """Non-serializable data should return False."""
    f = tmp_path / "bad.json"
    result = await aio.write_json(str(f), {"fn": lambda: None})
    assert result is False
