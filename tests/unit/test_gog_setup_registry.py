"""GOG ``setRegistry`` dual-WOW64-view write — gog_setup/scripts.py.

Regression: 32-bit GOG titles (Fallout: New Vegas, `gog:1454587428`) launched
into a launcher that said "Install". Cause: the ``goggame-*.script``
``setRegistry`` "Installed Path" key was written only to the native
``HKLM\\Software\\…`` view, but the game is PE32/i386 in a win64 prefix, so it
reads the ``Wow6432Node`` redirect — which was empty → "not installed".

``_apply_set_registry`` must now emit the key to BOTH views for
``HKLM\\Software`` keys, mirroring the Epic/Ubisoft registry fixes.
"""
from __future__ import annotations

from unifideck.launcher.proton.compat.gog_setup import scripts

_FNV_SUBKEY = "Software\\Bethesda Softworks\\FalloutNV"
_FNV_REDIRECT = "Software\\WOW6432Node\\Bethesda Softworks\\FalloutNV"


# ── _wow64_subkeys (pure) ──────────────────────────────────────────────


def test_wow64_subkeys_hklm_software_adds_redirect() -> None:
    assert scripts._wow64_subkeys("HKLM", _FNV_SUBKEY) == [_FNV_SUBKEY, _FNV_REDIRECT]


def test_wow64_subkeys_case_insensitive_software_prefix() -> None:
    out = scripts._wow64_subkeys("HKLM", "SOFTWARE\\Foo")
    assert out[0] == "SOFTWARE\\Foo"  # original preserved verbatim
    assert "Software\\WOW6432Node\\Foo" in out


def test_wow64_subkeys_skips_non_software() -> None:
    assert scripts._wow64_subkeys("HKLM", "System\\Foo") == ["System\\Foo"]


def test_wow64_subkeys_skips_hkcu() -> None:
    # Only HKLM\\Software is WOW64-redirected for these install-marker keys.
    assert scripts._wow64_subkeys("HKCU", "Software\\Foo") == ["Software\\Foo"]


def test_wow64_subkeys_already_redirected_not_doubled() -> None:
    assert scripts._wow64_subkeys("HKLM", _FNV_REDIRECT) == [_FNV_REDIRECT]


# ── _apply_set_registry (integration, run_wine mocked) ─────────────────


def _recorder(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    async def fake_run_wine(_plan, exe, args):
        calls.append((exe, list(args)))
        return True

    monkeypatch.setattr(scripts, "run_wine", fake_run_wine)
    return calls


async def test_apply_set_registry_writes_both_views(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    args = {
        "root": "HKEY_LOCAL_MACHINE",
        "subkey": _FNV_SUBKEY,
        "valueName": "Installed Path",
        "valueType": "string",
        "valueData": "{app}\\",
    }
    await scripts._apply_set_registry(None, args, "/games/Fallout New Vegas")

    assert len(calls) == 2
    keys = {a[1] for _exe, a in calls}  # the "<key>" positional after "add"
    assert f"HKLM\\{_FNV_SUBKEY}" in keys
    assert f"HKLM\\{_FNV_REDIRECT}" in keys
    for exe, a in calls:
        assert exe == "reg.exe"
        assert a[0] == "add" and "/f" in a
        assert a[a.index("/v") + 1] == "Installed Path"
        assert a[a.index("/t") + 1] == "REG_SZ"
        # {app} expanded to the Wine Z: path of the install dir.
        assert a[a.index("/d") + 1] == "Z:\\games\\Fallout New Vegas\\"


async def test_apply_set_registry_single_write_for_non_software(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    args = {
        "root": "HKLM",
        "subkey": "System\\CurrentControlSet\\Foo",
        "valueName": "X",
        "valueType": "dword",
        "valueData": "1",
    }
    await scripts._apply_set_registry(None, args, "/x")
    assert len(calls) == 1
    assert calls[0][1][1] == "HKLM\\System\\CurrentControlSet\\Foo"


async def test_apply_set_registry_noop_without_root_or_subkey(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    await scripts._apply_set_registry(None, {"subkey": "Software\\X"}, "/x")
    await scripts._apply_set_registry(None, {"root": "HKLM"}, "/x")
    assert calls == []
