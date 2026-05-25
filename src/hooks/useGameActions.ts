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
import type { Result, StoreId } from "../types/api";

/** Steam bridge shape. */
interface SteamBridgeShape {
  runGame(appId: string, launchOptions: string): void;
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
  uninstall: (appId: number) => Promise<Result | null>;
  cancel: (downloadId: string) => Promise<Result | null>;
  launch: (appId: number, launchOptions: string) => void;
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
  const [isWorking, setWorking] = useState(false);

  const install = useCallback(
    async (
      store: StoreId, gameId: string,
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
    async (appId: number) => {
      setWorking(true);
      try {
        const result = await downloads.uninstallGame(appId);
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
    (appId: number, launchOptions: string) => {
      bridge.runGame(String(appId), launchOptions);
    },
    [bridge],
  );

  const terminate = useCallback(
    (appId: number, force: boolean = false) => {
      bridge.terminateApp(String(appId), force);
    },
    [bridge],
  );

  return { isWorking, install, uninstall, cancel, launch, terminate };
}
