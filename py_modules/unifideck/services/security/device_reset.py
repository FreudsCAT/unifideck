"""Device-reset detection — invalidate tokens after a system reset.

OP-19f | py_modules/unifideck/services/security/device_reset.py

If the Steam Deck has been factory-reset between two boots, every
stored credential is suspect (the user might no longer be the same
person). ``check_device_fingerprint`` compares the current machine
fingerprint (a hash of stable system identifiers) against the
last-known one.

``handle_device_reset`` is the recovery handler that wipes the
encrypted token store and prompts the user to re-authenticate on
every store.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from ...security import DeviceIdentityError, FingerprintState
from .bus_emitter import emit_security_event
from .config_readers import read_list

if TYPE_CHECKING:
    from .service import SecurityService
logger = logging.getLogger(__name__)


async def check_device_fingerprint(service: SecurityService) -> FingerprintState:
    """Check device fingerprint."""
    try:
        state = service._fingerprint.verify_or_initialize()
    except DeviceIdentityError as e:
        logger.error(
            "[SecurityService] fingerprint check failed: %s",
            e,
        )
        return FingerprintState(
            machine_id_hash="",
            first_seen=0.0,
            last_verified=0.0,
            is_new=False,
            mismatch=False,
        )
    if state.is_new:
        emit_security_event(
            service._bus,
            "SECURITY_FINGERPRINT_INITIALIZED",
        )
        service._audit.record(
            "SECURITY_FINGERPRINT_INITIALIZED",
            {},
        )
    elif state.mismatch:
        await handle_device_reset(service, state)
    return state


async def handle_device_reset(
    service: SecurityService,
    state: FingerprintState,
) -> None:
    """Handle device reset."""
    logger.error(
        "[SecurityService] DEVICE RESET DETECTED — "
        "machine-id no longer matches stored fingerprint",
    )
    token_files = read_list(
        service._config,
        "security.token_files_to_wipe_on_reset",
    )
    wiped: list[str] = []
    for rel_path in token_files:
        full_path = Path(rel_path).expanduser()
        full = str(full_path)
        if not full_path.is_file():
            continue
        try:
            full_path.unlink()
            wiped.append(full)
            logger.warning(
                "[SecurityService] wiped stale token: %s",
                full,
            )
        except OSError as e:
            logger.warning(
                "[SecurityService] failed to wipe %s: %s",
                full,
                e,
            )
    emit_security_event(
        service._bus,
        "SECURITY_DEVICE_RESET_DETECTED",
        wiped_files=wiped,
        wiped_count=len(wiped),
    )
    service._audit.record(
        "SECURITY_DEVICE_RESET_DETECTED",
        {"wiped_count": len(wiped)},
    )
    try:
        service._fingerprint.reinitialize()
    except DeviceIdentityError as e:
        logger.error(
            "[SecurityService] reinit failed: %s",
            e,
        )
