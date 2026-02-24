#!/usr/bin/env python3
"""
Ubisoft launch wrapper for Legendary.

Strips only -AUTH_TYPE=exchangecode to prevent the CEF SSO browser crash.
Keeps -AUTH_LOGIN and -AUTH_PASSWORD so UplayLaunch.exe can start upc.exe.
Epic identity args (-epicapp, -epicusername, etc.) are preserved.

With linked Epic+Ubisoft accounts, Ubisoft Connect authenticates server-side
without needing CEF, so login completes normally.

After umu-run returns, polls for wineserver and waits for all Wine processes
to exit so the parent launcher script blocks until the game finishes.

Prerequisite: Link accounts at https://epicgames.com/id/link/ubisoft
"""
import sys
import subprocess
import logging
import os
import time

log_file = os.path.expanduser("~/.local/share/unifideck/wrapper_stripper.log")
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s %(message)s')

def main():
    args = sys.argv[1:]
    new_args = []

    for arg in args:
        # Strip -AUTH_TYPE and -AUTH_PASSWORD (the exchange code)
        # to prevent CEF SSO crash. Keep -AUTH_LOGIN=unused so
        # UplayLaunch.exe knows it's an Epic launch and starts upc.exe.
        if arg.startswith("-AUTH_TYPE=") or arg.startswith("-AUTH_PASSWORD="):
            logging.info(f"Stripped: {arg}")
            continue
        new_args.append(arg)

    logging.info(f"Executing: {' '.join(new_args)}")

    try:
        proc = subprocess.Popen(new_args)
        proc.wait()
    except Exception as e:
        logging.error(f"Failed to execute: {e}")
        sys.exit(1)

    # umu-run returns before Wine is fully started. Wait for wineserver
    # so this wrapper (and thus the parent launcher) stays alive until
    # all game processes exit.
    prefix = os.environ.get("WINEPREFIX", "")
    pfx = os.path.join(prefix, "pfx") if prefix else ""
    proton = os.environ.get("PROTONPATH", "")
    wineserver = os.path.join(proton, "files", "bin", "wineserver") if proton else ""

    if wineserver and os.path.exists(wineserver) and pfx:
        ws_env = os.environ.copy()
        ws_env["WINEPREFIX"] = pfx

        # Poll until wineserver appears (up to 15s)
        for _ in range(15):
            try:
                r = subprocess.run([wineserver, "--kill", "0"], env=ws_env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if r.returncode == 0:
                    break
            except Exception:
                pass
            time.sleep(1)

        # Block until all Wine processes exit
        logging.info("Waiting for wineserver...")
        try:
            subprocess.run([wineserver, "--wait"], env=ws_env, timeout=14400)
        except (subprocess.TimeoutExpired, Exception):
            pass
        logging.info("Game exited")

    sys.exit(0)

if __name__ == "__main__":
    main()
