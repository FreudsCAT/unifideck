#!/usr/bin/env python3
"""Probe repo.steampowered.com for what umu can and cannot fetch.

Standalone and stdlib-only on purpose: copy it to any machine (including a
reporter's Deck) and run it with no venv, no project imports, no install.

    python3 probe_steamrt_repo.py                 # full report
    python3 probe_steamrt_repo.py -v steamrt4     # one variant
    python3 probe_steamrt_repo.py --download      # really fetch an archive

WHY THIS EXISTS
---------------
How umu reaches the Steam Linux Runtime changed, and the two generations
fail in completely different places:

  * umu <=1.4.1 fetched straight out of the ``latest-*`` SYMLINK DIRECTORY::

        https://repo.steampowered.com/<variant>/images/latest-public-beta/...

    The repo now answers those with HTTP 403, which is fatal but asymmetric
    and easy to misread: ``_update_umu`` (runtime already on disk) logs the
    error and carries on, so those machines keep working indefinitely, while
    ``_install_umu`` (runtime absent) raises — a machine that has LOST its
    runtime can never get one back.

  * umu >=1.4.3 (what Unifideck bundles) reads a small VERSION FILE::

        https://repo.steampowered.com/<variant>/images/latest-public-beta.txt

    then fetches from the numbered directory that file names. Both serve
    normally, so this generation installs fine.

That difference is why this script probes BOTH shapes and decides on the
``.txt`` one: judging a modern umu by the symlink directories reports a
false "umu cannot install" on a perfectly healthy machine.

"It works for me" proves nothing about whether a fresh install works. This
script asks the question directly, and separates a *path* problem (the repo
refuses that URL for everyone) from a *client* problem (our UA, token, TLS,
or network), which the raw umu error cannot distinguish.

It replicates umu's own derivations exactly, including the non-obvious one:
the archive filename comes from the CODENAME, not the variant, so steamrt3
ships SteamLinuxRuntime_sniper.tar.xz while steamrt4 ships
SteamLinuxRuntime_4.tar.xz.
"""

from __future__ import annotations

import argparse
import re
import ssl
import sys
import urllib.error
import urllib.request
from secrets import token_urlsafe

HOST = "https://repo.steampowered.com"

# variant -> codename. Mirrors umu/__init__.py's RUNTIMEVERSIONS table.
VARIANTS: dict[str, str] = {
    "steamrt2": "soldier",
    "steamrt3": "sniper",
    "steamrt4": "steamrt4",
}

# The version file umu >=1.4.3 reads. THIS is the deciding probe: it names
# the numbered dir everything else is fetched from, so if it serves and that
# dir serves, the bundled umu can install.
VERSION_FILE = "latest-public-beta.txt"

# Named channel directories, used by umu <=1.4.1 and kept only as context —
# they are expected to 403 now and that is no longer a blocker on its own.
CHANNELS: tuple[str, ...] = (
    "latest-public-beta",  # <- what umu <=1.4.1 hardcoded
    "latest-public-stable",
    "latest-container-runtime-public-beta",
    "latest-container-runtime-depot",
)

# The three metadata files umu fetches before the archive.
META_FILES: tuple[str, ...] = ("SHA256SUMS", "BUILD_ID.txt", "VERSION.txt")

UMU_UA = "umu-launcher"
BROWSER_UA = "Mozilla/5.0"


def archive_name(variant: str) -> str:
    """Tarball filename, derived the way umu derives it."""
    codename = VARIANTS[variant]
    suffix = codename.removeprefix("steamrt")
    return f"SteamLinuxRuntime_{suffix if suffix.isdigit() else codename}.tar.xz"


