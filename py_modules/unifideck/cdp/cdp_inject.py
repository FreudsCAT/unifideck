"""Steam UI CSS / DOM mutation via the Chrome DevTools Protocol.

Two surfaces:

* ``SteamCSSInjector.inject_css`` / ``remove_css`` — generic
  ``<style>`` tag management for arbitrary CSS rules. Used by
  layout / theming helpers.
* ``SteamCSSInjector.hide_play_section`` / ``show_play_section``
  — JS-driven DOM mutation of Steam's action-bar PlaySection.
  The JS body is a verbatim port of the working staging
  implementation: each invocation is stateless, walks every
  visible button, matches its text against a multi-locale
  action-verb regex, walks up parents to a sensible container,
  and tags it with ``data-unifideck-hidden-native``.

  Stateless on purpose — the frontend re-invokes hide on a
  short burst (0/50/150/300/600 ms) plus a 2 s persistent poll
  so Steam's SPA re-renders that re-create the action bar are
  caught without us holding any namespace state inside the
  renderer (which can be wiped by tab reloads).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cdp_client import CDPClient

logger = logging.getLogger(__name__)

STEAM_TAB_URL_MARKER = "steamloopback.host"
STYLE_ID_PREFIX = "unifideck-style-"


def is_steam_ui_tab(page: dict[str, Any]) -> bool:
    """Check whether the CDP page handle is Steam's UI tab."""
    if not isinstance(page, dict):
        return False  # type: ignore[unreachable]
    url = page.get("url", "")
    return STEAM_TAB_URL_MARKER in url


def escape_css_for_template_literal(css: str) -> str:
    """Escape a CSS string for use inside a JS template literal."""
    return (
        css.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )


def build_marker_id(name: str) -> str:
    """Build a DOM id for an injected ``<style>`` tag."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{STYLE_ID_PREFIX}{safe}"


# Multi-locale action-verb regex covering Steam's "Play / Install
# / Update / Resume / Download / Pause / Cancel / Stream / Preload"
# strings in 90+ locales. Verbatim port from the working staging
# implementation — do not edit individual verbs without updating
# the full set; Steam's locale strings rotate per release and the
# tested coverage matters more than ordering.
_PLAY_BUTTON_TEXT_REGEX = (
    "Téléchargement en cours|Wird heruntergeladen …|Предварително сваляне|"
    "Завчасно завантажити|Nạp trước nội dung|Download bezig \\.\\.\\.|"
    "Alvast downloaden|Download in corso|Pobierz wstępnie|Mettre en pause|"
    "Đang tải xuống|Poner en pausa|กำลังดาวน์โหลด|التحميل المسبق|Транслировать|"
    "Aktualisieren|Предзагрузить|Herunterladen|Възобновяване|Mettre à jour|"
    "Förinstallera|Приостановить|Forhåndslast|Pré\\-carregar|Předstáhnout|"
    "Γίνεται λήψη|Nainstalovat|หยุดชั่วคราว|Installieren|Retransmitir|A transferir|"
    "โหลดล่วงหน้า|Actualizează|ดำเนินการต่อ|Aktualizovat|Завантаження|Télécharger|"
    "Завантажити|Pre\\-scarica|Zaktualizuj|Інсталювати|Descargando|Εγκατάσταση|"
    "جار التنزيل|Downloading|Se descarcă|Возобновить|Forudindlæs|İndiriliyor|"
    "Installeren|Vorausladen|Призупинити|Инсталиране|Strumieniuj|Devam ettir|"
    "Zainstaluj|Pokračovat|Preîncarcă|Instalează|إيقاف مؤقت|Downloader|Transferir|"
    "Установить|Трансляція|Installera|Pozastavit|Продолжить|Actualizar|Laster ned|"
    "Downloaden|Обновяване|Transmitir|Streamovat|Précharger|Pobieranie|Fortsetzen|"
    "Laddar ned|Továbbítás|Продовжити|Προφόρτωση|Atualizar|Întrerupe|Stahování|"
    "Előtöltés|Transmite|Bijwerken|Återuppta|Uppdatera|Trasmetti|Mengunduh|"
    "Излъчване|Reprendre|Επαναφορά|Ενημέρωση|Wstrzymaj|Загрузить|Folytatás|"
    "ดาวน์โหลด|Gjenoppta|Ladda ned|Lanjutkan|Descargar|Hervatten|Installer|"
    "Frissítés|Telepítés|Pausieren|Phát sóng|Precargar|Installa|Baixando|"
    "Обновить|Riprendi|Last ned|Streamen|Perbarui|Ön Yükle|Streamer|Esilataa|"
    "Duraklat|Güncelle|Tạm dừng|Ladataan|Tiếp tục|Μετάδοση|Cập nhật|Striimaa|"
    "Stáhnout|Εκκίνηση|Download|Keskeytä|Oppdater|Devam Et|Genoptag|Letöltés|"
    "Descarcă|Continuă|Aggiorna|Instalar|Загрузка|Pauzeren|Reanudar|Sospendi|"
    "Оновити|Scarica|Retomar|ダウンロード中|استئناف|Pobierz|ストリーミング|Opdater|"
    "Preload|Yayınla|Streama|ติดตั้ง|Пускане|Spielen|Install|Сваляне|Mainkan|"
    "Cài đặt|Pramuat|Päivitä|Szünet|Pausar|インストール|ダウンロード|Spelen|Update|"
    "อัปเดต|Baixar|다운로드 중|Stream|アップデート|Instal|Resume|Asenna|Играть|"
    "Tải về|Yükle|Pausa|미리 받기|Jugar|Spill|Παύση|Pelaa|Joacă|Lataa|تحديث|"
    "Wznów|Játék|プリロード|Jatka|Gioca|Jouer|تنزيل|Пауза|Jogar|สตรีม|Spela|"
    "تثبيت|Pause|Unduh|İndir|Грати|일시 정지|تشغيل|Strøm|正在下载|Λήψη|一時停止|"
    "스트리밍|Oyna|开始游戏|Hrát|Graj|다운로드|Chơi|Play|البث|開始遊戲|流式传输|Reia|"
    "계속하기|เล่น|업데이트|Jeda|Spil|플레이|プレイ|下載中|更新|설치|继续|下載|"
    "預載|预载|串流|暂停|安裝|安装|繼續|暫停|再開|下载"
)


def _hide_play_js(app_id: int) -> str:
    """Build the stateless one-shot hide JS for ``app_id``.

    Verbatim port of staging's ``hide_native_play_section`` JS,
    with one robustness tweak: action-verb matching strips
    leading/trailing non-letter characters before the regex so
    buttons with embedded icon glyphs (e.g. "▶ Play",
    "↓INSTALL") still match. Without this, recent Steam UI
    builds that inline the icon as a text character (rather
    than an SVG) silently return ``"not_found"``.

    Returns ``"hidden"`` / ``"not_found"`` / ``"too_large"`` so
    Python can log the outcome.
    """
    app_id_str = str(app_id)
    return (
        '(function() {\n'
        '    var appId = "' + app_id_str + '";\n'
        '    var buttons = document.querySelectorAll(\'button, [class*="Focusable"]\');\n'
        '    var playBtn = null;\n'
        '    var candidateCount = 0;\n'
        '    for (var i = 0; i < buttons.length; i++) {\n'
        '        var btn = buttons[i];\n'
        '        // Skip buttons inside our wrapper, already-hidden, or modals.\n'
        '        var parent = btn;\n'
        '        var skip = false;\n'
        '        while (parent) {\n'
        '            if (parent.getAttribute) {\n'
        '                if (parent.getAttribute("data-unifideck-play-wrapper") === "true"\n'
        '                    || parent.getAttribute("data-unifideck-hidden-native")\n'
        '                    || parent.getAttribute("role") === "dialog") {\n'
        '                    skip = true;\n'
        '                    break;\n'
        '                }\n'
        '            }\n'
        '            parent = parent.parentElement;\n'
        '        }\n'
        '        if (skip) continue;\n'
        '        \n'
        '        // Token-based match: split the button text on any non-\n'
        '        // letter sequence (\\p{L} covers Latin/CJK/Arabic/etc.)\n'
        '        // and test each token against the action-verb regex.\n'
        '        // This handles all the real-world button shapes Steam\n'
        '        // emits without relying on a strict "==" comparison:\n'
        '        //   "▶ Play"            -> ["Play"]\n'
        '        //   "↓INSTALL"          -> ["INSTALL"]\n'
        '        //   "Play (5.2 GB)"     -> ["Play", "GB"]\n'
        '        //   "INSTALL SPACE …"   -> ["INSTALL", "SPACE", ...]\n'
        '        var txt = btn.textContent.trim();\n'
        '        var tokens = txt.split(/[^\\p{L}]+/u).filter(function(t) { return t.length > 0; });\n'
        '        var verbRe = /^(' + _PLAY_BUTTON_TEXT_REGEX + ')$/i;\n'
        '        var matched = false;\n'
        '        for (var ti = 0; ti < tokens.length; ti++) {\n'
        '            if (verbRe.test(tokens[ti])) { matched = true; break; }\n'
        '        }\n'
        '        if (matched) {\n'
        '            candidateCount++;\n'
        '            var rect = btn.getBoundingClientRect();\n'
        '            // Size gate keeps us from grabbing the small "Install"\n'
        '            // *tab* (next to Details/Synopsis) — primary action\n'
        '            // buttons in the BPM action bar are always wider.\n'
        '            if (rect.width > 100 && rect.height > 30) {\n'
        '                playBtn = btn;\n'
        '                break;\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '    if (!playBtn) {\n'
        '        console.log("[Unifideck CDP] No visible native play button found for app " + appId + " (scanned " + buttons.length + " buttons, " + candidateCount + " text-matched but size-filtered)");\n'
        '        return "not_found";\n'
        '    }\n'
        '    // Walk up to find the full action-bar container without\n'
        '    // engulfing our injected wrapper or the entire viewport.\n'
        '    var viewportH = window.innerHeight || document.documentElement.clientHeight || 720;\n'
        '    var maxHeight = Math.max(220, viewportH * 0.5);\n'
        '    var node = playBtn.parentElement;\n'
        '    if (!node) {\n'
        '        console.warn("[Unifideck CDP] No parent for play button of app " + appId);\n'
        '        return "not_found";\n'
        '    }\n'
        '    var container = node;\n'
        '    var depth = 0;\n'
        '    while (node && node.parentElement && depth < 10) {\n'
        '        var p = node.parentElement;\n'
        '        if (p.querySelector && p.querySelector(\'[data-unifideck-play-wrapper="true"]\')) {\n'
        '            break;\n'
        '        }\n'
        '        var pr = p.getBoundingClientRect();\n'
        '        if (pr.width <= 0 || pr.height <= 0) {\n'
        '            node = p; depth++; continue;\n'
        '        }\n'
        '        if (pr.height > maxHeight) break;\n'
        '        container = p; node = p; depth++;\n'
        '    }\n'
        '    var cRect = container.getBoundingClientRect();\n'
        '    if (cRect.height > maxHeight) {\n'
        '        console.warn("[Unifideck CDP] Refusing oversized container (" + Math.round(cRect.height) + "px) for app " + appId);\n'
        '        return "too_large";\n'
        '    }\n'
        '    container.setAttribute("data-unifideck-hidden-native", appId);\n'
        '    container.style.setProperty("display", "none", "important");\n'
        '    container.style.setProperty("visibility", "hidden", "important");\n'
        '    container.style.setProperty("pointer-events", "none", "important");\n'
        '    console.log("[Unifideck CDP] Hidden native play section for app " + appId + " (container " + Math.round(cRect.height) + "px, depth " + depth + ")");\n'
        '    return "hidden";\n'
        '})()'
    )


def _show_play_js(app_id: int) -> str:
    """Build the stateless unhide JS for ``app_id``."""
    app_id_str = str(app_id)
    return (
        '(function() {\n'
        '    var appId = "' + app_id_str + '";\n'
        '    var el = document.querySelector(\'[data-unifideck-hidden-native="\' + appId + \'"]\');\n'
        '    if (el) {\n'
        '        el.style.removeProperty("display");\n'
        '        el.style.removeProperty("visibility");\n'
        '        el.style.removeProperty("pointer-events");\n'
        '        el.removeAttribute("data-unifideck-hidden-native");\n'
        '        console.log("[Unifideck CDP] Unhidden native play section for app " + appId);\n'
        '        return true;\n'
        '    }\n'
        '    return false;\n'
        '})()'
    )


class SteamCSSInjector:
    """CDP-mediated DOM / CSS mutation for the Steam UI tab."""

    def __init__(self, cdp_client: CDPClient) -> None:
        """Initialize the instance."""
        self._cdp = cdp_client

    async def connect_to_steam(self) -> bool:
        """Connect to the Steam UI page over CDP."""
        try:
            return await self._cdp.connect(STEAM_TAB_URL_MARKER)
        except Exception as e:
            logger.warning("[cdp_inject] connect failed: %s", e)
            return False

    async def inject_css(self, css: str, marker: str) -> bool:
        """Inject (or update in place) a ``<style>`` tag keyed by ``marker``."""
        marker_id = build_marker_id(marker)
        escaped = escape_css_for_template_literal(css)
        js = f"""
        (() => {{
            const id = "{marker_id}";
            let el = document.getElementById(id);
            if (!el) {{
                el = document.createElement("style");
                el.id = id;
                document.head.appendChild(el);
            }}
            el.textContent = `{escaped}`;
            return true;
        }})()
        """
        try:
            return bool(await self._cdp.eval_js(js))
        except Exception as e:
            logger.warning("[cdp_inject] eval failed for %s: %s", marker, e)
            return False

    async def remove_css(self, marker: str) -> bool:
        """Remove a previously-injected ``<style>`` tag."""
        marker_id = build_marker_id(marker)
        js = f"""
        (() => {{
            const el = document.getElementById("{marker_id}");
            if (el) {{ el.remove(); return true; }}
            return false;
        }})()
        """
        try:
            return bool(await self._cdp.eval_js(js))
        except Exception as e:
            logger.debug("[cdp_inject] remove failed for %s: %s", marker, e)
            return False

    async def hide_play_section(self, app_id: int) -> Any:
        """Hide Steam's native PlaySection for ``app_id``.

        Stateless one-shot — caller (frontend hook) re-invokes
        on a short burst + 2 s persistent poll to catch React
        re-renders that re-create the action bar.
        """
        try:
            result = await self._cdp.eval_js(_hide_play_js(app_id))
            # _send() returns None when the WS isn't connected — surface
            # that as an explicit failure so the silent-success regression
            # we hit on for-pr-0.7 can't recur unnoticed.
            if result is None:
                logger.warning(
                    "[cdp_inject] hide_play_section(%d) got no response — "
                    "CDP not connected",
                    app_id,
                )
                return {"ok": False, "error": "cdp_not_connected"}
            if result == "not_found":
                logger.debug("[cdp_inject] hide_play_section(%d) => 'not_found'", app_id)
            else:
                logger.info("[cdp_inject] hide_play_section(%d) => %r", app_id, result)
            # Returns "hidden" / "not_found" / "too_large"; expose
            # raw value so the frontend can decide whether to back off.
            return {"ok": True, "outcome": result}
        except Exception as e:
            logger.warning("[cdp_inject] hide_play_section(%d) failed: %s", app_id, e)
            return {"ok": False, "error": str(e)}

    async def show_play_section(self, app_id: int) -> Any:
        """Restore Steam's native PlaySection for ``app_id``."""
        try:
            result = await self._cdp.eval_js(_show_play_js(app_id))
            if result is None:
                logger.debug(
                    "[cdp_inject] show_play_section(%d) got no response — "
                    "CDP not connected",
                    app_id,
                )
                return {"ok": False, "error": "cdp_not_connected"}
            return {"ok": True, "restored": bool(result)}
        except Exception as e:
            logger.debug("[cdp_inject] show_play_section(%d) failed: %s", app_id, e)
            return {"ok": False, "error": str(e)}


