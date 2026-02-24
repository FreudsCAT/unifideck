#!/usr/bin/env python3
import sys
import subprocess
import logging
import os

log_file = os.path.expanduser("~/.local/share/unifideck/wrapper_stripper.log")
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s %(message)s')

def main():
    args = sys.argv[1:]
    new_args = []
    
    for arg in args:
        # Strip only the Epic exchange code injection to stop CEF from crashing
        # Keep -AUTH_PASSWORD and -AUTH_LOGIN so EpicGamesLauncher.exe proxy doesn't abort.
        if arg.startswith("-AUTH_TYPE="):
            logging.info(f"Stripped argument: {arg}")
            continue
        new_args.append(arg)
        
    logging.info(f"Executing cleaned command: {' '.join(new_args)}")
    
    try:
        proc = subprocess.Popen(new_args)
        proc.wait()
        sys.exit(proc.returncode)
    except Exception as e:
        logging.error(f"Failed to execute: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
