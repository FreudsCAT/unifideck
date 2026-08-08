/**
 * useGameActions — install / uninstall / launch / cancel.
 *
 * Wraps `DownloadContext` plus `SteamBridge.runGame` /
 * `terminateApp`, exposing a clean API for any component
 * that needs to trigger a game-state action :
 *  - `install(store, gameId, options?)`
 *  - `uninstall(appId)`
 *  - `cancel(downloadId)`
 *  - `launch(appId, storeGameId)`
 *  - `terminate(appId, force?)`
 *
 * The hook tracks an `isWorking` flag while any one of these
 * is in flight, useful for disabling buttons or showing a
 * spinner without each caller managing its own loading state.
 */
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDownloads } from "../contexts/DownloadContext";
import { useToast } from "./useToast";
import { invalidateGameInfo } from "./useGameInfo";
import { bumpGameStateVersion } from "../lib/game-state-version";
import { captureForceCompatPin } from "../utils/protonPin";
import type { Result, StoreId } from "../types/api";

/** Steam bridge shape. */
interface SteamBridgeShape {
  runGame(appId: number): void;
  terminateApp(appId: string, force?: boolean): void;
}

/**
 * Memoised action callbacks returned by {@link useGameActions}.
 * Identity-stable across renders so they can be passed to
 * memoised children without breaking memoisation.
 */
export interface UseGameActionsResult {
  isWorking: boolean;
  install: (
    store: StoreId,
    gameId: string,
    options?: { storage?: string; language?: string; title?: string },
  ) => Promise<Result | null>;
  uninstall: (appId: number, deletePrefix?: boolean) => Promise<Result | null>;
  cancel: (downloadId: string) => Promise<Result | null>;
  /** `storeGameId` is the `"<store>:<game_id>"` key. It is required,
   *  not optional, so `tsc` fails any new call site that forgets it —
   *  a launch without the Force-Compat capture silently does nothing
   *  at all when a Proton is forced (see {@link captureForceCompatPin}),
   *  which is exactly how the downloads-tab Play button shipped broken. */
  launch: (appId: number, storeGameId: string) => Promise<void>;
  terminate: (appId: number, force?: boolean) => void;
}

/**
 * Hook bundling all game-level actions a UI element
 * may trigger : install, uninstall, launch, cancel
 * download, force update. Each callback returns a
 * promise so callers can await the backend response
 * before showing a confirmation toast.
 *
 * @param game — the Game the actions apply to.
 * @returns set of memoised action callbacks.
 */
export function useGameActions(bridge: SteamBridgeShape): UseGameActionsResult {
  const downloads = useDownloads();
  const { t } = useTranslation();
  const toast = useToast();
  const [isWorking, setWorking] = useState(false);

  const install = useCallback(
    async (
      store: StoreId,
      gameId: string,
      options?: { storage?: string; language?: string; title?: string },
    ) => {
      setWorking(true);
      try {
        return await downloads.installGame(store, gameId, options);
      } finally {
        setWorking(false);
      }
    },
    [downloads],
  );

  const uninstall = useCallback(
    async (appId: number, deletePrefix = false) => {
      setWorking(true);
      try {
        const result = await downloads.uninstallGame(appId, deletePrefix);
        if (result?.success) {
          invalidateGameInfo(appId);
          bumpGameStateVersion(appId);
        }
        return result;
      } finally {
        setWorking(false);
      }
    },
    [downloads],
  );

  const cancel = useCallback(
    async (downloadId: string) => {
      setWorking(true);
      try {
        return await downloads.cancelDownload(downloadId);
      } finally {
        setWorking(false);
      }
    },
    [downloads],
  );

  // The capture lives here rather than in the buttons so every launch
  // path gets it. Steam wraps the launcher in Wine when a Proton is
  // forced on the shortcut, so a RunGame without this does nothing —
  // no log, no toast, no game.
  const launch = useCallback(
    async (appId: number, storeGameId: string) => {
      const outcome = await captureForceCompatPin(storeGameId);
      if (outcome.pinned) {
        // Steam's dialog will now show no forced tool, so say where the
        // choice went — otherwise it looks like the setting was discarded.
        toast.info(
          t("play.protonPinned", { version: outcome.pinned }),
          t("play.protonPinnedBody"),
        );
      }
      bridge.runGame(appId);
    },
    [bridge, t, toast],
  );

  const terminate = useCallback(
    (appId: number, force: boolean = false) => {
      bridge.terminateApp(String(appId), force);
    },
    [bridge],
  );

  return { isWorking, install, uninstall, cancel, launch, terminate };
}
