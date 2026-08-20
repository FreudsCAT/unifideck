"""Backend-side Wine/umu environment for wrapper stores.

py_modules/unifideck/stores/shared/wine_env.py

Wrapper stores run a vendor installer *from the backend* to bootstrap a
prefix, before any Steam shortcut exists. That needs umu, a Proton, and —
critically — a display environment, because the plugin runs headless under
``plugin_loader`` and a Wine process with no ``DISPLAY``/``XDG_RUNTIME_DIR``
hangs rather than failing.

Delegates to Ubisoft's resolver, which already solves all of this, rather
than reimplementing it: it self-heals a half-downloaded umu runtime, walks
both Proton locations, and borrows the display environment from the live
Steam process when the plugin's own is empty. This module exists so
Battle.net (and EA App next) consume that logic through a store-neutral
surface instead of importing another store's internals.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class WineEnvResolver:
    """umu / Proton / display resolution for one wrapper store."""

    def __init__(self, store: str, plugin_dir: str | None) -> None:
        self._store = store
        self._plugin_dir = plugin_dir
        self._inner: Any | None = None

    def _resolver(self) -> Any:
        """Build the underlying resolver lazily.

        Imported inside the method so a store package never imports another
        store at module scope, which would make the dependency look
        structural rather than incidental.
        """
        if self._inner is None:
            from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver
            from unifideck.stores.ubisoft.config import UbisoftConfig

            self._inner = UbisoftBinaryResolver(UbisoftConfig(), self._plugin_dir)
        return self._inner

    def find_umu_run(self) -> str | None:
        """Path to ``umu-run``, repairing an incomplete runtime first."""
        found = self._resolver().find_umu_run()
        return str(found) if found else None

    def find_proton_path(self) -> str | None:
        """A usable Proton, official or custom."""
        found = self._resolver().find_proton_path()
        return str(found) if found else None

    def detect_display_env(self) -> dict[str, str]:
        """DISPLAY / WAYLAND_DISPLAY / XDG_RUNTIME_DIR / DBUS.

        Borrowed from the live Steam process when the plugin's own
        environment is empty — the headless Decky env lacks all of them, and
        a Wine process without them hangs instead of erroring.
        """
        return dict(self._resolver().detect_display_env())

    def build_env(
        self,
        wineprefix: Path | str,
        gameid: str,
        *,
        proton_path: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Full environment for a backend-side umu invocation."""
        env = dict(
            self._resolver().build_umu_env(
                str(wineprefix),
                gameid,
                proton_path=proton_path,
            ),
        )
        env["STORE"] = self._store
        if extra:
            env.update(extra)
        return env
