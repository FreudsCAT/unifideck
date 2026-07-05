"""Unit tests for UpdaterService's cache-bypass (``force``) plumbing.

Regression coverage for the self-updater install-does-nothing bug: a
mutable prerelease tag's single GitHub asset gets deleted and re-uploaded
under a new name on every dev build, so the 1-hour in-process cache must
be bypassable on demand (the explicit "Check for Updates" action) rather
than only expiring passively. See ``py_modules/unifideck/rpc/mixins/
updater.py``'s ``force_check_plugin_update``/``force_get_available_versions``
for the RPC-layer half of this fix.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from unifideck.services.updater.service import ReleaseInfo, UpdaterService

_RELEASE_A = ReleaseInfo(
    tag="Dev",
    version="Dev",
    name="Dev Release for Testing",
    asset_url="https://github.com/x/y/releases/download/Dev/unifideck.dev.v524.zip",
    asset_name="unifideck.dev.v524.zip",
    sha256="",
    size_bytes=123,
    prerelease=True,
    published_at="2026-01-09T16:27:08Z",
    body="TBD",
    download_count=0,
)

_RELEASE_B = ReleaseInfo(
    tag="Dev",
    version="Dev",
    name="Dev Release for Testing",
    asset_url="https://github.com/x/y/releases/download/Dev/unifideck.dev.v527.zip",
    asset_name="unifideck.dev.v527.zip",
    sha256="",
    size_bytes=456,
    prerelease=True,
    published_at="2026-01-09T16:27:08Z",
    body="TBD",
    download_count=0,
)


def _service(tmp_path: Path) -> UpdaterService:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"version": "0.7.0"}')
    return UpdaterService(bus=None, package_json_path=str(package_json))


async def test_fetch_releases_uses_warm_cache_without_force(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(
        UpdaterService, "_fetch_from_github", new_callable=AsyncMock,
    ) as mock_fetch:
        mock_fetch.side_effect = [[_RELEASE_A], [_RELEASE_B]]

        first = await svc.fetch_releases()
        second = await svc.fetch_releases()

        assert mock_fetch.await_count == 1
        assert first == second == [_RELEASE_A]


async def test_fetch_releases_force_bypasses_warm_cache(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(
        UpdaterService, "_fetch_from_github", new_callable=AsyncMock,
    ) as mock_fetch:
        mock_fetch.side_effect = [[_RELEASE_A], [_RELEASE_B]]

        first = await svc.fetch_releases()
        second = await svc.fetch_releases(force=True)

        assert mock_fetch.await_count == 2
        assert first == [_RELEASE_A]
        assert second == [_RELEASE_B]
        assert second[0].asset_name == "unifideck.dev.v527.zip"


async def test_check_for_update_forwards_force(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(
        UpdaterService, "_fetch_from_github", new_callable=AsyncMock,
    ) as mock_fetch:
        mock_fetch.side_effect = [[_RELEASE_A], [_RELEASE_B]]

        await svc.check_for_update()
        await svc.check_for_update(force=True)

        assert mock_fetch.await_count == 2
