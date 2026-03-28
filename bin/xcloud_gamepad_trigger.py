#!/usr/bin/env python3
"""Inject synthetic gamepad events to trigger Chromium's Gamepad API detection.

Chromium on Linux only populates navigator.getGamepads() after a real button
event arrives on the input device.  When xCloud launches through a Steam
shortcut, the virtual Xbox 360 pad exists at /dev/input/event* but no user
interaction has occurred yet, so the browser never fires 'gamepadconnected'.

This helper finds the SteamOS-managed virtual Xbox pad and injects brief
synthetic A-button press/release events until the browser has had time to
detect the gamepad.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import time

log = logging.getLogger("gamepad-trigger")


def find_xbox_pad() -> str | None:
    """Find the virtual Xbox 360 pad event device by name."""
    for event_path in sorted(glob.glob("/dev/input/event*")):
        name_path = f"/sys/class/input/{os.path.basename(event_path)}/device/name"
        try:
            with open(name_path) as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if "X-Box 360" in name or "Xbox 360" in name:
            if os.access(event_path, os.W_OK):
                log.info("Found virtual Xbox pad: %s (%s)", event_path, name)
                return event_path
            else:
                log.warning("Found %s but no write access", event_path)
    return None


def inject_button_press(device_path: str) -> bool:
    """Inject a brief A-button press+release into the event device."""
    try:
        import evdev
        from evdev import ecodes

        dev = evdev.InputDevice(device_path)
        dev.write(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1)
        dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        time.sleep(0.04)
        dev.write(ecodes.EV_KEY, ecodes.BTN_SOUTH, 0)
        dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        dev.close()
        return True
    except ImportError:
        return _inject_button_press_raw(device_path)
    except Exception as exc:
        log.error("evdev injection failed: %s", exc)
        return _inject_button_press_raw(device_path)


def _inject_button_press_raw(device_path: str) -> bool:
    """Fallback: inject events via raw struct writes (no evdev dependency)."""
    import struct

    EV_SYN = 0x00
    EV_KEY = 0x01
    SYN_REPORT = 0x00
    BTN_SOUTH = 0x130

    try:
        fd = os.open(device_path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        log.error("Cannot open %s for writing: %s", device_path, exc)
        return False

    try:
        def _write_event(etype: int, code: int, value: int) -> None:
            now = time.time()
            sec = int(now)
            usec = int((now - sec) * 1_000_000)
            os.write(fd, struct.pack("llHHi", sec, usec, etype, code, value))

        _write_event(EV_KEY, BTN_SOUTH, 1)
        _write_event(EV_SYN, SYN_REPORT, 0)
        time.sleep(0.04)
        _write_event(EV_KEY, BTN_SOUTH, 0)
        _write_event(EV_SYN, SYN_REPORT, 0)
        return True
    except OSError as exc:
        log.error("Raw injection failed: %s", exc)
        return False
    finally:
        os.close(fd)


def run(
    *,
    initial_delay: float,
    duration: float,
    interval: float,
) -> int:
    """Main loop: find pad, wait, inject events periodically."""
    log.info(
        "Gamepad trigger: delay=%.1fs, duration=%.1fs, interval=%.1fs",
        initial_delay,
        duration,
        interval,
    )

    device_path = find_xbox_pad()
    if not device_path:
        log.warning("No writable virtual Xbox pad found — retrying for up to 15s")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            time.sleep(1.0)
            device_path = find_xbox_pad()
            if device_path:
                break
        if not device_path:
            log.error("No virtual Xbox pad found — cannot trigger gamepad detection")
            return 1

    if initial_delay > 0:
        log.info("Waiting %.1fs for browser to start scanning devices…", initial_delay)
        time.sleep(initial_delay)

    end_time = time.monotonic() + duration
    injection_count = 0

    while time.monotonic() < end_time:
        if inject_button_press(device_path):
            injection_count += 1
            log.info("Injected synthetic button press #%d on %s", injection_count, device_path)
        else:
            log.warning("Injection #%d failed on %s", injection_count + 1, device_path)

        remaining = end_time - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))

    log.info("Gamepad trigger complete: %d injections over %.1fs", injection_count, duration)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds to wait before first injection (default: 5)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=20.0,
        help="Total seconds to keep injecting (default: 20)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Seconds between injections (default: 3)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s][%(levelname)s] %(name)s: %(message)s",
    )

    return run(
        initial_delay=args.delay,
        duration=args.duration,
        interval=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
