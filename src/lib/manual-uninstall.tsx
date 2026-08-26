/**
 * manual-uninstall — post-uninstall cleanup for manual-store games.
 *
 * A manual game's shortcut is deleted outright on uninstall (the game
 * no longer exists in any library, so a "Not Installed" tile would only
 * offer an Install button that cannot work). The backend drops it from
 * shortcuts.vdf; this module completes the job in the LIVE Steam
 * session so nothing ghostly survives until the next restart:
 *
 *  1. `SteamClient.Apps.RemoveShortcut` — the tile vanishes from the
 *     library and Recents immediately.
 *  2. If the user is sitting on the game's own detail page (or the
 *     current route cannot be read), navigate to the library home —
 *     a detail page whose app just disappeared from the session is a
 *     dead end with a broken Install button.
 *  3. Only if the live removal FAILS is the Steam-restart prompt
 *     shown; "absent" means Steam never had the shortcut this session,
 *     so there was no tile to clear.
 *
 * Called from `useGameActions.uninstall`, the single choke point every
 * uninstall surface goes through (Downloads tab row, the detail page's
 * InstalledButtons, GameInfoCompatRow) — fixing it per call site is
 * how the detail-page path got missed the first time.
 */
import { Navigation, Router, showModal } from "@decky/ui";
import { unifideckGameCache } from "./library-filters";
import { removeShortcutFromSession } from "./steam-bridge/shortcut-types";
import { SteamRestartModal } from "../components/modals/SteamRestartModal";

/** Steam's gamepad-UI router keeps its react-router history on the
 *  (untyped) `m_history` field — the established way plugins read the
 *  current route. Absent fields just make the route unreadable. */
interface RouteHolder {
  m_history?: { location?: { pathname?: string } };
}

function currentGamepadRoute(): string | null {
  try {
    const instance = Router.WindowStore?.GamepadUIMainWindowInstance as
      | RouteHolder
      | undefined;
    const path = instance?.m_history?.location?.pathname;
    return typeof path === "string" ? path : null;
  } catch {
    return null;
  }
}

/**
 * Finish a manual game's uninstall in the live Steam session.
 * No-op for every other store.
 */
export function finalizeManualUninstall(appId: number): void {
  if (unifideckGameCache.get(appId)?.store !== "manual") return;

  const outcome = removeShortcutFromSession(appId);
  if (outcome === "failed") {
    showModal(<SteamRestartModal />);
    return;
  }
  if (outcome !== "removed") return;

  const unsigned = appId < 0 ? appId + 0x100000000 : appId;
  const route = currentGamepadRoute();
  // Navigate away when the user is on the dead app's page — and also
  // when the route cannot be read, because a stray jump to the library
  // is benign while a dead detail page is the exact reported bug.
  if (route === null || route.startsWith(`/library/app/${unsigned}`)) {
    try {
      Navigation.Navigate("/library/home");
    } catch (e) {
      console.error("[ManualUninstall] navigation failed:", e);
    }
  }
}
