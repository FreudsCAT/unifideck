from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .cdp_primitives import (
    close_target,
    close_titled_targets,
    wait_for_titled_target,
)
from .steam_controller_popup_fiber import (
    inspect_popup_state,
    preview_popup_config,
    resolve_popup_preview_context,
    set_active_popup_config,
)
from .steam_controller_popup_targets import (
    open_controller_popup,
    wait_for_popup_root_ready,
)

logger = logging.getLogger(__name__)
_STEAM_CONTROLLER_LAYOUT_TITLE = "Controller Layout"
_WASD_TEMPLATE_URL = "template://controller_neptune_wasd.vdf"
_JOYSTICK_TEMPLATE_URL = "template://controller_neptune_gamepad_fps.vdf"
_PostBounceHook = Callable[[], Awaitable[Any]] | Callable[[], Any] | None
async def _phase1_preview_wasd(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    shortcut_appid: int,
    controller_index: int,
    dwell: float,
) -> dict[str, Any]:
    """Phase1 preview wasd."""
    await preview_popup_config(
        websocket,
        h_v3_object,
        shortcut_appid,
        controller_index,
        _WASD_TEMPLATE_URL,
        msg_id=2001,
    )
    await asyncio.sleep(dwell)
    return await inspect_popup_state(websocket, msg_id=2002)
async def _phase2_activate_joystick(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    shortcut_appid: int,
    controller_index: int,
) -> None:
    """Phase2 activate joystick."""
    await set_active_popup_config(
        websocket,
        h_v3_object,
        shortcut_appid,
        controller_index,
        _JOYSTICK_TEMPLATE_URL,
        msg_id=2003,
    )
    await asyncio.sleep(0.5)

async def _phase3_confirm_joystick(
    websocket: aiohttp.ClientWebSocketResponse,
    h_v3_object: str,
    shortcut_appid: int,
    controller_index: int,
) -> dict[str, Any]:

    """Phase3 confirm joystick."""
    await preview_popup_config(
        websocket,
        h_v3_object,
        shortcut_appid,
        controller_index,
        _JOYSTICK_TEMPLATE_URL,
        msg_id=2004,
    )
    await asyncio.sleep(0.75)
    return await inspect_popup_state(websocket, msg_id=2005)
async def _run_bounce_sequence(
    websocket: aiohttp.ClientWebSocketResponse,
    shortcut_appid: int,
    dwell: float,
) -> bool:
    """Run bounce sequence."""
    if not await wait_for_popup_root_ready(websocket):
        raise RuntimeError(
            "Controller Layout popup never reached the root page",
        )
    h_v3_object, controller_index = await resolve_popup_preview_context(
        websocket,
    )
    logger.info(
        "[popup] using controller index %s for AppID %s",
        controller_index,
        shortcut_appid,
    )
    wasd_state = await _phase1_preview_wasd(
        websocket, h_v3_object, shortcut_appid, controller_index, dwell,
    )
    logger.info(
        "[popup] after-wasd title=%s url=%s",
        wasd_state.get("title"), wasd_state.get("url"),
    )
    await _phase2_activate_joystick(
        websocket, h_v3_object, shortcut_appid, controller_index,
    )
    final_state = await _phase3_confirm_joystick(
        websocket, h_v3_object, shortcut_appid, controller_index,
    )
    logger.info(
        "[popup] after-joystick title=%s url=%s",
        final_state.get("title"), final_state.get("url"),
    )
    return final_state.get("url") == _JOYSTICK_TEMPLATE_URL
async def _open_popup_and_run_bounce(
    steam_port: int,
    shortcut_appid: int,
    dwell: float,
) -> tuple[bool, str | None]:
    """Open popup and run bounce."""
    logger.info(
        "[popup] opening controller configurator for AppID %s",
        shortcut_appid,
    )
    await open_controller_popup(steam_port, shortcut_appid)
    popup_target = await wait_for_titled_target(
        steam_port, _STEAM_CONTROLLER_LAYOUT_TITLE, timeout=15.0,
    )
    if not popup_target:
        raise RuntimeError("Controller Layout popup did not open")
    popup_target_id = str(popup_target["id"])
    async with aiohttp.ClientSession() as session, session.ws_connect(
        popup_target["webSocketDebuggerUrl"], heartbeat=10, autoping=True,
    ) as websocket:
        success = await _run_bounce_sequence(
            websocket, shortcut_appid, dwell,
        )
    return success, popup_target_id
async def _close_popup(steam_port: int, popup_target_id: str | None) -> None:
    """Close popup."""
    if popup_target_id:
        await close_target(steam_port, popup_target_id)
    else:
        await close_titled_targets(
            steam_port, _STEAM_CONTROLLER_LAYOUT_TITLE,
        )

async def _invoke_post_bounce_hook(on_complete: _PostBounceHook) -> None:

    """Invoke post bounce hook."""
    if on_complete is None:
        return
    try:
        result = on_complete()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug("[popup] on_complete hook failed: %s", exc)
async def refresh_steam_controller_layout(
    steam_port: int,
    shortcut_appid: int,
    *,
    delay: float,
    dwell: float,
    on_complete: _PostBounceHook = None,
) -> bool:
    """Refresh steam controller layout."""
    if shortcut_appid <= 0:
        return False
    await asyncio.sleep(delay)
    await close_titled_targets(steam_port, _STEAM_CONTROLLER_LAYOUT_TITLE)
    popup_target_id: str | None = None
    success = False
    try:
        success, popup_target_id = await _open_popup_and_run_bounce(
            steam_port, shortcut_appid, dwell,
        )
    except Exception:
        logger.exception("[popup] bounce failed")
    finally:
        await _close_popup(steam_port, popup_target_id)
        await _invoke_post_bounce_hook(on_complete)
    return success