def _ctx() -> ssl.SSLContext:
    """Permissive TLS — the Deck's CA store is often too old to validate."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def probe(
    url: str,
    *,
    method: str = "GET",
    ua: str = UMU_UA,
    timeout: float = 20.0,
    read: bool = False,
) -> tuple[int | str, int | None, bytes]:
    """Return ``(status, content_length, body)``. Status is an int or a string."""
    req = urllib.request.Request(url, method=method)  # noqa: S310 — https literal
    if ua:
        req.add_header("User-Agent", ua)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:  # noqa: S310
            length = r.headers.get("Content-Length")
            body = r.read() if read else b""
            return r.status, int(length) if length else None, body
    except urllib.error.HTTPError as e:
        return e.code, None, b""
    except Exception as e:  # noqa: BLE001 — a probe reports failures, never raises
        return f"ERR {type(e).__name__}: {e}", None, b""


def mark(status: int | str) -> str:
    """Short verdict glyph for a status."""
    if status == 200:
        return "OK  "
    if status == 403:
        return "403!"
    return "??  "


def human(n: int | None) -> str:
    """Content-Length as MB, when present."""
    return f"{n / 1048576:.1f} MB" if n else "-"


def numeric_versions(variant: str, timeout: float) -> list[str]:
    """Numbered version dirs from the images/ index, oldest first."""
    status, _, body = probe(
        f"{HOST}/{variant}/images/", ua=BROWSER_UA, timeout=timeout, read=True,
    )
    if status != 200:
        return []
    found = re.findall(r'href="(\d[\d.]*)/"', body.decode("utf-8", "replace"))
    return sorted(found, key=lambda v: [int(p) for p in v.split(".") if p.isdigit()])


def section(title: str) -> None:
    """Print a section header."""
    print(f"\n{title}\n{'-' * len(title)}")


def check_reachability(timeout: float) -> None:
    """Confirm the host answers at all, so 403s below mean something."""
    section("REACHABILITY (control)")
    for label, url in (
        ("host root", f"{HOST}/"),
        ("steamrt3 index", f"{HOST}/steamrt3/images/"),
        ("steamrt4 index", f"{HOST}/steamrt4/images/"),
    ):
        status, _, _ = probe(url, ua=BROWSER_UA, timeout=timeout)
        print(f"  {mark(status)} {status!s:<6} {label}")


def check_variant(variant: str, timeout: float, download: bool) -> dict[str, bool]:
    """Probe every channel and the newest numbered dir for one variant."""
    arc = archive_name(variant)
    section(f"{variant}  (codename={VARIANTS[variant]}, archive={arc})")

    versions = numeric_versions(variant, timeout)
    newest = versions[-1] if versions else None
    print(f"  numbered versions listed: {len(versions)}"
          f"{f', newest {newest}' if newest else ''}")

    # The deciding probe for the bundled umu (>=1.4.3).
    print(f"\n  version file (what umu >=1.4.3 uses):")
    vf_url = f"{HOST}/{variant}/images/{VERSION_FILE}"
    vf_status, _, vf_body = probe(vf_url, timeout=timeout, read=True)
    pinned = vf_body.decode("utf-8", "replace").strip() if vf_body else ""
    print(f"    {mark(vf_status)} {vf_status!s:<6} {VERSION_FILE}"
          f"{f'  -> {pinned}' if pinned else ''}")

    # Follow it exactly as umu does: the version it names must also serve.
    version_file_ok = False
    if vf_status == 200 and pinned:
        for f in META_FILES:
            status, _, _ = probe(
                f"{HOST}/{variant}/images/{pinned}/{f}", timeout=timeout,
            )
            version_file_ok = version_file_ok or status == 200
            print(f"    {mark(status)} {status!s:<6} {pinned}/{f}")

    print("\n  named channels (what umu <=1.4.1 used — legacy context):")
    channel_ok = False
    for ch in CHANNELS:
        row = []
        for f in META_FILES:
            status, _, _ = probe(
                f"{HOST}/{variant}/images/{ch}/{f}?versions={token_urlsafe(16)}",
                timeout=timeout,
            )
            row.append(f"{f}={status}")
            channel_ok = channel_ok or status == 200
        print(f"    {ch:<38} {'  '.join(row)}")

    numeric_ok = False
    if newest:
        print(f"\n  newest numbered dir ({newest}):")
        for f in META_FILES:
            status, _, _ = probe(
                f"{HOST}/{variant}/images/{newest}/{f}", timeout=timeout,
            )
            numeric_ok = numeric_ok or status == 200
            print(f"    {mark(status)} {status!s:<6} {f}")
        status, length, _ = probe(
            f"{HOST}/{variant}/images/{newest}/{arc}",
            method="HEAD", timeout=timeout,
        )
        print(f"    {mark(status)} {status!s:<6} {arc}  ({human(length)})")
        if download and status == 200:
            print("      downloading first 1 MB to confirm the body streams...")
            got = _stream_probe(f"{HOST}/{variant}/images/{newest}/{arc}", timeout)
            print(f"      read {got} bytes")

    return {
        "version_file": version_file_ok,
        "channel": channel_ok,
        "numeric": numeric_ok,
    }


def _stream_probe(url: str, timeout: float) -> int:
    """Read ~1 MB of ``url`` to prove the body actually transfers."""
    req = urllib.request.Request(url, headers={"User-Agent": UMU_UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:  # noqa: S310
            return len(r.read(1024 * 1024))
    except Exception as e:  # noqa: BLE001
        print(f"      stream failed: {e}")
        return 0


def check_client_shape(timeout: float) -> None:
    """Vary only the CLIENT against one URL, to rule client factors in/out.

    Aimed at the version file the bundled umu actually requests. Pointed at
    a ``latest-*`` directory it only ever demonstrated that the directory
    403s for every client shape — true, but no longer the question.
    """
    section("CLIENT-SHAPE MATRIX (same URL, different request shapes)")
    url = f"{HOST}/steamrt4/images/{VERSION_FILE}"
    cases = (
        ("umu UA + token", f"{url}?versions={token_urlsafe(16)}", UMU_UA),
        ("umu UA, no token", url, UMU_UA),
        ("browser UA, no token", url, BROWSER_UA),
        ("no UA header", url, ""),
    )
    for label, u, ua in cases:
        status, _, _ = probe(u, ua=ua, timeout=timeout)
        print(f"  {mark(status)} {status!s:<6} {label}")

    print("\n  same shapes against a NUMBERED dir (the control):")
    versions = numeric_versions("steamrt4", timeout)
    if versions:
        nurl = f"{HOST}/steamrt4/images/{versions[-1]}/SHA256SUMS"
        for label, ua in (("umu UA", UMU_UA), ("no UA header", "")):
            status, _, _ = probe(nurl, ua=ua, timeout=timeout)
            print(f"  {mark(status)} {status!s:<6} {label}")


def verdict(results: dict[str, dict[str, bool]]) -> int:
    """Summarise into the one conclusion that drives the fix.

    Decided on the VERSION FILE, because that is what the bundled umu
    (>=1.4.3) actually uses. The ``latest-*`` channel directories are
    reported for context only: they are expected to 403 now, and treating
    that as the failure signal — as this script originally did — reports
    "umu cannot install" on a machine where umu installs perfectly well.
    """
    section("VERDICT")
    any_version_file = any(r["version_file"] for r in results.values())
    any_channel = any(r["channel"] for r in results.values())
    any_numeric = any(r["numeric"] for r in results.values())

    if any_version_file:
        print("  Version file + the numbered dir it names are SERVING.")
        print("  -> The bundled umu (>=1.4.3) CAN install the runtime.")
        if not any_channel:
            print("  -> Legacy latest-* channel dirs are blocked, which is")
            print("     expected and affects only umu <=1.4.1.")
        print("  -> A runtime failure here is NOT a repo-access issue.")
        return 0
    if any_channel or any_numeric:
        print("  Version file is BLOCKED, but other paths still serve"
              f" ({'channels' if any_channel else 'numbered dirs'}).")
        print("  -> The bundled umu cannot install: it reads")
        print(f"     images/{VERSION_FILE} first and gives up if that fails.")
        print("  -> Machines with a cached runtime keep working (the update")
        print("     path tolerates the error); machines without one are stuck.")
        print("  -> Check whether upstream repointed the URL again, and bump")
        print("     umu to match; else pin a version and install it ourselves.")
        return 1
    print("  NOTHING is serving — version file, channels, numbered dirs and")
    print("  the index alike. Suspect local network/DNS/TLS or a full outage.")
    return 2


def main() -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "-v", "--variant", choices=sorted(VARIANTS), action="append",
        help="limit to this variant (repeatable; default: all)",
    )
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument(
        "--download", action="store_true",
        help="also read 1 MB of the archive to prove the body transfers",
    )
    args = ap.parse_args()

    print(f"Probing {HOST}")
    print(f"python {sys.version.split()[0]}")
    check_reachability(args.timeout)

    results = {
        v: check_variant(v, args.timeout, args.download)
        for v in (args.variant or sorted(VARIANTS))
    }
    check_client_shape(args.timeout)
    return verdict(results)


if __name__ == "__main__":
    raise SystemExit(main())
