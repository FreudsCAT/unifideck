from __future__ import annotations

import contextlib
import logging
from typing import Any

import aiohttp

from .cdp_primitives import cdp_command

logger = logging.getLogger(__name__)
async def _click_handler_object_id(
    websocket: aiohttp.ClientWebSocketResponse,
    msg_id: int,
) -> str:
    """Click handler object ID."""
    resp = await cdp_command(
        websocket,
        msg_id,
        "Runtime.evaluate",
        {
            "expression": r"""(() => {
                const node = Array.from(
                    document.querySelectorAll('button,[role="link"]')
                ).find((element) => (element.textContent || '').trim() === 'View Layout');
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
    object_id = resp.get("result", {}).get("result", {}).get("objectId")
    if not object_id:
        raise RuntimeError("Could not resolve controller popup preview context")
    return str(object_id)

async def _scopes_object_id(
    websocket: aiohttp.ClientWebSocketResponse,
    on_click_object: str,
    msg_id: int,
) -> str:

    """Scopes object ID."""
    resp = await cdp_command(
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
            for item in resp.get("result", {}).get("internalProperties", [])
            if item.get("name") == "[[Scopes]]"
        ),
        None,
    )
    if not scopes_object:
        raise RuntimeError("Could not inspect controller popup scopes")
    return str(scopes_object)
async def _scope1_object_id(
    websocket: aiohttp.ClientWebSocketResponse,
    scopes_object: str,
    msg_id: int,
) -> str:
    """Scope1 object ID."""
    resp = await cdp_command(
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
            for item in resp.get("result", {}).get("result", [])
            if item.get("name") == "1"
        ),
        None,
    )
    if not scope1_object:
        raise RuntimeError("Could not resolve configurator module scope")
    return str(scope1_object)
async def _scope1_h_object_id(
    websocket: aiohttp.ClientWebSocketResponse,
    scope1_object: str,
    msg_id: int,
) -> str:
    """Scope1 h object ID."""
    resp = await cdp_command(
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
        for prop in resp.get("result", {}).get("result", [])
        if prop.get("value", {}).get("objectId")
    }
    h_object = scope_lookup.get("h")
    if not h_object:
        raise RuntimeError("Could not resolve h.v3 controller helper")
    return str(h_object)

async def _h_v3_object_id(
    websocket: aiohttp.ClientWebSocketResponse,
    h_object: str,
    msg_id: int,
) -> tuple[str, int]:

    """H v3 object ID."""
    resp = await cdp_command(
        websocket,
        msg_id,
        "Runtime.getProperties",
        {
            "objectId": h_object,
            "ownProperties": True,
            "generatePreview": True,
        },
    )
    v3_object = next(
        (
            prop.get("value", {}).get("objectId")
            for prop in resp.get("result", {}).get("result", [])
            if prop.get("name") == "v3"
        ),
        None,
    )
    if not v3_object:
        raise RuntimeError("Could not resolve h.v3 sub-object")
    controller_index = 0
    with contextlib.suppress(Exception):
        v3_props = await cdp_command(
            websocket,
            msg_id + 1,
            "Runtime.getProperties",
            {"objectId": v3_object, "ownProperties": True},
        )
        for prop in v3_props.get("result", {}).get("result", []):
            if prop.get("name") == "currentController":
                value = prop.get("value", {}).get("value")
                if isinstance(value, int):
                    controller_index = value
                    break
    return str(v3_object), controller_index
async def resolve_popup_preview_context(
    websocket: aiohttp.ClientWebSocketResponse,
) -> tuple[str, int]:
    """Resolve popup preview context."""
    on_click = await _click_handler_object_id(websocket, 1000)
    scopes = await _scopes_object_id(websocket, on_click, 1001)
    scope1 = await _scope1_object_id(websocket, scopes, 1002)
    h_obj = await _scope1_h_object_id(websocket, scope1, 1003)
    return await _h_v3_object_id(websocket, h_obj, 1004)
async def preview_popup_config(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    appid: int,
    controller_index: int,
    config_url: str,
    *,
    msg_id: int,
) -> None:
    """Preview popup config."""
    await cdp_command(
        websocket,
        msg_id,
        "Runtime.callFunctionOn",
        {
            "objectId": h_v3_object,
            "functionDeclaration": (
                "function(appid, controllerIndex, url){ "
                "this.PreviewConfigForApp(appid, controllerIndex, url); "
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

async def set_active_popup_config(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    appid: int,
    controller_index: int,
    config_url: str,
    *,
    msg_id: int,
) -> None:

    """Set active popup config."""
    await cdp_command(
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
                "    this.ClearSelectedConfigCache(appid); "
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

async def inspect_popup_state(
    websocket: aiohttp.ClientWebSocketResponse,
    *,
    msg_id: int,
) -> dict[str, Any]:

    """Inspect popup state."""
    state_resp = await cdp_command(
        websocket,
        msg_id,
        "Runtime.evaluate",
        {
            "expression": r"""(() => {
                const node = Array.from(
                    document.querySelectorAll('button,[role="link"]')
                ).find((element) => (
                    (element.textContent || '').includes('Official Layout for') ||
                    (element.textContent || '').includes('Using Template:') ||
                    (element.textContent || '').includes('Gamepad With Joystick Trackpad') ||
                    (element.textContent || '').includes('Keyboard (WASD) and Mouse')
                ));
                const fiberKey = node ? Object.keys(node).find(
                    (key) => key.startsWith('__reactFiber')
                ) : null;
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
                    body: document.body
                        ? document.body.innerText.slice(0, 1200)
                        : null,
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
