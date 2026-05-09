import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ...core.types import Events, Result, StoreError
if TYPE_CHECKING:
    from ...core.cache_manager import CacheManager
    from ...event_bus import EventBus
    from .store_base import StoreBase
logger = logging.getLogger(__name__)
class StoreRegistry:
    """Store registry."""
    def __init__(self, bus: "EventBus") -> None:
        """Initialize the instance."""
        self._stores: dict[str, StoreBase] = {}
        self._bus = bus
    def register(
        self, store_id: str, store: "StoreBase",
    ) -> None:
        """Register."""
        self._stores[store_id] = store
        logger.info("[StoreRegistry] Registered: %s", store_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "[StoreRegistry] no running event loop; "
                "STORE_REGISTERED suppressed for %s",
                store_id,
            )
            return
        payload = {
            "store_id": store_id,
            "store_info": asdict(store.store_info),
        }
        loop.create_task(
            self._bus.emit(Events.STORE_REGISTERED, **payload),
            name=f"emit_store_registered_{store_id}",
        )

    def auto_discover(
        self,
        stores_dir: str,
        bus: "EventBus",
        cache: "CacheManager",
        plugin_dir: str = "",
        config=None,
    ) -> int:

        """Auto discover."""
        import importlib
        from .store_base import StoreBase as _StoreBase
        real_stores = self._validate_stores_dir(
            stores_dir, plugin_dir,
        )
        if real_stores is None:
            return 0
        package_name = "unifideck.stores"
        try:
            importlib.import_module(package_name)
        except ImportError as e:
            logger.warning(
                "[StoreRegistry] Cannot resolve stores "
                "package: %s", e,
            )
            return 0
        registered = 0
        for filename, full_path in self._iter_store_files(
            real_stores,
        ):
            store_cls = self._load_store_class(
                package_name, filename, full_path,
                _StoreBase,
            )
            if store_cls is None:
                continue
            try:
                store = store_cls(
                    bus, cache, plugin_dir, config=config,
                )
                store_id = store.store_info.name
                self.register(store_id, store)
                logger.info(
                    "[StoreRegistry] registered %s (%s) "
                    "from %s",
                    store_id, store_cls.__name__, filename,
                )
                registered += 1
            except Exception as e:
                logger.error(
                    "[StoreRegistry] Failed to instantiate "
                    "%s from %s: %s",
                    store_cls.__name__, filename, e,
                )
        logger.info(
            "[StoreRegistry] Auto-discovery: %d stores "
            "from %s",
            registered, real_stores,
        )
        return registered

    @staticmethod
    def _validate_stores_dir(
        stores_dir: str, plugin_dir: str,
    ) -> str | None:
        """Validate stores dir."""
        try:
            real_stores = str(Path(stores_dir).resolve())
        except OSError as e:
            logger.error(
                "[StoreRegistry] Cannot resolve stores dir "
                "%r: %s",
                stores_dir, e,
            )
            return None
        if not Path(real_stores).is_dir():
            logger.warning(
                "[StoreRegistry] stores dir not found: %s",
                real_stores,
            )
            return None
        if plugin_dir:
            real_plugin = str(Path(plugin_dir).resolve())
            confined = (
                real_stores == real_plugin
                or real_stores.startswith(real_plugin + "/")
            )
            if not confined:
                logger.error(
                    "[StoreRegistry] SECURITY: stores dir "
                    "%s is NOT under plugin dir %s — "
                    "refusing to auto-discover.",
                    real_stores, real_plugin,
                )
                return None
        else:
            logger.warning(
                "[StoreRegistry] auto_discover called "
                "without plugin_dir — path confinement "
                "disabled. This is only acceptable in unit "
                "tests; production must always pass "
                "plugin_dir.",
            )
        return real_stores
    @staticmethod
    def _iter_store_files(real_stores: str):
        """Iter store files."""
        real_stores_p = Path(real_stores)
        for entry in sorted(real_stores_p.iterdir()):
            filename = entry.name
            if not filename.endswith("_store.py"):
                continue
            if filename.startswith("_"):
                continue
            if entry.is_symlink():
                logger.warning(
                    "[StoreRegistry] SECURITY: skipping "
                    "symlink %s", str(entry),
                )
                continue
            yield filename, str(entry)

    @staticmethod
    def _load_store_class(
        package_name: str,
        filename: str,
        full_path: str,
        store_base_cls: type,
    ) -> type | None:
        """Load store class."""
        import importlib
        module_name = f"{package_name}.{filename[:-3]}"
        logger.info(
            "[StoreRegistry] loading %s from %s",
            module_name, full_path,
        )
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            logger.debug(
                "[StoreRegistry] Skip %s: %s", filename, e,
            )
            return None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, store_base_cls)
                and attr is not store_base_cls
                and hasattr(attr, "store_info")
            ):
                return attr
        return None
    def get(self, store_id: str) -> "StoreBase":
        """Get."""
        if store_id not in self._stores:
            raise KeyError(
                f"Store '{store_id}' not registered. "
                f"Available: {list(self._stores.keys())}",
            )
        return self._stores[store_id]
    def get_store(self, store_id: str) -> "StoreBase | None":
        """Get store."""
        return self._stores.get(store_id)
    def all(self) -> list["StoreBase"]:
        """All."""
        return list(self._stores.values())
    def available(self) -> list["StoreBase"]:
        """Available."""
        return [
            s for s in self._stores.values()
            if getattr(s, "_cached_available", False)
        ]
    def store_ids(self) -> list[str]:
        """Store ids."""
        return list(self._stores.keys())
    def has(self, store_id: str) -> bool:
        """Check whether s."""
        return store_id in self._stores
    def get_store_infos(self) -> list[dict]:
        """Get store infos."""
        infos = []
        for store in self._stores.values():
            info = asdict(store.store_info)
            info["available"] = getattr(
                store, "_cached_available", False,
            )
            infos.append(info)
        return infos

    async def auth_action(
        self, store_id: str, action: str, **kwargs,
    ) -> Result:

        """Auth action."""
        try:
            store = self.get(store_id)
        except KeyError as e:
            return Result(success=False, error=str(e))
        try:
            if action == "start":
                return await store.start_auth(**kwargs)
            if action == "complete":
                return await store.complete_auth(**kwargs)
            if action == "logout":
                result = await store.logout()
                if result.success:
                    await self._bus.emit(
                        Events.STORE_LOGOUT,
                        store=store_id,
                    )
                return result
            if action == "status":
                is_avail = await store.is_available()
                store._cached_available = is_avail
                return Result(success=is_avail)
            return Result(
                success=False,
                error=(
                    f"Unknown auth action: '{action}'. "
                    f"Valid: start, complete, logout, status"
                ),
            )
        except StoreError as e:
            logger.error(
                "[StoreRegistry] %s.%s failed: %s",
                store_id, action, e,
            )
            await self._bus.emit(
                Events.STORE_AUTH_FAILED,
                store=store_id, error=str(e),
            )
            return Result(success=False, error=str(e))
        except Exception as e:
            logger.exception(
                "[StoreRegistry] Unexpected error in %s.%s",
                store_id, action,
            )
            return Result(
                success=False, error=f"Unexpected: {e}",
            )
    async def check_all_status(self) -> list[dict[str, Any]]:
        """Check all status."""
        results: list[dict[str, Any]] = []
        for store in self._stores.values():
            entry: dict[str, Any] = {
                "store_id": store.store_info.name,
                "name": store.store_info.display_name,
                "available": False,
                "error": None,
            }
            try:
                entry["available"] = await store.is_available()
                store._cached_available = entry["available"]
            except Exception as e:
                entry["error"] = str(e)
                logger.warning(
                    "[StoreRegistry] %s availability check "
                    "failed: %s", store.store_info.name, e,
                )
            results.append(entry)
        return results
    async def logout_all(self) -> dict[str, Any]:
        """Logout all."""
        out: dict[str, Any] = {}
        for store_id in self._stores:
            result = await self.auth_action(store_id, "logout")
            out[store_id] = {
                "success": result.success,
                "error": result.error,
            }
        return out