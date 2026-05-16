from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .device_identity import DeviceIdentity

logger = logging.getLogger(__name__)

_FORMAT_VERSION = 1


@dataclass(frozen=True)
class FingerprintState:
    machine_id_hash: str
    first_seen: float
    last_verified: float
    is_new: bool = False
    mismatch: bool = False


class DeviceFingerprint:
    def __init__(
        self,
        path: str,
        device_identity: DeviceIdentity | None = None,
    ) -> None:
        self._path = str(Path(path).expanduser())
        self._device_identity = device_identity or DeviceIdentity()

    def verify_or_initialize(self) -> FingerprintState:
        current_hash = self._compute_current_hash()
        stored = self._load()

        if stored is None:
            return self._initialize(current_hash)

        if stored.get("machine_id_hash") != current_hash:
            return FingerprintState(
                machine_id_hash=current_hash,
                first_seen=float(stored.get("first_seen", 0.0)),
                last_verified=float(stored.get("last_verified", 0.0)),
                is_new=False,
                mismatch=True,
            )

        now = time.time()
        self._save({
            "machine_id_hash": current_hash,
            "first_seen": float(stored.get("first_seen", now)),
            "last_verified": now,
            "version": _FORMAT_VERSION,
        })

        return FingerprintState(
            machine_id_hash=current_hash,
            first_seen=float(stored.get("first_seen", now)),
            last_verified=now,
            is_new=False,
            mismatch=False,
        )

    def reinitialize(self) -> FingerprintState:
        current_hash = self._compute_current_hash()
        return self._initialize(current_hash)

    def _compute_current_hash(self) -> str:
        mid = self._device_identity.read()
        digest = hashlib.sha256(mid.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _initialize(self, current_hash: str) -> FingerprintState:
        now = time.time()
        payload = {
            "machine_id_hash": current_hash,
            "first_seen": now,
            "last_verified": now,
            "version": _FORMAT_VERSION,
        }
        self._save(payload)
        logger.info(
            "[DeviceFingerprint] initialized at %s", self._path,
        )

        return FingerprintState(
            machine_id_hash=current_hash,
            first_seen=now,
            last_verified=now,
            is_new=True,
            mismatch=False,
        )

    def _load(self) -> dict[str, Any] | None:
        if not Path(self._path).is_file():
            return None

        try:
            with Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "[DeviceFingerprint] load failed: %s", e,
            )
            return None

        if not isinstance(data, dict):
            return None

        return data

    def _save(self, payload: dict[str, Any]) -> None:
        try:
            parent = str(Path(self._path).parent)
            if parent:
                Path(parent).mkdir(parents=True, exist_ok=True)

            tmp = self._path + ".tmp"
            fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

            Path(tmp).replace(self._path)
        except OSError as e:
            logger.warning(
                "[DeviceFingerprint] save failed: %s", e,
            )
