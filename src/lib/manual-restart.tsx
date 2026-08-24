/**
 * manual-restart — offer the Steam restart when a manual game's tile
 * is not live yet.
 *
 * A manual game's shortcut is written to shortcuts.vdf the moment the
 * flow starts, but Steam only reads that file at startup — so until a
 * restart the tile simply does not exist in the session. Every exit
 * point of the flow (exe chosen, "Later", the import verification run
 * ending) funnels through here: if Steam already has the shortcut in
 * its in-memory app store (i.e. a restart already happened), stay
 * quiet; otherwise show the restart prompt so the user knows how to
 * make the tile appear.
 */
import { call } from "@decky/api";
import { showModal } from "@decky/ui";
import { rpcRoutes } from "../api/rpc-routes";
import { resolveAppIdFromStoreGame } from "./library-filters";
import { isShortcutRegistered } from "./steam-bridge/wrapper-shortcut-launch";
import { SteamRestartModal } from "../components/modals/SteamRestartModal";

/** True when the manual game's persistent shortcut is live in Steam's
 *  in-memory app store (i.e. a restart already picked it up). */
export function isManualTileLive(gameId: string): boolean {
  const appId = resolveAppIdFromStoreGame("manual", gameId);
  if (appId == null) return false;
  const unsigned = appId < 0 ? appId + 0x100000000 : appId;
  return isShortcutRegistered(unsigned);
}

export function offerRestartIfTileMissing(gameId: string): void {
  if (isManualTileLive(gameId)) return;
  showModal(<SteamRestartModal />);
}

/**
 * Re-write the game's persistent shortcut on the backend.
 *
 * Steam flushes its own copy of shortcuts.vdf whenever the temp-shortcut
 * dance calls AddShortcut/RemoveShortcut — erasing our row. Called after
 * the dance is over so the row lands AFTER the last flush and survives
 * the next restart. Best-effort: a failure only costs the tile until the
 * next sync's reconcile re-adds it.
 */
export async function ensureManualShortcut(gameId: string): Promise<void> {
  try {
    await call<[string], unknown>(rpcRoutes.manualEnsureShortcut, gameId);
  } catch (e) {
    console.error("[ManualRestart] ensure shortcut failed:", e);
  }
}
