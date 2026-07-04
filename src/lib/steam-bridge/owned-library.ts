/**
 * Push the user's full owned Steam library to the backend.
 *
 * Kept separate from `collection-manager.ts` (which must stay free of
 * the `@decky/api` runtime so its unit tests can import it under vitest)
 * — this module is the thin RPC edge that the pure
 * `collectSteamOwnedGameTitles` collector feeds.
 *
 * The backend's Ubisoft Steam-linked filter unions these titles in to
 * hide Ubisoft games the user owns on Steam but hasn't installed (those
 * never appear in `appmanifest`). See
 * `py_modules/unifideck/stores/ubisoft/library/steam_filter.py`.
 */
import { call } from "@decky/api";
import { rpcRoutes } from "../../api/rpc-routes";
import { collectSteamOwnedGameTitles } from "./collection-manager";

/**
 * Best effort — on failure the backend filter just degrades to the
 * installed-games-only scan.
 */
export async function uploadSteamOwnedTitles(): Promise<void> {
  try {
    const titles = collectSteamOwnedGameTitles();
    if (titles.length === 0) return;
    await call(rpcRoutes.updateSteamOwnedTitles, titles);
  } catch (e) {
    console.warn("[Unifideck] uploadSteamOwnedTitles failed", e);
  }
}
