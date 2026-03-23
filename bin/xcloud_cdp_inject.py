#!/usr/bin/env python3
"""Inject Better xCloud-inspired compatibility helpers into xCloud pages."""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from py_modules.unifideck.cdp.page_inject import inject_scripts
from py_modules.unifideck.utils.xcloud_compat import (
    get_xcloud_compat_js,
    get_xcloud_navigation_js,
)


async def _run(
    port: int,
    timeout: float,
    matches: list[str],
    launch_url: str,
) -> int:
    compat_js = get_xcloud_compat_js()
    navigation_js = get_xcloud_navigation_js(launch_url) if launch_url else ""
    initial_sources = [compat_js]
    if navigation_js:
        initial_sources.append(navigation_js)

    ok = await inject_scripts(
        port,
        initial_sources,
        url_patterns=matches,
        timeout=timeout,
        logger_prefix="xcloud-cdp",
    )
    if not ok:
        return 1

    if launch_url:
        ok = await inject_scripts(
            port,
            [compat_js, navigation_js] if navigation_js else [compat_js],
            url_patterns=[launch_url],
            timeout=timeout,
            logger_prefix="xcloud-cdp-final",
        )

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--match",
        dest="matches",
        action="append",
        default=None,
        help="Substring to match against the target page URL. Repeat as needed.",
    )
    parser.add_argument(
        "--launch-url",
        default="",
        help="If provided, navigate to this xCloud launch URL after injection.",
    )
    args = parser.parse_args()
    if not args.matches:
        args.matches = ["xbox.com/play"]

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s][%(levelname)s]: %(message)s",
    )
    return asyncio.run(
        _run(args.port, args.timeout, args.matches, args.launch_url),
    )


if __name__ == "__main__":
    raise SystemExit(main())
