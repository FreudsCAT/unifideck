/**
 * useGameActions — install / uninstall / launch / cancel.
 *
 * Wraps `DownloadContext` plus `SteamBridge.runGame` /
 * `terminateApp`, exposing a clean API for any component
 * that needs to trigger a game-state action :
 *  - `install(store, gameId, options?)`
 *  - `uninstall(appId)`
 *  - `cancel(downloadId)`
 *  - `launch(appId, launchOptions)`
 *  - `terminate(appId, force?)`
 *
 * The hook tracks an `isWorking` flag while any one of these
 * is in flight, useful for disabling buttons or showing a
 * spinner without each caller managing its own loading state.
 */
import { useCallback, useState } from "react";
import { useDownloads } from "../contexts/DownloadContext";
import { invalidateGameInfo } from "./useGameInfo";
import { bumpGameStateVersion } from "../lib/game-state-version";
import { UpdateStore } from "../stores/update-store";
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
  launch: (appId: number) => void;
  terminate: (appId: number, force?: boolean) => void;
  /** Queue an update. Pass `store`/`gameId` so the Update affordance can
   *  clear immediately instead of waiting for the next backend sweep. */
  update: (
    appId: number,
    store?: string,
    gameId?: string,
  ) => Promise<Result | null>;
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

  const launch = useCallback(
    (appId: number) => {
      bridge.runGame(appId);
    },
    [bridge],
  );

  const terminate = useCallback(
    (appId: number, force: boolean = false) => {
      bridge.terminateApp(String(appId), force);
    },
    [bridge],
  );

  const update = useCallback(
    async (appId: number, store?: string, gameId?: string) => {
      setWorking(true);
      try {
        const result = await downloads.updateGame(appId);
        if (result?.success && store && gameId) {
          // Drop it locally straight away. The backend invalidates its
          // own scan cache, but the re-scan is asynchronous — without
          // this the button would keep saying Update until the next
          // sweep landed, several hours later.
          UpdateStore.clearGame(store, gameId);
        }
        return result;
      } finally {
        setWorking(false);
      }
    },
    [downloads],
  );

  return { isWorking, install, uninstall, cancel, launch, terminate, update };
}
