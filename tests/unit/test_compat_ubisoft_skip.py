"""Ubisoft launches skip the generic redistributables compat step.

Ubisoft games run through UPC, which installs its own redistributables,
so ``apply_prefix_compat`` must NOT run winetricks/vcredist for them
(that only re-installs what UPC provides + adds a first-launch delay).
Other stores run the game exe directly and still need it.
"""
from __future__ import annotations

import types

import pytest

from unifideck.launcher.proton import compat


def _plan(store: str, prefix_path):
    return types.SimpleNamespace(
        context=types.SimpleNamespace(store=store),
        prefix_path=prefix_path,
    )


@pytest.mark.asyncio
async def test_ubisoft_skips_generic_compat(monkeypatch, tmp_path):
    called: list[str] = []

    async def _wt(_plan):
        called.append("winetricks")

    async def _vc(_plan):
        called.append("vcruntime")

    monkeypatch.setattr(compat, "apply_winetricks", _wt)
    monkeypatch.setattr(compat, "apply_vcruntime_fix", _vc)

    await compat.apply_prefix_compat(_plan("ubisoft", tmp_path))
    assert called == []  # neither step ran


@pytest.mark.asyncio
async def test_other_store_runs_generic_compat(monkeypatch, tmp_path):
    (tmp_path / "system.reg").write_text("x")  # initialised prefix
    called: list[str] = []

    async def _wt(_plan):
        called.append("winetricks")

    async def _vc(_plan):
        called.append("vcruntime")

    monkeypatch.setattr(compat, "apply_winetricks", _wt)
    monkeypatch.setattr(compat, "apply_vcruntime_fix", _vc)

    await compat.apply_prefix_compat(_plan("epic", tmp_path))
    assert called == ["winetricks", "vcruntime"]
