"""specialists.py — Compose all Ubisoft sub-systems into one bag.

# OP-55j | py_modules/unifideck/stores/ubisoft/specialists.py | Depends: (none)

The store layer ends up depending on a *lot* of helpers (config,
paths, binaries, id_map, session, library, installer, prefix manager,
auth). Threading them all through the constructor would be ugly, so
we collect them into ``UbisoftSpecialists`` once and pass that in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from unifideck.stores.ubisoft.auth import (
    UbisoftAuth,
    UbisoftAuthServices,
    UbisoftAuthState,
)
from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver
from unifideck.stores.ubisoft.config import UbisoftConfig
from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.installer import UbisoftInstaller
from unifideck.stores.ubisoft.installer.cache import UbisoftInstallerCache
from unifideck.stores.ubisoft.library import UbisoftLibrary
from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths
from unifideck.stores.ubisoft.prefix import UbisoftPrefixManager
from unifideck.stores.ubisoft.session import UbisoftSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UbisoftSpecialists:
    """Ubisoft specialists."""

    config: UbisoftConfig
    paths: UbisoftPrefixPaths
    binaries: UbisoftBinaryResolver
    id_map: UbisoftIdMap
    session: UbisoftSession
    installer_cache: UbisoftInstallerCache
    prefix_mgr: UbisoftPrefixManager
    library: UbisoftLibrary
    installer: UbisoftInstaller
    auth: UbisoftAuth


@dataclass(frozen=True)
class _UbisoftFoundations:
    """Ubisoft foundations."""

    ubi_config: UbisoftConfig
    paths: UbisoftPrefixPaths
    binaries: Any
    id_map: UbisoftIdMap


@dataclass(frozen=True)
class _UbisoftRuntimeChain:
    """Ubisoft runtime chain."""

    session: UbisoftSession
    installer_cache: UbisoftInstallerCache
    prefix_mgr: UbisoftPrefixManager


def _build_ubisoft_foundations(
    config_mgr: Any, plugin_dir: str | None,
) -> _UbisoftFoundations:
    """Build UBISOFT foundations."""
    ubi_config = UbisoftConfig.from_config_manager(config_mgr)
    paths = UbisoftPrefixPaths(ubi_config)
    binaries = UbisoftBinaryResolver(ubi_config, plugin_dir)
    id_map = UbisoftIdMap(ubi_config, paths)
    return _UbisoftFoundations(
        ubi_config=ubi_config, paths=paths, binaries=binaries, id_map=id_map,
    )


def _build_ubisoft_runtime_chain(
    f: _UbisoftFoundations,
) -> _UbisoftRuntimeChain:
    """Build UBISOFT runtime chain."""
    session = UbisoftSession(
        config=f.ubi_config, paths=f.paths,
        read_machine_guid=UbisoftPrefixManager.read_machine_guid,
    )
    installer_cache = UbisoftInstallerCache(f.ubi_config)
    prefix_mgr = UbisoftPrefixManager(
        config=f.ubi_config, paths=f.paths, binaries=f.binaries,
        installer_cache=installer_cache,
        inject_auth_state=session.ensure_auth_state_in_prefixes,
    )
    return _UbisoftRuntimeChain(
        session=session, installer_cache=installer_cache,
        prefix_mgr=prefix_mgr,
    )


def _build_ubisoft_auth(
    *, bus: Any, f: _UbisoftFoundations, r: _UbisoftRuntimeChain,
    plugin_dir: str | None, shortcut_service: Any | None,
    steamgriddb: Any | None,
) -> UbisoftAuth:
    """Build UBISOFT auth."""
    state = UbisoftAuthState(
        config=f.ubi_config, paths=f.paths, binaries=f.binaries,
        session=r.session,
        ensure_auth_prefix=r.prefix_mgr.ensure_auth_prefix,
        queue_auth_assets_ensure=r.prefix_mgr.queue_auth_assets_ensure,
    )
    services = UbisoftAuthServices(
        plugin_dir=plugin_dir,
        shortcut_service=shortcut_service,
        steamgriddb=steamgriddb,
    )
    return UbisoftAuth(bus=bus, state=state, services=services)


def build_ubisoft_specialists(
    *, bus: Any, config_mgr: Any, plugin_dir: str | None,
    shortcut_service: Any | None, steamgriddb: Any | None,
) -> UbisoftSpecialists:
    """Build UBISOFT specialists."""
    foundations = _build_ubisoft_foundations(config_mgr, plugin_dir)
    runtime = _build_ubisoft_runtime_chain(foundations)
    library = UbisoftLibrary(
        foundations.ubi_config, foundations.paths, foundations.id_map,
        queue_template_creation=runtime.prefix_mgr.queue_template_creation,
    )
    installer = UbisoftInstaller(
        config=foundations.ubi_config, paths=foundations.paths,
        binaries=foundations.binaries, id_map=foundations.id_map,
        session=runtime.session, library=library,
        bootstrap_game_prefix=runtime.prefix_mgr.bootstrap_game_prefix,
    )
    auth = _build_ubisoft_auth(
        bus=bus, f=foundations, r=runtime,
        plugin_dir=plugin_dir, shortcut_service=shortcut_service,
        steamgriddb=steamgriddb,
    )
    return UbisoftSpecialists(
        config=foundations.ubi_config,
        paths=foundations.paths,
        binaries=foundations.binaries,
        id_map=foundations.id_map,
        session=runtime.session,
        installer_cache=runtime.installer_cache,
        prefix_mgr=runtime.prefix_mgr,
        library=library,
        installer=installer,
        auth=auth,
    )
