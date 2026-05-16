import json

_XCLOUD_BROWSER_SHIMS_JS = r"""
(function() {
 'use strict';
 if (window.__unifideck_xcloud_helper) return;
 var state = {
 injectedAt: Date.now(),
 reconnects: 0,
 listenerRegistrations: 0,
 lastReason: 'init',
 };
 window.__unifideck_xcloud_helper = state;
 var XBOX_GAMEPAD_ID = 'Xbox 360 Controller (XInput STANDARD GAMEPAD)';
 var defaultChromiumVersion = '120.0.0.0';
 var proxyCache = typeof WeakMap === 'function' ? new WeakMap() : null;
 var originalGetGamepads =
 typeof navigator.getGamepads === 'function'
 ? navigator.getGamepads.bind(navigator)
 : null;
 var originalWebkitGetGamepads =
 typeof navigator.webkitGetGamepads === 'function'
 ? navigator.webkitGetGamepads.bind(navigator)
 : null;
 function getChromiumVersion() {
 try {
 var match = String(navigator.userAgent || '').match(/\s(?:Chrome|Edg)\/([\d.]+)/);
 if (match && match[1]) {
 return match[1];
 }
 } catch (e) {}
 return defaultChromiumVersion;
 }
 function spoofBrowserIdentity() {
 var chromiumVersion = getChromiumVersion();
 var edgeUserAgent =
 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' +
 'AppleWebKit/537.36 (KHTML, like Gecko) ' +
 'Chrome/' + chromiumVersion + ' Safari/537.36 Edg/' + chromiumVersion;
 state.originalUserAgent = String(navigator.userAgent || '');
 state.spoofedUserAgent = edgeUserAgent;
 try {
 if (!('orgUserAgent' in navigator)) {
 Object.defineProperty(navigator, 'orgUserAgent', {
 configurable: true,
 value: navigator.userAgent,
 });
 }
 } catch (e) {}
 try {
 Object.defineProperty(navigator, 'userAgent', {
 configurable: true,
 get: function() {
 return edgeUserAgent;
 },
 });
 } catch (e) {}
 try {
 Object.defineProperty(navigator, 'platform', {
 configurable: true,
 get: function() {
 return 'Win32';
 },
 });
 } catch (e) {}
 try {
 if ('userAgentData' in navigator && !('orgUserAgentData' in navigator)) {
 Object.defineProperty(navigator, 'orgUserAgentData', {
 configurable: true,
 value: navigator.userAgentData,
 });

 }
 if ('userAgentData' in navigator) {
 Object.defineProperty(navigator, 'userAgentData', {
 configurable: true,
 get: function() {
 return undefined;
 },
 });
 }
 } catch (e) {}
 }
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
 function copyButtons(buttons) {
 return Array.prototype.map.call(buttons || [], function(button) {
 if (!button) return button;
 return {
 pressed: !!button.pressed,
 touched: !!button.touched,
 value: typeof button.value === 'number' ? button.value : 0,
 };
 });
 }
 function serializeGamepad(gamepad) {
 return {
 axes: Array.prototype.slice.call(gamepad.axes || []),
 buttons: copyButtons(gamepad.buttons),
 connected: !!gamepad.connected,
 id: shouldSpoofGamepad(gamepad)
 ? XBOX_GAMEPAD_ID
 : String(gamepad.id || ''),
 index: typeof gamepad.index === 'number' ? gamepad.index : 0,
 mapping: shouldSpoofGamepad(gamepad) ? 'standard' : (gamepad.mapping || ''),
 timestamp:
 typeof gamepad.timestamp === 'number' ? gamepad.timestamp : Date.now(),
 };
 }
 function shouldSpoofGamepad(gamepad) {
 if (!gamepad || !gamepad.connected) {
 return false;
 }
 var id = String(gamepad.id || '');
 return !(id.indexOf('Xbox') !== -1 && gamepad.mapping === 'standard');
 }

 function normalizeGamepad(gamepad) {
 if (!shouldSpoofGamepad(gamepad)) {
 return gamepad;
 }
 if (proxyCache && proxyCache.has(gamepad)) {
 return proxyCache.get(gamepad);
 }
 var wrapped = null;
 try {
 wrapped = new Proxy(gamepad, {
 get: function(target, prop, receiver) {
 if (prop === 'id') return XBOX_GAMEPAD_ID;
 if (prop === 'mapping') return 'standard';
 if (prop === 'toJSON') {
 return function() {
 return serializeGamepad(target);
 };
 }
 return Reflect.get(target, prop, receiver);
 },
 ownKeys: function(target) {
 var keys = Reflect.ownKeys(target);
 if (keys.indexOf('id') === -1) keys.push('id');
 if (keys.indexOf('mapping') === -1) keys.push('mapping');
 return keys;
 },
 getOwnPropertyDescriptor: function(target, prop) {
 if (prop === 'id') {
 return {
 configurable: true,
 enumerable: true,
 value: XBOX_GAMEPAD_ID,
 };
 }
 if (prop === 'mapping') {
 return {
 configurable: true,
 enumerable: true,
 value: 'standard',
 };
 }
 return (
 Object.getOwnPropertyDescriptor(target, prop) || {
 configurable: true,
 enumerable: true,
 value: Reflect.get(target, prop),
 }
 );
 },
 });
 } catch (e) {
 wrapped = serializeGamepad(gamepad);
 }
 if (proxyCache) {
 proxyCache.set(gamepad, wrapped);
 }
 return wrapped;
 }
 function remapGamepadArray(gamepads) {
 var result = [];
 for (var i = 0; i < gamepads.length; i += 1) {
 result[i] = gamepads[i] ? normalizeGamepad(gamepads[i]) : null;
 }
 return result;
 }
 function overrideNavigatorMethod(name, implementation) {
 if (!name || typeof implementation !== 'function') {
 return false;
 }
 try {
 Object.defineProperty(navigator, name, {
 configurable: true,
 writable: true,
 value: implementation,
 });
 return true;
 } catch (e) {}
 try {
 var proto = Object.getPrototypeOf(navigator);
 if (proto) {
 Object.defineProperty(proto, name, {
 configurable: true,
 writable: true,

 value: implementation,
 });
 return true;
 }
 } catch (e) {}
 try {
 navigator[name] = implementation;
 return navigator[name] === implementation;
 } catch (e) {}
 return false;
 }
 function installGamepadOverride() {
 if (originalGetGamepads) {
 overrideNavigatorMethod('getGamepads', function() {
 return remapGamepadArray(originalGetGamepads() || []);
 });
 }
 if (originalWebkitGetGamepads) {
 overrideNavigatorMethod('webkitGetGamepads', function() {
 return remapGamepadArray(originalWebkitGetGamepads() || []);
 });
 }
 }
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
 function getPadSignature(gamepad) {
 if (!gamepad) return '';
 return [
 gamepad.index,
 gamepad.id,
 gamepad.mapping,
 gamepad.connected ? '1' : '0',
 ].join(':');
 }
 function getPadsSignature(gamepads) {
 return (gamepads || []).map(getPadSignature).join('|');
 }
 function makeGamepadEvent(type, gamepad) {
 try {
 return new GamepadEvent(type, { gamepad: gamepad });
 } catch (e) {
 try {
 var evt = new Event(type);
 evt.gamepad = gamepad;
 return evt;
 } catch (inner) {
 return null;
 }
 }
 }
 function dispatchGamepadEvent(type, gamepad) {
 try {
 var windowEvt = makeGamepadEvent(type, gamepad);
 windowEvt && window.dispatchEvent(windowEvt);
 } catch (e) {}
 try {
 var documentEvt = makeGamepadEvent(type, gamepad);
 documentEvt && document.dispatchEvent(documentEvt);
 } catch (e) {}
 }
 function dispatchReconnect(gamepad, reason) {
 if (!gamepad) return;
 state.reconnects += 1;
 state.lastReason = reason;
 var normalizedPad = normalizeGamepad(gamepad);
 dispatchGamepadEvent('gamepaddisconnected', normalizedPad);
 dispatchGamepadEvent('gamepadconnected', normalizedPad);
 }
 function resyncGamepads(reason) {
 safeFocus();
 var pads = getConnectedGamepads();

 state.lastResyncAt = Date.now();
 state.lastCount = pads.length;
 state.lastPadIds = pads.map(function(pad) { return pad.id; });
 state.lastSignature = getPadsSignature(pads);
 for (var i = 0; i < pads.length; i += 1) {
 dispatchReconnect(pads[i], reason);
 }
 }
 function periodicScan() {
 var pads = getConnectedGamepads();
 var signature = getPadsSignature(pads);
 state.lastCount = pads.length;
 state.lastPadIds = pads.map(function(pad) { return pad.id; });
 if (signature && signature !== state.lastSignature) {
 resyncGamepads('periodic-change');
 return;
 }
 if (
 pads.length &&
 (!state.lastResyncAt || Date.now() - state.lastResyncAt >= 5000)
 ) {
 resyncGamepads('periodic-refresh');
 }
 }
 function patchListenerRegistration(target) {
 if (!target || typeof target.addEventListener !== 'function') {
 return;
 }
 var originalAddEventListener = target.addEventListener.bind(target);
 target.addEventListener = function(type, listener, options) {
 var result = originalAddEventListener(type, listener, options);
 if (type === 'gamepadconnected' || type === 'gamepaddisconnected') {
 state.listenerRegistrations += 1;
 window.setTimeout(function() {
 resyncGamepads('listener-' + type);
 }, 0);
 }
 return result;
 };
 }
 patchListenerRegistration(window);
 patchListenerRegistration(document);
 spoofBrowserIdentity();
 window.addEventListener('focus', function() {
 window.setTimeout(function() { resyncGamepads('focus'); }, 50);
 });
 document.addEventListener('visibilitychange', function() {
 if (!document.hidden) {
 window.setTimeout(function() { resyncGamepads('visibility'); }, 50);
 }
 });
 installGamepadOverride();
 window.setTimeout(function() { resyncGamepads('startup-1s'); }, 1000);
 window.setTimeout(function() { resyncGamepads('startup-3s'); }, 3000);
 window.setInterval(periodicScan, 1000);
})();
"""
def get_xcloud_browser_shims_js() -> str:
    """Get xcloud browser shims js."""
    return _XCLOUD_BROWSER_SHIMS_JS
def get_xcloud_navigation_js(target_url: str) -> str:
    """Get xcloud navigation js."""
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