_singleton_injector: SteamCSSInjector | None = None
_CDP_CONNECT_TIMEOUT_S = 5.0


async def _build_connected_injector() -> SteamCSSInjector | None:
    """Construct a fresh ``SteamCSSInjector`` and connect it.

    Returns ``None`` if the connect fails so the caller can avoid
    caching a dead singleton.
    """
    from .cdp_client import CDPClient
    injector = SteamCSSInjector(CDPClient())
    try:
        ok = await asyncio.wait_for(
            injector.connect_to_steam(),
            timeout=_CDP_CONNECT_TIMEOUT_S,
        )
    except (TimeoutError, Exception) as e:
        logger.warning("[cdp_inject] connect to Steam UI tab failed: %s", e)
        return None
    if not ok:
        logger.warning(
            "[cdp_inject] connect to Steam UI tab returned False "
            "(no '%s' target found?)",
            STEAM_TAB_URL_MARKER,
        )
        return None
    logger.info(
        "[cdp_inject] connected to Steam UI tab (marker=%r)",
        STEAM_TAB_URL_MARKER,
    )
    return injector


async def get_cdp_client() -> SteamCSSInjector | None:
    """Return the process-wide ``SteamCSSInjector`` singleton.

    Constructs and connects on first call. If the cached singleton's
    WebSocket has dropped (Steam restart, CEF reload), reconnects from
    scratch. Returns ``None`` if connect fails so RPC handlers can
    surface a clean error instead of caching a dead client.
    """
    global _singleton_injector
    if _singleton_injector is None:
        _singleton_injector = await _build_connected_injector()
        return _singleton_injector
    if not _singleton_injector._cdp.connected:
        logger.debug("[cdp_inject] singleton present but disconnected; reconnecting")
        await _singleton_injector._cdp.disconnect()
        _singleton_injector = await _build_connected_injector()
    return _singleton_injector


async def shutdown_cdp_client() -> None:
    """Drop the singleton (called on plugin unload)."""
    global _singleton_injector
    if _singleton_injector is not None:
        try:
            await _singleton_injector._cdp.disconnect()
        except Exception as e:
            logger.debug("[cdp_inject] disconnect on shutdown skipped: %s", e)
    _singleton_injector = None
