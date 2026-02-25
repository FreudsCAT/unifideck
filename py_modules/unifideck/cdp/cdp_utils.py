import os
import subprocess


def ensure_dummy_network_interface() -> bool:
    """Create a dummy network interface so Chromium/CEF doesn't block localhost requests.

    When WiFi is off, Chromium's IPv6 viability probe fails (no external interface up),
    so CEF marks the system as 'offline' and blocks ALL renderer HTTP requests —
    including import('http://localhost:1337/...') used by Decky Loader to load plugins.
    A dummy interface with any non-loopback IP prevents this probe from failing.
    See: https://bugs.chromium.org/p/chromium/issues/detail?id=42058

    Returns True if the interface was created, False if it already existed.
    """
    try:
        result = subprocess.run(
            ["ip", "link", "show", "dummy0"],
            capture_output=True, timeout=3
        )
        if result.returncode == 0:
            print("[Unifideck CDP] dummy0 interface already exists, skipping creation")
            return False

        subprocess.run(["ip", "link", "add", "dummy0", "type", "dummy"],
                       check=True, capture_output=True, timeout=3)
        subprocess.run(["ip", "addr", "add", "192.168.168.168/32", "dev", "dummy0"],
                       check=True, capture_output=True, timeout=3)
        subprocess.run(["ip", "link", "set", "dummy0", "up"],
                       check=True, capture_output=True, timeout=3)
        print("[Unifideck CDP] Created dummy0 interface (prevents Chromium offline localhost blocking)")
        return True

    except Exception as e:
        print(f"[Unifideck CDP] Could not create dummy0 interface: {e}")
        return False


def get_steam_path() -> str:
    """Get Steam installation path"""
    # Try common locations
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".local", "share", "Steam"),
        "/home/deck/.steam/steam",  # Steam Deck default
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    raise Exception("Steam path not found")


def create_cef_debugging_flag():
    """Create .cef-enable-remote-debugging flag in Steam folder"""
    try:
        steam_path = get_steam_path()
        flag_path = os.path.join(steam_path, ".cef-enable-remote-debugging")

        if not os.path.exists(flag_path):
            with open(flag_path, 'w') as f:
                pass  # Empty file
            print(f"[Unifideck CDP] Created CEF debugging flag at {flag_path}")
            print("[Unifideck CDP] Steam restart required for CDP to work")
            return True  # Flag was created
        else:
            print(f"[Unifideck CDP] CEF debugging flag already exists")
            return False  # Flag already existed

    except Exception as e:
        print(f"[Unifideck CDP] Failed to create CEF flag: {e}")
        return False
