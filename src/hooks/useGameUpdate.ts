/**
 * useGameUpdate — "does this game have a pending store update?"
 *
 * Thin reactive read over {@link UpdateStore}. Every surface that offers
 * an Update affordance goes through this so they can never disagree: the
 * App-Details Play section, the QAM Downloads tab's Installed rows, and
 * the pre-launch warning.
 *
 * Cheap by construction — the answer is already in memory (the backend
 * sweep put it there), so this never triggers a store round-trip and
 * never blocks a render.
 */
import { useSyncExternalStore } from "react";
import { UpdateStore } from "../stores/update-store";

/**
 * @param store — store id (`"epic"`, `"gog"`, …).
 * @param gameId — the store-native game id (`store_game_id`), NOT the
 *   Steam shortcut appid. Epic's Rocket League is `"Sugar"` here.
 * @returns true when the last sweep reported this game as out of date.
 */
export function useGameUpdate(
  store: string | undefined,
  gameId: string | undefined,
): boolean {
  const map = useSyncExternalStore(
    UpdateStore.subscribe,
    UpdateStore.getSnapshot,
  );
  if (!store || !gameId) return false;
  return (map[store] ?? []).includes(gameId);
}
