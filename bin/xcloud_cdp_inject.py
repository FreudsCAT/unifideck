#!/usr/bin/env python3
"""Inject Better xCloud-inspired compatibility helpers into xCloud pages."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp


ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from py_modules.unifideck.cdp.page_inject import inject_scripts, list_page_targets
from py_modules.unifideck.utils.xcloud_compat import (
    get_xcloud_compat_js,
    get_xcloud_navigation_js,
)


STEAM_SHARED_CONTEXT_TITLE = "SharedJSContext"
STEAM_CONTROLLER_LAYOUT_TITLE = "Controller Layout"
WASD_TEMPLATE_URL = "template://controller_neptune_wasd.vdf"
JOYSTICK_TEMPLATE_URL = "template://controller_neptune_gamepad_fps.vdf"


def _build_launch_matches(launch_url: str) -> list[str]:
    """Return resilient match patterns for localized/sluggified xCloud URLs."""
    if not launch_url:
        return []

    parsed = urlparse(launch_url)
    path = parsed.path.rstrip("/")
    product_id = path.split("/")[-1] if path else ""

    matches: list[str] = [launch_url]
    if path:
        matches.append(path)
    if product_id:
        matches.append(product_id)
        matches.append(f"/play/launch/{product_id}")

    deduped: list[str] = []
    for match in matches:
        if match and match not in deduped:
            deduped.append(match)
    return deduped


def _merge_matches(*match_sets: list[str]) -> list[str]:
    merged: list[str] = []
    for match_set in match_sets:
        for match in match_set:
            if match and match not in merged:
                merged.append(match)
    return merged


def _focus_xcloud_window() -> None:
    if shutil.which("xdotool") is None:
        return

    search_commands = [
        ["xdotool", "search", "--onlyvisible", "--classname", "unifideck-xcloud"],
        ["xdotool", "search", "--onlyvisible", "--classname", "www.xbox.com__play"],
        ["xdotool", "search", "--onlyvisible", "--name", "Xbox Cloud Gaming|xbox.com"],
    ]

    for _ in range(20):
        for command in search_commands:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            window_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if not window_ids:
                continue

            window_id = window_ids[-1]
            for activate_cmd in (
                ["xdotool", "windowactivate", "--sync", window_id],
                ["xdotool", "windowraise", window_id],
                ["xdotool", "windowfocus", window_id],
            ):
                subprocess.run(
                    activate_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            logging.info("[steam-layout] Refocused xCloud window %s", window_id)
            return

        time.sleep(0.25)


async def _wait_for_titled_target(
    cdp_port: int,
    title_substring: str,
    *,
    timeout: float = 15.0,
    poll_delay: float = 0.25,
) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            targets = await list_page_targets(cdp_port, timeout=3.0)
            for target in targets:
                if title_substring in str(target.get("title", "")):
                    return dict(target)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - best effort polling
            logging.debug("[steam-layout] waiting for target failed: %s", exc)

        await asyncio.sleep(poll_delay)

    return None


async def _close_target(cdp_port: int, target_id: str) -> None:
    close_url = f"http://127.0.0.1:{cdp_port}/json/close/{target_id}"
    async with aiohttp.ClientSession() as session:
        with contextlib.suppress(Exception):
            async with session.get(
                close_url,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as response:
                await response.read()


async def _close_titled_targets(cdp_port: int, title_substring: str) -> None:
    with contextlib.suppress(Exception):
        targets = await list_page_targets(cdp_port, timeout=3.0)
        for target in targets:
            if title_substring in str(target.get("title", "")):
                await _close_target(cdp_port, str(target["id"]))


async def _cdp_command(
    websocket: aiohttp.ClientWebSocketResponse,
    msg_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await websocket.send_json(
        {
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
    )

    while True:
        message = await websocket.receive(timeout=15)
        if message.type != aiohttp.WSMsgType.TEXT:
            if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                raise RuntimeError("CDP websocket closed")
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError("CDP websocket error")
            continue

        payload = json.loads(message.data)
        if payload.get("id") != msg_id:
            continue
        if "error" in payload:
            raise RuntimeError(f"{method} failed: {payload['error']}")
        return payload


async def _evaluate_in_target(
    target: dict[str, Any],
    expression: str,
    *,
    return_by_value: bool = True,
) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            target["webSocketDebuggerUrl"],
            heartbeat=10,
            autoping=True,
        ) as websocket:
            return await _cdp_command(
                websocket,
                9001,
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": return_by_value,
                    "userGesture": True,
                },
            )


async def _open_controller_popup(steam_port: int, appid: int) -> None:
    shared_target = await _wait_for_titled_target(
        steam_port,
        STEAM_SHARED_CONTEXT_TITLE,
        timeout=10.0,
    )
    if not shared_target:
        raise RuntimeError("SharedJSContext target not found")

    expression = (
        f"(async () => {{ "
        f"window.SteamClient?.Apps?.ShowControllerConfigurator?.({appid}); "
        f"return 'opened'; "
        f"}})()"
    )
    await _evaluate_in_target(shared_target, expression)


async def _resolve_popup_preview_context(
    websocket: aiohttp.ClientWebSocketResponse,
) -> tuple[str, int]:
    msg_id = 1000

    function_resp = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.evaluate",
        {
            "expression": r"""(() => {
                const node = Array.from(document.querySelectorAll('button,[role="link"]'))
                    .find((element) => (element.textContent || '').trim() === 'View Layout');
                if (!node) {
                    return null;
                }
                const fiberKey = Object.keys(node).find((key) => key.startsWith('__reactFiber'));
                let fiber = fiberKey ? node[fiberKey] : null;
                while (fiber) {
                    const props = fiber.memoizedProps || {};
                    if (
                        typeof props.onClick === 'function' &&
                        String(props.onClick).includes('ControllerConfigurator.Summary')
                    ) {
                        return props.onClick;
                    }
                    fiber = fiber.return;
                }
                return null;
            })()""",
            "awaitPromise": True,
            "returnByValue": False,
            "userGesture": True,
        },
    )
    on_click_object = function_resp.get("result", {}).get("result", {}).get("objectId")
    if not on_click_object:
        raise RuntimeError("Could not resolve controller popup preview context")

    msg_id += 1
    on_click_props = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.getProperties",
        {
            "objectId": on_click_object,
            "ownProperties": False,
            "generatePreview": True,
        },
    )
    scopes_object = next(
        (
            item["value"]["objectId"]
            for item in on_click_props.get("result", {}).get("internalProperties", [])
            if item.get("name") == "[[Scopes]]"
        ),
        None,
    )
    if not scopes_object:
        raise RuntimeError("Could not inspect controller popup scopes")

    msg_id += 1
    scopes_resp = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.getProperties",
        {
            "objectId": scopes_object,
            "ownProperties": True,
            "generatePreview": True,
        },
    )
    scope1_object = next(
        (
            item["value"]["objectId"]
            for item in scopes_resp.get("result", {}).get("result", [])
            if item.get("name") == "1"
        ),
        None,
    )
    if not scope1_object:
        raise RuntimeError("Could not resolve configurator module scope")

    msg_id += 1
    scope1_props = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.getProperties",
        {
            "objectId": scope1_object,
            "ownProperties": True,
            "generatePreview": True,
        },
    )
    scope_lookup = {
        prop["name"]: prop.get("value", {}).get("objectId")
        for prop in scope1_props.get("result", {}).get("result", [])
        if prop.get("value", {}).get("objectId")
    }
    h_object = scope_lookup.get("h")
    if not h_object:
        raise RuntimeError("Could not resolve h.v3 controller helper")

    msg_id += 1
    hv3_resp = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.callFunctionOn",
        {
            "objectId": h_object,
            "functionDeclaration": "function(){ return this.v3; }",
            "awaitPromise": True,
            "returnByValue": False,
        },
    )
    h_v3_object = hv3_resp.get("result", {}).get("result", {}).get("objectId")
    if not h_v3_object:
        raise RuntimeError("Could not resolve h.v3 controller helper")

    msg_id += 1
    controller_index_resp = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.callFunctionOn",
        {
            "objectId": h_v3_object,
            "functionDeclaration": (
                "function(){ "
                "return this.EditingConfigurationControllerIndex "
                "?? this.ActiveControllerIndex "
                "?? 15; "
                "}"
            ),
            "awaitPromise": True,
            "returnByValue": True,
        },
    )
    controller_index = (
        controller_index_resp.get("result", {})
        .get("result", {})
        .get("value")
    )
    if not isinstance(controller_index, int):
        raise RuntimeError("Could not resolve controller index from popup")

    return h_v3_object, controller_index


async def _wait_for_popup_root_ready(
    websocket: aiohttp.ClientWebSocketResponse,
    *,
    timeout: float = 5.0,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    msg_id = 1900
    while asyncio.get_running_loop().time() < deadline:
        ready_resp = await _cdp_command(
            websocket,
            msg_id,
            "Runtime.evaluate",
            {
                "expression": r"""(() => Array.from(
                    document.querySelectorAll('button,[role="link"]')
                ).some((element) => (element.textContent || '').trim() === 'View Layout'))()""",
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if ready_resp.get("result", {}).get("result", {}).get("value") is True:
            return True

        msg_id += 1
        await asyncio.sleep(0.25)

    return False


async def _preview_popup_config(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    appid: int,
    controller_index: int,
    config_url: str,
    *,
    msg_id: int,
) -> None:
    await _cdp_command(
        websocket,
        msg_id,
        "Runtime.callFunctionOn",
        {
            "objectId": h_v3_object,
            "functionDeclaration": (
                "function(appid, controllerIndex, url){ "
                "this.EnsureEditingConfiguration(appid, controllerIndex); "
                "this.PreviewConfiguration(appid, controllerIndex, url); "
                "return true; "
                "}"
            ),
            "arguments": [
                {"value": appid},
                {"value": controller_index},
                {"value": config_url},
            ],
            "awaitPromise": True,
            "returnByValue": True,
        },
    )


async def _set_active_popup_config(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    appid: int,
    controller_index: int,
    config_url: str,
    *,
    msg_id: int,
) -> None:
    await _cdp_command(
        websocket,
        msg_id,
        "Runtime.callFunctionOn",
        {
            "objectId": h_v3_object,
            "functionDeclaration": (
                "function(appid, controllerIndex, url){ "
                "this.SetActiveConfigForApp(appid, controllerIndex, url, false); "
                "this.SaveEditingConfiguration(appid); "
                "if (typeof this.ClearSelectedConfigCache === 'function') { "
                "  this.ClearSelectedConfigCache(appid); "
                "} "
                "this.EnsureEditingConfiguration(appid, controllerIndex); "
                "return true; "
                "}"
            ),
            "arguments": [
                {"value": appid},
                {"value": controller_index},
                {"value": config_url},
            ],
            "awaitPromise": True,
            "returnByValue": True,
        },
    )


async def _inspect_popup_state(
    websocket: aiohttp.ClientWebSocketResponse,
    *,
    msg_id: int,
) -> dict[str, Any]:
    state_resp = await _cdp_command(
        websocket,
        msg_id,
        "Runtime.evaluate",
        {
            "expression": r"""(() => {
                const node = Array.from(document.querySelectorAll('button,[role="link"]'))
                    .find((element) => (
                        (element.textContent || '').includes('Official Layout for') ||
                        (element.textContent || '').includes('Using Template:') ||
                        (element.textContent || '').includes('Gamepad With Joystick Trackpad') ||
                        (element.textContent || '').includes('Keyboard (WASD) and Mouse')
                    ));
                const fiberKey = node ? Object.keys(node).find((key) => key.startsWith('__reactFiber')) : null;
                let fiber = fiberKey ? node[fiberKey] : null;
                let config = null;
                while (fiber) {
                    const props = fiber.memoizedProps || {};
                    if (props.config && typeof props.config === 'object') {
                        config = props.config;
                        break;
                    }
                    fiber = fiber.return;
                }
                return {
                    body: document.body ? document.body.innerText.slice(0, 1200) : null,
                    title: config?.Title || null,
                    url: config?.URL || null,
                    progenitor: config?.ProgenitorURL || null,
                    usesMouse: config?.bUsesMouse ?? null,
                    usesKeyboard: config?.bUsesKeyboard ?? null,
                    usesGamepad: config?.bUsesGamepad ?? null,
                };
            })()""",
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        },
    )
    value = state_resp.get("result", {}).get("result", {}).get("value")
    return value if isinstance(value, dict) else {}


async def _refresh_steam_controller_layout(
    steam_port: int,
    shortcut_appid: int,
    *,
    delay: float,
    dwell: float,
) -> bool:
    if shortcut_appid <= 0:
        return False

    await asyncio.sleep(delay)
    await _close_titled_targets(steam_port, STEAM_CONTROLLER_LAYOUT_TITLE)

    popup_target_id: str | None = None
    try:
        logging.info(
            "[steam-layout] Opening controller configurator for AppID %s",
            shortcut_appid,
        )
        await _open_controller_popup(steam_port, shortcut_appid)

        popup_target = await _wait_for_titled_target(
            steam_port,
            STEAM_CONTROLLER_LAYOUT_TITLE,
            timeout=15.0,
        )
        if not popup_target:
            raise RuntimeError("Controller Layout popup did not open")
        popup_target_id = str(popup_target["id"])

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                popup_target["webSocketDebuggerUrl"],
                heartbeat=10,
                autoping=True,
            ) as websocket:
                if not await _wait_for_popup_root_ready(websocket):
                    raise RuntimeError("Controller Layout popup never reached the root page")
                h_v3_object, controller_index = await _resolve_popup_preview_context(
                    websocket
                )
                logging.info(
                    "[steam-layout] Using controller index %s for AppID %s",
                    controller_index,
                    shortcut_appid,
                )

                await _preview_popup_config(
                    websocket,
                    h_v3_object,
                    shortcut_appid,
                    controller_index,
                    WASD_TEMPLATE_URL,
                    msg_id=2001,
                )
                await asyncio.sleep(dwell)
                wasd_state = await _inspect_popup_state(websocket, msg_id=2002)
                logging.info(
                    "[steam-layout] after-wasd title=%s url=%s body=%s",
                    wasd_state.get("title"),
                    wasd_state.get("url"),
                    (wasd_state.get("body") or "")[:180],
                )

                await _set_active_popup_config(
                    websocket,
                    h_v3_object,
                    shortcut_appid,
                    controller_index,
                    JOYSTICK_TEMPLATE_URL,
                    msg_id=2003,
                )
                await asyncio.sleep(0.5)
                await _preview_popup_config(
                    websocket,
                    h_v3_object,
                    shortcut_appid,
                    controller_index,
                    JOYSTICK_TEMPLATE_URL,
                    msg_id=2004,
                )
                await asyncio.sleep(0.75)
                final_state = await _inspect_popup_state(websocket, msg_id=2005)
                logging.info(
                    "[steam-layout] after-joystick title=%s url=%s body=%s",
                    final_state.get("title"),
                    final_state.get("url"),
                    (final_state.get("body") or "")[:180],
                )
                return final_state.get("url") == JOYSTICK_TEMPLATE_URL
    except Exception as exc:
        logging.exception("[steam-layout] Popup bounce failed: %s", exc)
        return False
    finally:
        if popup_target_id:
            await _close_target(steam_port, popup_target_id)
        else:
            await _close_titled_targets(steam_port, STEAM_CONTROLLER_LAYOUT_TITLE)
        await asyncio.to_thread(_focus_xcloud_window)


async def _run(
    port: int,
    timeout: float,
    matches: list[str],
    launch_url: str,
    *,
    steam_port: int,
    steam_controller_appid: int,
    steam_controller_delay: float,
    steam_controller_dwell: float,
) -> int:
    compat_js = get_xcloud_compat_js()
    navigation_js = get_xcloud_navigation_js(launch_url) if launch_url else ""
    final_matches = _build_launch_matches(launch_url)
    initial_matches = _merge_matches(matches, final_matches)
    initial_sources = [compat_js]
    if navigation_js:
        initial_sources.append(navigation_js)

    ok = await inject_scripts(
        port,
        initial_sources,
        url_patterns=initial_matches,
        timeout=timeout,
        logger_prefix="xcloud-cdp",
    )
    if not ok:
        return 1

    if launch_url:
        ok = await inject_scripts(
            port,
            [compat_js, navigation_js] if navigation_js else [compat_js],
            url_patterns=final_matches,
            timeout=timeout,
            logger_prefix="xcloud-cdp-final",
        )
        if not ok:
            return 1

    controller_ok = True
    if steam_controller_appid > 0:
        controller_ok = await _refresh_steam_controller_layout(
            steam_port,
            steam_controller_appid,
            delay=steam_controller_delay,
            dwell=steam_controller_dwell,
        )

    return 0 if controller_ok else 1


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
    parser.add_argument(
        "--steam-port",
        type=int,
        default=8080,
        help="Steam webhelper CDP port for controller-layout popup automation.",
    )
    parser.add_argument(
        "--steam-controller-appid",
        type=int,
        default=0,
        help="Steam shortcut AppID to bounce through the controller layout popup.",
    )
    parser.add_argument(
        "--steam-controller-delay",
        type=float,
        default=10.0,
        help="Seconds to wait after xCloud is ready before running the popup bounce.",
    )
    parser.add_argument(
        "--steam-controller-dwell",
        type=float,
        default=2.5,
        help="Seconds to stay on the WASD template before restoring joystick.",
    )
    args = parser.parse_args()
    if not args.matches:
        args.matches = ["xbox.com"]

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s][%(levelname)s]: %(message)s",
    )
    return asyncio.run(
        _run(
            args.port,
            args.timeout,
            args.matches,
            args.launch_url,
            steam_port=args.steam_port,
            steam_controller_appid=args.steam_controller_appid,
            steam_controller_delay=args.steam_controller_delay,
            steam_controller_dwell=args.steam_controller_dwell,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
