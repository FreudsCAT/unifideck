"""Tests for core/io/safe_file_op.py (OP-06b)."""
from __future__ import annotations

import asyncio

import pytest

from unifideck.core.io.safe_file_op import safe_file_op


def test_sync_returns_default_on_oserror():
    @safe_file_op(default=42)
    def reader(path):
        raise OSError("disk error")

    assert reader("/x") == 42


def test_sync_passes_through_non_oserror():
    @safe_file_op(default=42)
    def reader(path):
        raise ValueError("not an OSError")

    with pytest.raises(ValueError, match="not an OSError"):
        reader("/x")


def test_sync_returns_real_value():
    @safe_file_op(default=42)
    def reader(path):
        return "real"

    assert reader("/x") == "real"


@pytest.mark.asyncio
async def test_async_returns_default_on_oserror():
    @safe_file_op(default="fallback")
    async def reader(path):
        raise OSError("disk error")

    assert await reader("/x") == "fallback"


@pytest.mark.asyncio
async def test_async_passes_through_non_oserror():
    @safe_file_op(default="fallback")
    async def reader(path):
        raise TypeError("not OSError")

    with pytest.raises(TypeError):
        await reader("/x")


@pytest.mark.asyncio
async def test_async_returns_real_value():
    @safe_file_op(default="fallback")
    async def reader(path):
        return "async_real"

    assert await reader("/x") == "async_real"
