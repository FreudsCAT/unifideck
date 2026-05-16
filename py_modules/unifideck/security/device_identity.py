"""security/device_identity.py — Stable hardware identifier reader.

Reads /etc/machine-id, the systemd-managed unique identifier
generated once at SteamOS install time. This file is:
- Stable across reboots and SteamOS updates
- Different on every Deck (32 hex chars from 128 random bits)
- Reset only on a complete SteamOS reinstall (which is the
  expected behaviour: tokens become unrecoverable, forcing a
  fresh OAuth flow on the new system)
- Readable without privileges (mode 0o444 typically)

This identifier is NOT a secret — any process running on the
device can read it. It is used here purely as a *device binding*
factor: combined with a static salt, it produces a key that is
unique to a specific Deck and cannot be reused on a different
device. It does NOT protect against a local attacker who has
read access to the user's home directory.

The read is wrapped in a class to make injection trivial in
tests — production code calls DeviceIdentity().read(), tests call
FakeDeviceIdentity("aabbccdd...").read().
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the systemd machine-id file. Same on every modern
# Linux distribution including SteamOS. The fallback location
# /var/lib/dbus/machine-id is a symlink to /etc/machine-id on
# all systems we target, so we don't bother reading it.
_MACHINE_ID_PATH = "/etc/machine-id"


class DeviceIdentityError(RuntimeError):
    """Raised when the machine-id cannot be read or is malformed.

    We treat this as fatal for the secure storage layer: there
    is no safe fallback that doesn't compromise the entire
    threat model. Better to crash loudly than to silently
    use a constant value (which would make the encryption
    pointless device-wide).
    """


class DeviceIdentity:
    """Reads and caches the device's machine-id.

    The value is read once on first access and cached for the
    lifetime of the instance. /etc/machine-id never changes at
    runtime (only on a reboot after a deliberate reset), so
    re-reading it on every encryption operation would be wasted
    syscalls.
    """

    def __init__(self, path: str = _MACHINE_ID_PATH) -> None:
        """Create a reader for the given machine-id path.

        The path argument exists purely for tests — production
        always uses _MACHINE_ID_PATH. Tests can point at a
        fixture file with a known value.
        """
        self._path = path
        self._cached: str | None = None

    def read(self) -> str:
        """Return the machine-id as a 32-char lowercase hex string.

        Raises DeviceIdentityError if the file is missing, unreadable,
        or contains a value that doesn't look like a machine-id.
        Result is cached on first call.
        """
        if self._cached is not None:
            return self._cached

        try:
            with Path(self._path).open(encoding="utf-8") as f:
                raw = f.read().strip()
        except OSError as e:
            raise DeviceIdentityError(
                f"cannot read {self._path}: {e}",
            ) from e

        if not self._looks_valid(raw):
            raise DeviceIdentityError(
                f"machine-id at {self._path} is malformed: "
                f"expected 32 hex chars, got {len(raw)} chars",
            )

        self._cached = raw.lower()
        logger.debug("[DeviceIdentity] read 32-char id from %s", self._path)
        return self._cached

    @staticmethod
    def _looks_valid(value: str) -> bool:
        """Return True if value matches the machine-id format.

        systemd machine-ids are always exactly 32 lowercase hex
        characters (128 bits encoded). We reject anything else
        loudly rather than trying to be clever.
        """
        if len(value) != 32:
            return False
        try:
            int(value, 16)
        except ValueError:
            return False
        return True


class FakeDeviceIdentity:
    """Test double that returns a fixed value.

    Useful for unit tests that need a deterministic encryption
    key without touching the real /etc/machine-id. Implements
    the same .read() interface as DeviceIdentity.
    """

    def __init__(self, value: str) -> None:
        """Create a fake reader returning `value` on every call."""
        if not DeviceIdentity._looks_valid(value):
            raise ValueError(
                f"FakeDeviceIdentity requires a valid 32-char hex string, "
                f"got {len(value)} chars",
            )
        self._value = value.lower()

    def read(self) -> str:
        """Return the fixed value passed to the constructor."""
        return self._value
