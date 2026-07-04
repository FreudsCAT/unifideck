/**
 * Reuse Steam's native app context menu (Add to Favorites / Add to /
 * Remove from / Manage / Developer / Properties) — the popup the native
 * "Manage" gear opens — for our own custom settings button.
 *
 * We hide the native Play section with CSS, but it stays mounted in the
 * DOM with its handlers fully wired. Rather than reconstruct the menu
 * from minified internals (the menu component / showContextMenu export
 * names rotate per Steam build), we locate the native "Manage" button
 * inside that hidden section and invoke its React `onClick` verbatim,
 * passing OUR button as the menu anchor (`currentTarget`). The native
 * handler only reads `currentTarget` for positioning; the menu's items
 * act on the app bound in the handler's closure (the current page's
 * app), so it works for non-Steam shortcuts too.
 *
 * Verified live: the native handler is `i => { if (BKioskModeLocked())…
 * showContextMenu(<AppContextMenu {...}/>, i.currentTarget, opts) }`.
 */

type ClickHandler = (e: { currentTarget: EventTarget | null }) => void;

/**
 * Identify the native "Manage" (menu) button among a Play section's
 * buttons by stable, readable markers in its (minified) onClick — the
 * kiosk-lock guard and the menu overlap option. Avoids relying on the
 * localized "Manage" aria-label or on child ordering.
 */
function isManageHandler(fn: unknown): fn is ClickHandler {
  if (typeof fn !== "function") return false;
  const src = fn.toString();
  return src.includes("BKioskModeLocked") || src.includes("bOverlapHorizontal");
}

/** Read a DOM node's React `onClick` if it's the native menu handler. */
function nativeMenuOnClick(el: Element): ClickHandler | undefined {
  const key = Object.keys(el).find((k) => k.startsWith("__reactProps$"));
  if (!key) return undefined;
  const props = (el as unknown as Record<string, { onClick?: unknown }>)[key];
  return isManageHandler(props?.onClick)
    ? (props!.onClick as ClickHandler)
    : undefined;
}

/**
 * Open Steam's native app "Manage" context menu anchored to `sourceEl`.
 * Returns `false` if the native button can't be located so the caller
 * can fall back (e.g. to opening Properties directly).
 */
export function openNativeAppManageMenu(sourceEl: HTMLElement | null): boolean {
  if (!sourceEl) return false;
  // CRITICAL: query `sourceEl.ownerDocument`, NOT the global `document`.
  // The plugin runs in SharedJSContext, whose `document` is a *different*
  // CEF document than the Gaming Mode window where the UI actually renders
  // — so global `document.querySelectorAll` finds nothing. `sourceEl` (our
  // clicked gear) lives in the right document, and the hidden-but-mounted
  // native Play section is in that same document.
  //
  // We scan document-wide (not scoping to the Play-section class) and match
  // the native "Manage" button by its handler signature — that uniquely
  // identifies the one Manage button on the page (verified: 1 match).
  const doc = sourceEl.ownerDocument ?? document;
  for (const btn of Array.from(
    doc.querySelectorAll("button, [role='button'], [tabindex]"),
  )) {
    const onClick = nativeMenuOnClick(btn);
    if (onClick) {
      onClick({ currentTarget: sourceEl });
      return true;
    }
  }
  return false;
}
