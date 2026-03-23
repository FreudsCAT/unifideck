"""
Better xCloud-inspired compatibility helpers for xbox.com/play.

This does not attempt Better xCloud's brittle bundle patching. Instead it
ports the low-risk parts that help the Steam Deck/browser combination:

- re-dispatch connected gamepads so xCloud notices already-attached pads
- re-sync gamepads when focus/visibility changes
- provide fullscreen/pointer-lock shims that xCloud expects
"""

import json


_XCLOUD_COMPAT_JS = r"""
(function() {
  'use strict';
  if (window.__unifideck_xcloud_helper) return;

  var state = {
    injectedAt: Date.now(),
    reconnects: 0,
    lastReason: 'init',
  };
  window.__unifideck_xcloud_helper = state;

  function safeFocus() {
    try {
      if (typeof window.focus === 'function') window.focus();
    } catch (e) {}
  }

  var pointerLockElement = null;

  try {
    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: function() {
        return document.documentElement;
      },
    });
  } catch (e) {}

  try {
    if (typeof HTMLElement.prototype.requestFullscreen !== 'function') {
      HTMLElement.prototype.requestFullscreen = function() {
        return Promise.resolve();
      };
    }
  } catch (e) {}

  try {
    Object.defineProperty(document, 'pointerLockElement', {
      configurable: true,
      get: function() {
        return pointerLockElement;
      },
    });
  } catch (e) {}

  try {
    HTMLElement.prototype.requestPointerLock = function() {
      pointerLockElement = document.documentElement;
      document.dispatchEvent(new Event('pointerlockchange'));
    };
  } catch (e) {}

  try {
    document.exitPointerLock = function() {
      pointerLockElement = null;
      document.dispatchEvent(new Event('pointerlockchange'));
    };
  } catch (e) {}

  function getConnectedGamepads() {
    if (typeof navigator.getGamepads !== 'function') {
      return [];
    }

    var pads = navigator.getGamepads() || [];
    var result = [];
    for (var i = 0; i < pads.length; i += 1) {
      var pad = pads[i];
      if (pad && pad.connected) {
        result.push(pad);
      }
    }
    return result;
  }

  function dispatchReconnect(gamepad, reason) {
    if (!gamepad) return;
    state.reconnects += 1;
    state.lastReason = reason;

    try {
      window.dispatchEvent(new GamepadEvent('gamepaddisconnected', { gamepad: gamepad }));
    } catch (e) {}

    try {
      window.dispatchEvent(new GamepadEvent('gamepadconnected', { gamepad: gamepad }));
    } catch (e) {}
  }

  function resyncGamepads(reason) {
    safeFocus();
    var pads = getConnectedGamepads();
    state.lastCount = pads.length;
    state.lastPadIds = pads.map(function(pad) { return pad.id; });
    for (var i = 0; i < pads.length; i += 1) {
      dispatchReconnect(pads[i], reason);
    }
  }

  function periodicScan() {
    getConnectedGamepads();
  }

  window.addEventListener('focus', function() {
    window.setTimeout(function() { resyncGamepads('focus'); }, 50);
  });

  document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
      window.setTimeout(function() { resyncGamepads('visibility'); }, 50);
    }
  });

  window.setTimeout(function() { resyncGamepads('startup-1s'); }, 1000);
  window.setTimeout(function() { resyncGamepads('startup-3s'); }, 3000);
  window.setInterval(periodicScan, 1000);
})();
"""


def get_xcloud_compat_js() -> str:
    """Return the xCloud compatibility helper JavaScript."""
    return _XCLOUD_COMPAT_JS


def get_xcloud_navigation_js(target_url: str) -> str:
    """Return JS that redirects the root xCloud shell to the requested game."""
    if not target_url:
        return ""

    encoded_target = json.dumps(target_url)
    return f"""
(function() {{
  'use strict';
  var targetUrl = {encoded_target};
  if (!targetUrl) return;

  window.__unifideck_xcloud_target_url = targetUrl;
  if (window.location.href === targetUrl) return;
  window.setTimeout(function() {{
    try {{
      if (window.location.href !== targetUrl) {{
        window.location.assign(targetUrl);
      }}
    }} catch (e) {{}}
  }}, 250);
}})();
"""
