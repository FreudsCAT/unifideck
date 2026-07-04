"""services/cloud_save/gog_state_mixin.py — GOG cloud-save on-disk state.

Split out of ``gog_strategy.py`` to keep that module focused on the resolve →
sync pipeline. ``GOGStateMixin`` owns the persistent sidecar state (resolved
save dirs in ``cloud_sync_state.json`` + per-location sync watermarks) and the
GOG OAuth-token decryption that produces gogdl credentials. Its only host
dependency is ``local_save_root`` (provided by ``CloudSaveStrategy``).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from unifideck.security.secure_token_store import SecureTokenStore
from unifideck.services.cloud_save.gog_cloud_api import GOG_DEFAULT_NAMESPACE

logger = logging.getLogger(__name__)


class GOGStateMixin:
    """Credential decryption + on-disk save-dir/watermark state for
    :class:`GOGCloudSaveStrategy`."""

    # Provided by ``CloudSaveStrategy`` / ``GOGCloudSaveStrategy.__init__``.
    local_save_root: str

    def _read_gog_credentials(self) -> dict[str, Any] | None:
        """Return ``{access_token, refresh_token, user_id}`` from the auth file."""
        auth_path = self._convert_gog_token()
        if not auth_path:
            return None
        try:
            with open(auth_path) as f:
                data: dict[str, Any] = json.load(f)
        except Exception:
            return None
        account = data.get("46899977096215655")
        return account if isinstance(account, dict) else None

    def _read_cached_record(self, game_id: str) -> dict[str, Any] | None:
        """Normalised cached save record ``{path, name, targets}`` or None.

        Tolerates older on-disk shapes: a bare path string (pre-namespace fix)
        and ``{path, name}`` without ``targets`` (pre-multi-location fix)."""
        state_file = self._get_state_file()
        if not state_file.exists():
            return None
        try:
            with open(state_file) as f:
                data: dict[str, Any] = json.load(f)
        except Exception:
            return None
        cached = data.get("gog_save_dirs", {}).get(game_id)
        if isinstance(cached, dict):
            path = cached.get("path")
            if not path:
                return None
            name = cached.get("name")
            targets = [
                (str(d), str(n))
                for d, n in (cached.get("targets") or [])
                if d and n
            ]
            return {
                "path": str(path),
                "name": str(name) if name else None,
                "targets": targets,
            }
        # Legacy: a bare path string with no namespace.
        return {"path": str(cached), "name": None, "targets": []} if cached else None

    def _read_cached_save_dir(self, game_id: str) -> tuple[str, str | None] | None:
        """Cached ``(save_dir, cloud_namespace)``; namespace is None for a
        legacy path-only entry (written before the gogdl ``--name`` fix)."""
        rec = self._read_cached_record(game_id)
        return (rec["path"], rec["name"]) if rec else None

    def _write_cached_save_dir(
        self, game_id: str, path: str, name: str,
        targets: list[tuple[str, str]] | None = None,
    ) -> None:
        state_file = self._get_state_file()
        state: dict[str, Any] = {}
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
            except Exception:
                state = {}
        state.setdefault("gog_save_dirs", {})[game_id] = {
            "path": path, "name": name,
            "targets": [list(t) for t in (targets or [])],
        }
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception:
            logger.exception("[GOGSync] Failed to cache resolved save dir")

    def _convert_gog_token(self) -> str | None:
        """Decrypt GOG OAuth token and write gogdl credentials config. Returns path on success."""
        token_path = Path("~/.config/unifideck/gog_token.json").expanduser()
        if not token_path.exists():
            logger.error("[GOGSync] GOG OAuth token file not found at %s", token_path)
            return None

        try:
            store = SecureTokenStore()
            with open(token_path, "rb") as f:
                blob = f.read()
            token = store.decrypt_payload(blob)

            gogdl_auth = {
                "46899977096215655": {
                    "access_token": token.get("access_token"),
                    "expires_in": 3600,
                    "token_type": "bearer",
                    "scope": "",
                    "refresh_token": token.get("refresh_token"),
                    "user_id": token.get("user_id", ""),
                    "session_id": "",
                    "loginTime": time.time()
                }
            }

            auth_file = Path("~/.config/unifideck/gogdl_auth.json").expanduser()
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            with open(auth_file, "w") as f:
                json.dump(gogdl_auth, f)
            return str(auth_file)
        except Exception:
            logger.exception("[GOGSync] Failed to convert/decrypt GOG OAuth token")
            return None

    def _get_state_file(self) -> Path:
        return Path(self.local_save_root).parent / "cloud_sync_state.json"

    @staticmethod
    def _ts_key(game_id: str, namespace: str) -> str:
        """Per-(game, namespace) watermark key. The default namespace keeps
        the bare ``game_id`` key (backward-compatible with existing state);
        extra namespaces (a multi-location game's ``saves2`` …) get their own
        so a stale watermark from one location can't make gogdl skip another.
        """
        if namespace in (GOG_DEFAULT_NAMESPACE, ""):
            return game_id
        return f"{game_id}::{namespace}"

    def _get_saved_timestamp(
        self, game_id: str, namespace: str = GOG_DEFAULT_NAMESPACE,
    ) -> str:
        state_file = self._get_state_file()
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                ts = state.get("gog", {}).get(self._ts_key(game_id, namespace))
                if ts is not None:
                    return str(ts)
            except Exception:
                logger.exception("[GOGSync] Failed to read cloud_sync_state")
        return "0"

    def _save_timestamp(
        self, game_id: str, timestamp: str,
        namespace: str = GOG_DEFAULT_NAMESPACE,
    ) -> None:
        state_file = self._get_state_file()
        state: dict[str, Any] = {}
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
            except Exception as e:
                logger.debug("[GOGSync] state read failed (will recreate): %s", e)

        state.setdefault("gog", {})[self._ts_key(game_id, namespace)] = timestamp
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception:
            logger.exception("[GOGSync] Failed to write cloud_sync_state")
