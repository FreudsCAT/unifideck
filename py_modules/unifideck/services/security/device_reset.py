"""services.security.device_reset — Machine-ID mismatch handler (Policy 3).

Two free functions implementing Policy 3 of SecurityService:

  - ``check_device_fingerprint(service)`` : verify the stored
    fingerprint against the current device at startup. Called
    once by ``SecurityService.start()``.
  - ``handle_device_reset(service, state)`` : on mismatch, wipe
    the configured token files proactively and reinitialise
    the fingerprint. Called by ``check_device_fingerprint``
    when a mismatch is detected.

Both functions take ``service`` (a ``SecurityService`` instance)
as their first argument and access its attributes directly. They
are extracted from the class as free functions because:

  - they have clearly-scoped side effects (file I/O + event
    emission);
  - they are only called from one place (the lifecycle hook);
  - extracting them trims ~75 LOC off the SecurityService class
    body and lets a new dev read the device-reset policy without
    wading through unrelated handlers.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.security import DeviceIdentityError, FingerprintState

from .bus_emitter import emit_security_event
from .config_readers import read_list

if TYPE_CHECKING:
    from .service import SecurityService

logger = logging.getLogger(__name__)


async def check_device_fingerprint(
    service: SecurityService,
) -> FingerprintState:
    """Verify the stored fingerprint against the current device.

    Called once at startup by ``start_async_services`` via the
    uniform lifecycle hook ``SecurityService.start()``. Never
    raises: ``DeviceIdentityError`` is caught and surfaced via
    the returned state (empty hash means verification skipped).
    """
    try:
        state = service._fingerprint.verify_or_initialize()
    except DeviceIdentityError:
        logger.exception("[SecurityService] fingerprint check failed")
        return FingerprintState(
            machine_id_hash="",
            first_seen=0.0,
            last_verified=0.0,
            is_new=False,
            mismatch=False,
        )
    if state.is_new:
        emit_security_event(
            service._bus, "SECURITY_FINGERPRINT_INITIALIZED",
        )
        service._audit.record(
            "SECURITY_FINGERPRINT_INITIALIZED", {},
        )
    elif state.mismatch:
        await handle_device_reset(service, state)
    return state


async def handle_device_reset(
    service: SecurityService, state: FingerprintState,
) -> None:
    """Wipe configured token files on machine-id mismatch.

    The fingerprint no longer matches the machine's ID — this
    usually means a reinstall or a restored backup. We
    proactively wipe any encrypted token files listed in
    ``security.token_files_to_wipe_on_reset`` so a stale
    encryption key doesn't leave decryption failures piling up
    (which would trip the brute-force detector).
    """
    logger.error(
        "[SecurityService] DEVICE RESET DETECTED — "
        "machine-id no longer matches stored fingerprint",
    )
    token_files = read_list(
        service._config, "security.token_files_to_wipe_on_reset",
    )
    wiped: list[str] = []
    for rel_path in token_files:
        # Bind ``rel_path`` as a default arg so the lambda captures
        # the current iteration value (ruff B023). The lambda runs
        # in a worker via ``to_thread`` so this is also our
        # ASYNC240 escape hatch for the blocking ``expanduser`` and
        # ``is_file`` calls below.
        full_path = await asyncio.to_thread(
            # Lambda param ``rp`` capture-by-default — mypy can't
            # infer its return type without an annotation; the
            # silence below acknowledges this is by design.
            lambda rp=rel_path: Path(rp).expanduser(),  # type: ignore[misc]
        )
        full = str(full_path)
        if not await asyncio.to_thread(full_path.is_file):
            continue
        try:
            await asyncio.to_thread(full_path.unlink)
            wiped.append(full)
            logger.warning(
                "[SecurityService] wiped stale token: %s", full,
            )
        except OSError as e:
            logger.warning(
                "[SecurityService] failed to wipe %s: %s",
                full, e,
            )
    emit_security_event(
        service._bus, "SECURITY_DEVICE_RESET_DETECTED",
        wiped_files=wiped,
        wiped_count=len(wiped),
    )
    service._audit.record(
        "SECURITY_DEVICE_RESET_DETECTED",
        {"wiped_count": len(wiped)},
    )
    try:
        service._fingerprint.reinitialize()
    except DeviceIdentityError:
        logger.exception("[SecurityService] reinit failed")
