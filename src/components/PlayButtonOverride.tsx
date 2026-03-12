/**
 * PlaySectionWrapper Component
 *
 * For non-Steam Unifideck games, this component:
 *   - Installed & not downloading: Renders hidden anchor (native PlaySection visible)
 *   - Uninstalled: Hides native PlaySection via CSS, renders custom Install button
 *   - Downloading: Hides native PlaySection via CSS, renders Cancel button with progress
 *
 * Native PlaySection hiding strategy (CSSLoader-aligned):
 *   Style injection is DECOUPLED from React component lifecycle.
 *   Module-level functions inject/remove a <style> tag in document.head.
 *   The patcher calls injectHidePlaySection() synchronously before React reconciles.
 *   The component only calls removeHidePlaySection() when it confirms the game IS installed.
 *   Plugin onDismount calls removeAllHidePlaySectionStyles() for cleanup.
 *
 * Click handling triggers Steam's game action flow, which the interceptor catches.
 */

import React, { FC, useState, useEffect, useRef } from "react";
import { call, toaster } from "@decky/api";
import { DialogButton, Focusable, showModal, ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { updateSingleGameStatus } from "../tabs";
import { setDownloadStateRef as setInterceptorDownloadState } from "../hooks/gameActionInterceptor";
import { GOGLanguageSelectModal } from "./GOGLanguageSelectModal";
import {
  getShortcutRunGameId,
  isShortcutAppRunning,
  launchUbisoftInstallViaShortcut,
  terminateShortcutApp,
} from "../utils/ubisoftShortcutLaunch";

// ============================================================
// CDP-based style management (CSSLoader pattern)
// Uses Chrome DevTools Protocol to inject CSS across CEF process boundary.
// ============================================================

// Per-appId operation queue to prevent inject/remove race conditions.
// Each key maps to the last CDP promise for that appId, so operations
// for the same app are chained sequentially.
const pendingCDPOps: Map<number, Promise<void>> = new Map();

// Generation counter per appId. Incremented on every hide request.
// Used to detect stale unhide operations that should be suppressed.
const hideGeneration: Map<number, number> = new Map();

function chainCDPOp(appId: number, op: () => Promise<void>): void {
  const prev = pendingCDPOps.get(appId) || Promise.resolve();
  const next = prev.then(op, op); // run op even if prev rejected
  pendingCDPOps.set(appId, next);
  // Clean up after completion to avoid memory leak
  next.finally(() => {
    if (pendingCDPOps.get(appId) === next) {
      pendingCDPOps.delete(appId);
    }
  });
}

const pendingUnhides: Map<number, NodeJS.Timeout> = new Map();

export function scheduleRemoveHidePlaySectionCDP(appId: number) {
  // Clear any existing timer for this app
  if (pendingUnhides.has(appId)) {
    clearTimeout(pendingUnhides.get(appId)!);
  }
  const timer = setTimeout(() => {
    pendingUnhides.delete(appId);
    removeHidePlaySectionCDP(appId);
  }, 150); // Small 150ms delay catches immediate React remounts
  pendingUnhides.set(appId, timer);
}

/**
 * Inject CSS to hide native PlaySection via CDP.
 * Called from the patcher (asynchronously, non-blocking).
 * Operations for the same appId are chained to prevent race conditions.
 * NOTE: No session guard - CDP must re-hide after every React re-render
 * because React destroys and recreates DOM elements.
 */
export async function injectHidePlaySectionCDP(appId: number): Promise<void> {
  // Increment generation: any pending unhide for a lower generation is stale
  const gen = (hideGeneration.get(appId) || 0) + 1;
  hideGeneration.set(appId, gen);

  // Cancel any pending unhide for this app
  const unhideTimer = pendingUnhides.get(appId);
  if (unhideTimer) {
    clearTimeout(unhideTimer);
    pendingUnhides.delete(appId);
    console.log(
      `[PlaySectionWrapper] Cancelled pending unhide for ${appId} (React remounted)`,
    );
  }

  chainCDPOp(appId, async () => {
    try {
      const timeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("CDP hide timeout (10s)")), 10000),
      );
      const result = await Promise.race([
        call<[number], { success: boolean; error?: string }>(
          "hide_native_play_section",
          appId,
        ),
        timeout,
      ]);

      if (result.success) {
        console.log(
          `[PlaySectionWrapper] Hidden native play section via CDP for app ${appId}`,
        );
      } else if (result.error === "not_found") {
        // Suppress "not_found" error, as it evaluates to expected state during component mount fast burst
      } else {
        console.warn(`[PlaySectionWrapper] CDP hide failed: ${result.error}`);
      }
    } catch (error) {
      console.warn(`[PlaySectionWrapper] CDP hide call failed:`, error);
    }
  });
}

/**
 * Remove hide CSS for specific app via CDP.
 * Called when the component confirms the game IS installed, or on error/unmount.
 * Operations for the same appId are chained to prevent race conditions.
 */
export async function removeHidePlaySectionCDP(appId: number): Promise<void> {
  // Capture the current generation at enqueue time.
  // If a hide is requested after this unhide was enqueued, the generation
  // will be higher when the chain executes, and we skip the stale unhide.
  const genAtEnqueue = hideGeneration.get(appId) || 0;

  chainCDPOp(appId, async () => {
    const currentGen = hideGeneration.get(appId) || 0;
    if (currentGen > genAtEnqueue) {
      console.log(
        `[PlaySectionWrapper] Suppressed stale unhide for app ${appId} (gen ${genAtEnqueue} < ${currentGen})`,
      );
      return;
    }

    try {
      const timeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("CDP unhide timeout (10s)")), 10000),
      );
      const result = await Promise.race([
        call<[number], { success: boolean; error?: string }>(
          "unhide_native_play_section",
          appId,
        ),
        timeout,
      ]);

      if (result.success) {
        console.log(
          `[PlaySectionWrapper] Unhidden native play section via CDP for app ${appId}`,
        );
      }
    } catch (error) {
      console.error(`[PlaySectionWrapper] CDP unhide failed:`, error);
    }
  });
}

let gameInfoCacheRef: Map<number, { info: any; timestamp: number }> | null =
  null;
const CACHE_TTL = 5000;

export function setPlayButtonCacheRef(
  cache: Map<number, { info: any; timestamp: number }>,
) {
  gameInfoCacheRef = cache;
}

// Helper functions for formatting stats
function formatLastPlayed(timestamp: number): string {
  if (!timestamp) return "Never";
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const diffDays = Math.floor(
    (now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24),
  );
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString();
}

interface PlaySectionWrapperProps {
  appId: number;
  playSectionClassName?: string;
}

const PlaySectionWrapperInner: FC<PlaySectionWrapperProps> = ({
  appId,
  playSectionClassName,
}) => {
  // Lazy init from cache: avoids blank screen on remount (e.g. after Steam Settings → B → back).
  // If any cached entry exists (even stale), use it immediately so the first render has data.
  // Stale data is refreshed in the background by the useEffect below.
  const [gameInfo, setGameInfo] = useState<any>(
    () => gameInfoCacheRef?.get(appId)?.info ?? null,
  );
  const [loading, setLoading] = useState<boolean>(
    () => !gameInfoCacheRef?.has(appId),
  );
  const [downloadInfo, setDownloadInfo] = useState<{
    id: string;
    status: string;
    progress_percent: number;
    downloaded_bytes?: number;
    total_bytes?: number;
    speed_mbps?: number;
    eta_seconds?: number;
    download_phase?: string;
    phase_message?: string;
    is_preparing?: boolean;
    was_previously_installed?: boolean;
  } | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [lastPlayedTimestamp, setLastPlayedTimestamp] = useState(0);
  const [updateAvailable, setUpdateAvailable] = useState<boolean | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [ubisoftInstalling, setUbisoftInstalling] = useState(false);
  const debugLoggedRef = useRef(false);
  const ubisoftInstallPollTicksRef = useRef(0);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();

  const isDownloading =
    downloadInfo !== null &&
    ["queued", "downloading", "extracting", "verifying"].includes(
      downloadInfo.status,
    );

  // Whether we should show our custom UI (and hide native PlaySection)
  // For Unifideck games: always show custom UI (install, cancel, OR play)
  // Non-Unifideck shortcuts: get_game_info returns null → gameInfo is null → false
  const shouldShowCustom = !loading && gameInfo && !gameInfo.error;

  // Style management: CDP hide with burst + persistent polling.
  // React can recreate native PlaySection DOM elements at unpredictable times,
  // especially when navigating from Decky settings, console, or Steam menu.
  // Strategy: fast burst for immediate navigation, then slow poll to catch late re-renders.
  // Safety: CDP's hide_native_play_section has a container-size check (>50% viewport)
  // that prevents blanking the entire page in offline mode or unusual DOM structures.
  useEffect(() => {
    if (!shouldShowCustom) return;

    const timers: NodeJS.Timeout[] = [];
    const doHide = () => injectHidePlaySectionCDP(appId);

    // Fast burst: catch the native PlaySection as soon as it appears
    doHide(); // Immediate
    timers.push(setTimeout(doHide, 50)); // Fast retry
    timers.push(setTimeout(doHide, 150)); // DOM settle
    timers.push(setTimeout(doHide, 300)); // React reconciliation
    timers.push(setTimeout(doHide, 600)); // Late re-render

    // Persistent poll: catch React re-renders that happen after the burst.
    // CDP hide is idempotent (re-applying styles to already-hidden elements is harmless).
    const interval = setInterval(doHide, 2000);

    return () => {
      timers.forEach(clearTimeout);
      clearInterval(interval);
      // Use scheduled removal! Decky UI might recreate this component rapidly
      // during navigation/focus changes, so we wait 150ms before unhiding natively.
      scheduleRemoveHidePlaySectionCDP(appId);
    };
  }, [shouldShowCustom, appId]);

  // Failure handling: if get_game_info fails or returns null/error,
  // remove the hide CSS so the native PlaySection is restored
  // rather than leaving a permanent blank area.
  useEffect(() => {
    if (!loading && !gameInfo) {
      console.warn(
        `[PlaySectionWrapper] get_game_info returned null for app ${appId}, restoring native PlaySection`,
      );
      removeHidePlaySectionCDP(appId);
    }
  }, [loading, gameInfo, appId]);

  // Navigation cleanup: when the user navigates to a DIFFERENT game page,
  // the appId changes. Remove the old appId's hide CSS so it doesn't
  // leak to other games. The new appId's CSS will be injected by the patcher.
  const prevAppIdRef = useRef(appId);
  // Persists extracted user params so handlePlay can forward them to the backend
  const userParamsRef = useRef<string>("");
  // Tracks FC we cleared so we can restore it on unmount and guard against
  // the else branch clearing the saved Proton tool on effect re-runs.
  const clearedFCRef = useRef<{
    tool_name: string;
    bypassOptions: string;
  } | null>(null);
  useEffect(() => {
    const prevAppId = prevAppIdRef.current;
    if (prevAppId !== appId) {
      removeHidePlaySectionCDP(prevAppId);
      prevAppIdRef.current = appId;
    }
  }, [appId]);

  // Safety timeout: if loading hangs (e.g. get_game_info never resolves),
  // force loading=false after 8s so the component falls back to showing
  // the native Steam game details page instead of a blank placeholder.
  useEffect(() => {
    if (!loading) return;
    const timer = setTimeout(() => {
      console.warn(
        `[PlaySectionWrapper] Loading timeout for app ${appId}, falling back to native`,
      );
      setLoading(false);
    }, 8000);
    return () => clearTimeout(timer);
  }, [loading, appId]);

  // Fetch game info on mount. With lazy state init above, any cached entry is already rendered.
  // - Fresh cache (< TTL): skip fetch entirely — data is already shown.
  // - Stale cache (≥ TTL): data is already shown from init; re-fetch in background and update.
  // - No cache: loading=true from init; fetch and show when done.
  useEffect(() => {
    const cached = gameInfoCacheRef?.get(appId);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
      return; // Fresh — already initialized, nothing to do
    }

    call<[number], any>("get_game_info", appId)
      .then((info) => {
        console.log(
          "[Unifideck DIAG] get_game_info result:",
          "appId:",
          appId,
          "hasError:",
          !!info?.error,
          "isInstalled:",
          info?.is_installed,
          "store:",
          info?.store,
        );
        const processedInfo = info?.error ? null : info;
        setGameInfo(processedInfo);
        if (processedInfo?.is_installed) {
          updateSingleGameStatus({
            appId,
            store: processedInfo.store,
            isInstalled: true,
          });
        }
        if (
          processedInfo?.has_update !== undefined &&
          processedInfo?.has_update !== null
        ) {
          setUpdateAvailable(processedInfo.has_update);
        }
        if (gameInfoCacheRef && processedInfo) {
          gameInfoCacheRef.set(appId, {
            info: processedInfo,
            timestamp: Date.now(),
          });
        }
      })
      .catch((err) => {
        console.error(`[Unifideck DIAG] get_game_info FAILED:`, err);
        setGameInfo(null);
      })
      .finally(() => setLoading(false));
  }, [appId]);

  // Fetch last played timestamp via GetPlaytime API
  // Confirmed working for both Steam and non-Steam games
  useEffect(() => {
    (window as any).SteamClient?.Apps?.GetPlaytime?.(appId)
      ?.then((result: any) => {
        if (result?.rtLastTimePlayed) {
          setLastPlayedTimestamp(result.rtLastTimePlayed);
        }
      })
      .catch(() => {});
  }, [appId]);

  // Detect game running state by polling display_status from appStore.
  // display_status is a MobX-managed property on the app overview — reliable for non-Steam shortcuts.
  // Also registers for lifetime notifications as a supplementary trigger for faster updates.
  // EDisplayStatus: Running=4, Launching=1, Terminating=35
  useEffect(() => {
    if (!gameInfo?.is_installed) {
      setIsRunning(false);
      return;
    }

    const checkRunningState = () => {
      try {
        const appStore = (window as any).appStore;
        const overview = appStore?.m_mapApps?.get?.(appId);
        if (!overview) return;

        // One-time diagnostic log to help debug running state issues
        if (!debugLoggedRef.current) {
          console.log(
            `[PlaySectionWrapper] Overview for ${appId}:`,
            "local_per_client_data:",
            JSON.stringify(overview.local_per_client_data),
            "per_client_data:",
            JSON.stringify(overview.per_client_data),
          );
          debugLoggedRef.current = true;
        }

        // Try local_per_client_data first (local machine), then per_client_data array
        const localData = overview.local_per_client_data;
        const displayStatus =
          localData?.display_status ??
          overview.per_client_data?.[0]?.display_status;

        // Only update state if display_status is actually available.
        // Don't override bRunning-derived state (from lifetime notifications)
        // with false when display_status isn't populated for non-Steam shortcuts.
        if (displayStatus !== undefined && displayStatus !== null) {
          const running = displayStatus === 4 || displayStatus === 1;
          setIsRunning(running);
        }
      } catch {
        // Silently fail — appStore shape may vary
      }
    };

    // Initial check + poll every 2 seconds
    checkRunningState();
    const pollInterval = setInterval(checkRunningState, 2000);

    // Supplementary: lifetime notifications for faster detection
    let unregLifetime: { unregister(): void } | null = null;
    try {
      unregLifetime =
        window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(
          (data) => {
            // For non-Steam shortcuts, unAppID may be 0 — accept both
            if (data.unAppID !== 0 && data.unAppID !== appId) return;
            console.log(
              `[PlaySectionWrapper] Lifetime notification: unAppID=${data.unAppID}, running=${data.bRunning}`,
            );
            // Use bRunning directly — proven reliable for non-Steam shortcuts
            // (MoonDeck pattern). display_status is NOT reliably populated for shortcuts.
            setIsRunning(data.bRunning);
          },
        ) ?? null;
    } catch {
      // GameSessions may not be available
    }

    return () => {
      clearInterval(pollInterval);
      unregLifetime?.unregister?.();
    };
  }, [appId, gameInfo?.is_installed]);

  // Listen for game state changes (e.g. uninstall from GameInfoPanel/InstallInfoDisplay)
  // Same CustomEvent pattern as VIEW_MODE_CHANGE_EVENT (confirmed working)
  useEffect(() => {
    const handleGameStateChange = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.appId !== appId) return;

      console.log(
        `[PlaySectionWrapper] Game state changed for app ${appId}: installed=${detail.isInstalled}`,
      );

      // Clear cache for this app
      if (gameInfoCacheRef) {
        gameInfoCacheRef.delete(appId);
      }

      // Ensure CDP hide stays active (re-inject in case it was cleared)
      injectHidePlaySectionCDP(appId);

      // Re-fetch game info to update component state
      setLoading(true);
      call<[number], any>("get_game_info", appId)
        .then((info) => {
          const processedInfo = info?.error ? null : info;
          setGameInfo(processedInfo);
          if (
            processedInfo?.has_update !== undefined &&
            processedInfo?.has_update !== null
          ) {
            setUpdateAvailable(processedInfo.has_update);
          }
          if (gameInfoCacheRef && processedInfo) {
            gameInfoCacheRef.set(appId, {
              info: processedInfo,
              timestamp: Date.now(),
            });
          }
        })
        .catch((err) => {
          console.error(
            `[PlaySectionWrapper] Error re-fetching game info:`,
            err,
          );
          setGameInfo(null);
        })
        .finally(() => setLoading(false));
    };

    window.addEventListener(
      "unifideck-game-state-changed",
      handleGameStateChange,
    );
    return () => {
      window.removeEventListener(
        "unifideck-game-state-changed",
        handleGameStateChange,
      );
    };
  }, [appId]);

  // Share download state with the game action interceptor
  useEffect(() => {
    setInterceptorDownloadState({
      isDownloading,
      downloadId: downloadInfo?.id,
      gameInfo,
    });
  }, [isDownloading, downloadInfo?.id, gameInfo]);

  // Proton compat tool handling: When a Unifideck game page loads, check if
  // Steam's Force Compatibility is set with a Proton tool. If so:
  // 1. Save the tool to proton_settings.json (launcher reads at Priority 2.5)
  // 2. Clear FC from Steam (prevents gaming mode loading screen)
  // 3. Restore simple launch options (user params preserved, no bypass needed)
  useEffect(() => {
    if (!gameInfo?.is_installed || !gameInfo?.store || !gameInfo?.game_id)
      return;
    if (appId <= 2000000000) return;

    let cancelled = false;
    const storeGameId = `${gameInfo.store}:${gameInfo.game_id}`;

    (async () => {
      try {
        const result = await call<
          [string],
          {
            success: boolean;
            tool_name?: string;
            appid_unsigned?: number;
            is_linux_runtime?: boolean;
            launcher_path?: string;
            current_launch_options?: string;
            saved_proton_tool?: string;
            error?: string;
          }
        >("get_compat_tool_for_game", storeGameId);

        if (cancelled || !result?.success) return;

        const launcherPath = result.launcher_path;
        if (!launcherPath) return;

        // Extract user-appended params from current launch options.
        // Strips: launcher path, quoted storeGameId, bare storeGameId, #%command% suffix.
        // Preserves: user-added quoted values, user %command% in wrappers (e.g. gamemoderun %command%).
        const currentOpts = result.current_launch_options ?? storeGameId;
        const escapedStoreGameId = storeGameId.replace(
          /[.*+?^${}()|[\]\\]/g,
          "\\$&",
        );
        const extractUserParams = (opts: string): string => {
          return opts
            .replace(/\s*#%command%\s*$/g, "") // Strip Unifideck's #%command% suffix only (not user's bare %command%)
            .replace(
              new RegExp('"' + escapedStoreGameId + '"', "g"),
              "",
            ) // Remove quoted storeGameId only (preserves other quoted values)
            .replace(new RegExp(escapedStoreGameId, "g"), "") // bare id
            .replace(/\/\S+unifideck-launcher/g, "") // launcher path (unquoted)
            .replace(/\s{2,}/g, " ") // Collapse multiple spaces from removals
            .trim();
        };
        const userParams = extractUserParams(currentOpts);
        userParamsRef.current = userParams;

        if (result.tool_name && !result.is_linux_runtime) {
          // Proton compat tool detected - save for the launcher (only if changed)
          if (result.saved_proton_tool !== result.tool_name) {
            await call<[string, string], { success: boolean }>(
              "save_proton_setting",
              storeGameId,
              result.tool_name,
            );
          }

          if (cancelled) return;

          // Clear Force Compatibility from Steam so RunGame doesn't create
          // a Proton session (which causes the perpetual loading screen in
          // gaming mode). The launcher reads Proton from proton_settings.json
          // independently, so the game still uses the correct Proton version.
          window.SteamClient?.Apps?.SpecifyCompatTool?.(appId, "");

          // Restore simple launch options (no #%command% bypass needed since FC
          // is cleared). This makes the launch identical to games without FC.
          const restoredOptions = userParams
            ? `${storeGameId} ${userParams}`
            : storeGameId;

          if (currentOpts !== restoredOptions) {
            window.SteamClient?.Apps?.SetShortcutLaunchOptions(
              appId,
              restoredOptions,
            );
          }

          // Save what we cleared so cleanup can restore FC + bypass options
          // when the user leaves the game page (preserves their FC selection).
          const bypassOptions = userParams
            ? `${launcherPath} "${storeGameId}" ${userParams} #%command%`
            : `${launcherPath} "${storeGameId}" #%command%`;
          clearedFCRef.current = {
            tool_name: result.tool_name,
            bypassOptions,
          };

          console.log(
            `[PlaySectionWrapper] Cleared FC for "${gameInfo.title}" — saved ${result.tool_name} to proton_settings.json`,
          );
        } else {
          // No Proton tool (or Linux runtime) - restore launch options preserving user params
          const restoredOptions = userParams
            ? `${storeGameId} ${userParams}`
            : storeGameId;

          // Only write if actually different (avoids unnecessary overwrites every page load)
          if (currentOpts !== restoredOptions) {
            window.SteamClient?.Apps?.SetShortcutLaunchOptions(
              appId,
              restoredOptions,
            );
          }

          // No FC set — clear saved proton setting so launcher uses default
          if (result.saved_proton_tool) {
            await call<[string, string], { success: boolean }>(
              "save_proton_setting",
              storeGameId,
              "",
            );
          }
        }
      } catch (error) {
        console.error(
          "[PlaySectionWrapper] Error checking compat tool:",
          error,
        );
      }
    })();

    return () => {
      cancelled = true;
      // Restore FC + bypass options when leaving the game page so the user
      // sees their FC selection in Steam Properties. FC will be cleared
      // again on the next page load (the effect re-runs).
      const cleared = clearedFCRef.current;
      if (cleared) {
        window.SteamClient?.Apps?.SpecifyCompatTool?.(appId, cleared.tool_name);
        window.SteamClient?.Apps?.SetShortcutLaunchOptions?.(
          appId,
          cleared.bypassOptions,
        );
        clearedFCRef.current = null;
        console.log(
          `[PlaySectionWrapper] Restored FC (${cleared.tool_name}) for app ${appId}`,
        );
      }
    };
  }, [appId, gameInfo?.is_installed, gameInfo?.store, gameInfo?.game_id]);

  // Check for game updates once when installed & not downloading
  useEffect(() => {
    if (!gameInfo?.is_installed || isDownloading || isUpdating) return;
    if (!gameInfo?.store || !gameInfo?.game_id) return;

    let cancelled = false;

    (async () => {
      try {
        const result = await call<
          [number],
          {
            success: boolean;
            has_update: boolean | null;
            store: string;
          }
        >("check_game_update", appId);

        if (cancelled) return;

        if (result?.success) {
          setUpdateAvailable(result.has_update ?? null);
          console.log(
            `[PlaySectionWrapper] Update check for ${appId}: has_update=${result.has_update}`,
          );
        }
      } catch (error) {
        console.error(`[PlaySectionWrapper] Update check error:`, error);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [appId, gameInfo?.is_installed, gameInfo?.store, gameInfo?.game_id]);

  // Unified download and update progress polling
  useEffect(() => {
    if (!gameInfo) return;

    const checkDownloadStatus = async () => {
      try {
        const result = await call<
          [string, string],
          {
            success: boolean;
            is_downloading: boolean;
            download_info?: any;
          }
        >("is_game_downloading", gameInfo.game_id, gameInfo.store);

        if (!result.success) return;

        const info = result.download_info;
        const prevStatus = downloadInfo?.status;
        const newStatus = info?.status;

        // Update state
        setDownloadInfo(info || null);

        // Handle completion
        if (
          (prevStatus === "downloading" ||
            prevStatus === "extracting" ||
            prevStatus === "verifying") &&
          newStatus === "completed"
        ) {
          setIsUpdating(false);
          setUpdateAvailable(false);

          toaster.toast({
            title: info.was_previously_installed
              ? t("toasts.updateComplete", "Update Complete!")
              : t("toasts.installComplete"),
            body: info.was_previously_installed
              ? t("toasts.updateCompleteMessage", {
                  title: gameInfo?.title || "Game",
                })
              : t("toasts.installCompleteMessage", {
                  title: gameInfo?.title || "Game",
                }),
            duration: 10000,
            critical: true,
          });

          // Refresh game info to update UI
          if (gameInfoCacheRef) gameInfoCacheRef.delete(appId);
          call<[number], any>("get_game_info", appId).then((info) => {
            const processedInfo = info?.error ? null : info;
            if (processedInfo && gameInfoCacheRef) {
              gameInfoCacheRef.set(appId, {
                info: processedInfo,
                timestamp: Date.now(),
              });
              updateSingleGameStatus({
                appId,
                store: processedInfo.store,
                isInstalled: processedInfo.is_installed,
              });
            }
            if (
              processedInfo?.has_update !== undefined &&
              processedInfo?.has_update !== null
            ) {
              setUpdateAvailable(processedInfo.has_update);
            }
            setGameInfo(processedInfo);
          });
        }

        // Handle error/cancel
        if (newStatus === "error" || newStatus === "cancelled") {
          setIsUpdating(false);
        }

        // Ubisoft launcher-driven installs bypass the download queue.
        // Poll get_game_info periodically so Install -> Play updates without requiring navigation.
        // Also check if UPC is still running so we can clear the "Cancel" state.
        if (gameInfo.store === "ubisoft" && !gameInfo.is_installed && !result.is_downloading) {
          ubisoftInstallPollTicksRef.current += 1;
          if (ubisoftInstallPollTicksRef.current % 10 === 0) {
            // Check if install session is still active
            const active = await call<[string], boolean>(
              "is_ubisoft_install_active",
              gameInfo.game_id,
            );
            if (!active && !isShortcutAppRunning(appId)) {
              setUbisoftInstalling(false);
            }

            const refreshed = await call<[number], any>("get_game_info", appId);
            const refreshedInfo = refreshed?.error ? null : refreshed;
            if (refreshedInfo?.is_installed) {
              setUbisoftInstalling(false);
              setGameInfo(refreshedInfo);
              if (gameInfoCacheRef) {
                gameInfoCacheRef.set(appId, {
                  info: refreshedInfo,
                  timestamp: Date.now(),
                });
              }
              updateSingleGameStatus({
                appId,
                store: refreshedInfo.store,
                isInstalled: true,
              });
              toaster.toast({
                title: t("toasts.installComplete"),
                body: t("toasts.installCompleteMessage", {
                  title: refreshedInfo.title || gameInfo?.title || "Game",
                }),
                duration: 10000,
              });
            }
          }
        } else {
          ubisoftInstallPollTicksRef.current = 0;
        }
      } catch (error) {
        console.error(
          "[PlaySectionWrapper] Error polling download status:",
          error,
        );
      }
    };

    checkDownloadStatus();
    const interval = setInterval(checkDownloadStatus, 1000);
    return () => clearInterval(interval);
  }, [gameInfo, appId]);

  // Autofocus: Focus our action button via CDP after hiding the native one.
  // DOM .focus() doesn't work cross-process (plugin CEF ≠ SP tab), so we
  // use CDP to find and focus our button in the SP tab's DOM directly.
  // Non-critical — short 3s timeout to avoid blocking on CDP degradation.
  useEffect(() => {
    if (!shouldShowCustom) return;
    const timer = setTimeout(() => {
      const focusTimeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("focus timeout")), 3000),
      );
      Promise.race([
        call<[number], { success: boolean }>("focus_unifideck_button", appId),
        focusTimeout,
      ]).catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [shouldShowCustom, appId]);

  // Start download with optional language (for GOG games) - matches GameInfoPanel behavior
  const startDownload = async (language?: string) => {
    if (!gameInfo) return;

    const result = await call<
      [string, string, string, boolean, string | null],
      any
    >(
      "add_to_download_queue",
      gameInfo.game_id,
      gameInfo.title,
      gameInfo.store,
      gameInfo.is_installed || false,
      language || null,
    );

    if (result.success) {
      toaster.toast({
        title: t("toasts.downloadStarted"),
        body: t("toasts.downloadQueued", { title: gameInfo.title }),
        duration: 5000,
      });
    } else {
      toaster.toast({
        title: t("toasts.downloadFailed"),
        body: result.error
          ? t(result.error)
          : t("toasts.downloadFailedMessage"),
        duration: 10000,
        critical: true,
      });
    }
  };

  const startUbisoftInstall = async () => {
    if (!gameInfo) return;
    setUbisoftInstalling(true);
    let result = await launchUbisoftInstallViaShortcut(
      `${gameInfo.store}:${gameInfo.game_id}`,
    );

    if (!result.success) {
      result = await call<
        [string, string],
        { success: boolean; already_running?: boolean; error?: string }
      >("open_ubisoft_launcher_for_install", gameInfo.game_id, gameInfo.title);
    }

    if (result.success) {
      toaster.toast({
        title: t("toasts.ubisoftLauncherOpening"),
        body: t(
          result.already_running
            ? "toasts.ubisoftLauncherAlreadyOpenMessage"
            : "toasts.ubisoftLauncherOpeningMessage",
          { title: gameInfo.title },
        ),
        duration: 7000,
      });
      return;
    }

    setUbisoftInstalling(false);
    toaster.toast({
      title: t("toasts.ubisoftLauncherOpenFailed"),
      body: result.error
        ? t(result.error)
        : t("toasts.ubisoftLauncherOpenFailedMessage"),
      duration: 10000,
      critical: true,
    });
  };

  const cancelUbisoftInstall = async () => {
    if (!gameInfo) return;
    const terminated = terminateShortcutApp(appId);
    const result = await call<[string], { success: boolean; error?: string }>(
      "cancel_ubisoft_install",
      gameInfo.game_id,
    );
    setUbisoftInstalling(false);
    if (terminated || result.success) {
      toaster.toast({
        title: t("toasts.downloadCancelled"),
        body: t("toasts.downloadCancelledMessage", { title: gameInfo.title }),
        duration: 5000,
      });
      return;
    }

    toaster.toast({
      title: t("toasts.cancelFailed"),
      body: result.error ? t(result.error) : t("toasts.cancelFailedMessage"),
      duration: 5000,
      critical: true,
    });
  };

  // Install handler - checks for GOG language selection
  const handleInstall = async () => {
    if (!gameInfo) return;

    if (gameInfo.store === "ubisoft") {
      await startUbisoftInstall();
      return;
    }

    // For GOG games, check if multiple languages are available
    if (gameInfo.store === "gog") {
      try {
        const langResult = await call<
          [string],
          { success: boolean; languages: string[]; error?: string }
        >("get_gog_game_languages", gameInfo.game_id);

        const languages = langResult?.languages;
        if (!langResult?.success || !Array.isArray(languages)) {
          console.warn(
            "[PlaySectionWrapper] Invalid language response, falling back to default:",
            langResult?.error || "unknown error",
          );
          startDownload();
          return;
        }

        // Multiple languages - show selection modal
        if (languages.length > 1) {
          showModal(
            <GOGLanguageSelectModal
              gameTitle={gameInfo.title}
              languages={languages}
              onConfirm={(selectedLang) => startDownload(selectedLang)}
            />,
          );
          return;
        }

        // Single or no language - use first available or fallback
        startDownload(languages[0] || undefined);
        return;
      } catch (error) {
        console.error(
          "[PlaySectionWrapper] Error fetching GOG languages:",
          error,
        );
        // Fallback to download without language selection
      }
    }

    // Non-GOG games or fallback - download without language
    startDownload();
  };

  // Cancel download handler
  const handleCancel = async () => {
    const dlId = downloadInfo?.id || `${gameInfo.store}:${gameInfo.game_id}`;

    const result = await call<[string], { success: boolean; error?: string }>(
      "cancel_download_by_id",
      dlId,
    );

    if (result.success) {
      toaster.toast({
        title: t("toasts.downloadCancelled"),
        body: t("toasts.downloadCancelledMessage", { title: gameInfo?.title }),
        duration: 5000,
      });
    } else {
      toaster.toast({
        title: t("toasts.cancelFailed"),
        body: result.error ? t(result.error) : t("toasts.cancelFailedMessage"),
        duration: 5000,
        critical: true,
      });
    }
  };

  // Show install confirmation modal (Ubisoft gets a store-specific modal)
  const showInstallConfirmation = () => {
    const isUbisoft = gameInfo?.store === "ubisoft";
    showModal(
      <ConfirmModal
        strTitle={t(isUbisoft ? "confirmModals.ubisoftInstallTitle" : "confirmModals.installTitle")}
        strDescription={t(
          isUbisoft ? "confirmModals.ubisoftInstallDescription" : "confirmModals.installDescription",
          { title: gameInfo?.title },
        )}
        strOKButtonText={t(isUbisoft ? "confirmModals.ubisoftInstallConfirm" : "confirmModals.yes")}
        strCancelButtonText={t("confirmModals.no")}
        onOK={() => {
          handleInstall();
          return true; // Return true to dismiss modal
        }}
      />,
    );
  };

  // Show cancel confirmation modal
  const showCancelConfirmation = () => {
    showModal(
      <ConfirmModal
        strTitle={t("confirmModals.cancelTitle")}
        strDescription={t("confirmModals.cancelDescription", {
          title: gameInfo?.title,
        })}
        strOKButtonText={t("confirmModals.yes")}
        strCancelButtonText={t("confirmModals.no")}
        bDestructiveWarning={true}
        onOK={() => handleCancel()}
      />,
    );
  };

  // Handle install/cancel button click
  const handleClick = () => {
    if (ubisoftInstalling) {
      cancelUbisoftInstall();
    } else if (isDownloading) {
      showCancelConfirmation();
    } else if (!gameInfo?.is_installed) {
      showInstallConfirmation();
    }
  };

  // Handle play button click — launches via Steam's RunGame.
  // FC is cleared at page-load time, so RunGame works without a loading screen.
  const handlePlay = () => {
    try {
      const gameId = getShortcutRunGameId(appId);
      console.log(
        `[PlaySectionWrapper] Launching game ${appId} via RunGame (gameId=${gameId})`,
      );
      window.SteamClient?.Apps?.RunGame?.(gameId, "", -1, 100);
    } catch (error) {
      console.error(`[PlaySectionWrapper] RunGame failed:`, error);
    }
  };

  // Handle stop/close button — terminates the running game.
  // Kills processes via backend + tells Steam to terminate.
  const handleStop = () => {
    setIsRunning(false);
    console.log(`[PlaySectionWrapper] Stopping game ${appId}`);

    // Kill actual game processes via backend
    call<[number], { success: boolean }>("stop_game_processes", appId).catch(
      (err) =>
        console.error(
          "[PlaySectionWrapper] stop_game_processes failed:",
          err,
        ),
    );

    // Tell Steam to terminate
    const appStore = (window as any).appStore;
    const overview = appStore?.m_mapApps?.get?.(appId);
    const gameId = overview?.gameid ?? String(appId);
    window.SteamClient?.Apps?.TerminateApp?.(gameId, false);
  };

  // DIAG: Track component lifecycle (temporary)
  console.log(
    "[Unifideck DIAG] PlaySectionWrapper render:",
    "appId:",
    appId,
    "loading:",
    loading,
    "gameInfo:",
    !!gameInfo,
    "shouldShowCustom:",
    shouldShowCustom,
    "isDownloading:",
    isDownloading,
  );

  // While loading: show visible placeholder to prevent blank screen.
  // Error (gameInfo null): return null so native PlaySection (unhidden via CDP) shows through.
  if (!shouldShowCustom) {
    if (loading) {
      return (
        <div
          ref={wrapperRef}
          data-unifideck-play-wrapper="true"
          className={playSectionClassName || undefined}
          style={{
            display: "flex",
            alignItems: "center",
            width: "100%",
            padding: "16px",
            boxSizing: "border-box",
            background: "rgba(14, 20, 27, 0.33)",
            position: "relative" as const,
            zIndex: 2,
            minHeight: "80px",
          }}
        />
      );
    }
    // Error state: gameInfo is null/error — return null so native PlaySection shows
    return null;
  }

  // Render a custom PlaySection that visually matches Steam's native layout.
  // NOTE: appActionButtonClasses (PlayButton, Green) resolve to stale CSS class names
  // that don't match current Steam DOM. Use explicit inline styles + injected <style> instead.

  // Shared inline styles for all action buttons (Install/Play/Resume/Cancel)
  // Native Steam button: wide, left-aligned text, flush to left edge
  const actionBtnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-start",
    gap: "8px",
    flex: "0 0 auto",
    width: "auto",
    minWidth: "200px",
    height: "48px",
    padding: "0 24px",
    color: "#fff",
    fontSize: "16px",
    fontWeight: 500,
    borderRadius: "4px",
    border: "none",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  };

  // Injected CSS for hover/focus pseudo-classes (can't do with inline styles).
  // Button colors match native Steam behavior:
  //   Install: grey default → blue on focus/hover (matches native Install screenshot)
  //   Cancel:  red default → brighter red on focus/hover
  //   Play:    grey default → green on focus/hover
  //   Resume:  grey default → blue on focus/hover
  //   X:       grey default → lighter on focus/hover
  const buttonStyles = `
    .unifideck-install-btn,
    .unifideck-play-btn,
    .unifideck-resume-btn,
    .unifideck-stop-btn,
    .unifideck-cancel-btn {
      background: rgba(255, 255, 255, 0.1) !important;
      transition: background 0.15s ease, filter 0.15s ease !important;
    }
    .unifideck-install-btn:hover,
    .unifideck-install-btn.gpfocus {
      background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
    }
    .unifideck-cancel-btn:hover,
    .unifideck-cancel-btn.gpfocus {
      background: linear-gradient(135deg, #dc3545 0%, #c82333 100%) !important;
    }
    .unifideck-play-btn:hover,
    .unifideck-play-btn.gpfocus {
      background: linear-gradient(135deg, #59bf40 0%, #459e31 100%) !important;
    }
    .unifideck-resume-btn:hover,
    .unifideck-resume-btn.gpfocus {
      background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
    }
    .unifideck-stop-btn:hover,
    .unifideck-stop-btn.gpfocus {
      background: rgba(255, 255, 255, 0.2) !important;
    }
    .unifideck-update-btn {
      background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
      transition: filter 0.15s ease !important;
    }
    .unifideck-update-btn:hover,
    .unifideck-update-btn.gpfocus {
      filter: brightness(1.15) !important;
    }
    .unifideck-update-btn:disabled {
      opacity: 0.6 !important;
      cursor: not-allowed !important;
    }
    .unifideck-install-btn:active,
    .unifideck-cancel-btn:active,
    .unifideck-play-btn:active,
    .unifideck-resume-btn:active,
    .unifideck-stop-btn:active,
    .unifideck-update-btn:active {
      filter: brightness(0.85) !important;
    }
    @keyframes unifideck-slide {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(300%); }
    }
  `;

  // Helper: format bytes for update progress display
  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  const formatETA = (seconds: number): string => {
    if (!seconds || seconds <= 0) return "--:--";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0)
      return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  // Helper: Render unified rich progress UI
  const renderUnifiedProgress = (info: NonNullable<typeof downloadInfo>) => {
    const isIndeterminate =
      info.download_phase === "extracting" ||
      info.download_phase === "verifying";

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "4px",
          marginLeft: "32px",
          flex: "0 0 50%",
          maxWidth: "50%",
          minWidth: 0,
        }}
      >
        {/* Status label */}
        <div
          style={{
            fontSize: "11px",
            fontWeight: 600,
            textTransform: "uppercase",
            color: "#8f98a0",
            letterSpacing: "0.08em",
          }}
        >
          {info.download_phase === "extracting"
            ? "Extracting..."
            : info.download_phase === "verifying"
            ? "Verifying..."
            : info.status === "queued"
            ? info.was_previously_installed
              ? "Update Queued"
              : "Download Queued"
            : info.was_previously_installed
            ? "Downloading Update"
            : "Downloading..."}
        </div>

        {/* Progress bar */}
        <div
          style={{
            width: "100%",
            height: "4px",
            background: "rgba(255, 255, 255, 0.1)",
            borderRadius: "2px",
            overflow: "hidden",
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              height: "100%",
              width: isIndeterminate ? "40%" : `${info.progress_percent}%`,
              background: "#1a9fff",
              borderRadius: "2px",
              transition: isIndeterminate ? "none" : "width 0.3s ease",
              animation: isIndeterminate
                ? "unifideck-slide 1.5s infinite linear"
                : "none",
            }}
          />
        </div>

        {/* Rich stats or Phase message */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: "11px",
            color: "#8f98a0",
            fontWeight: 500,
          }}
        >
          {isIndeterminate ? (
            <span>{info.phase_message || "Finalizing installation..."}</span>
          ) : (
            <>
              {info.total_bytes && info.total_bytes > 0 ? (
                <span>
                  {formatBytes(info.downloaded_bytes || 0)} /{" "}
                  {formatBytes(info.total_bytes)}
                </span>
              ) : (
                <span>{info.progress_percent.toFixed(1)}%</span>
              )}
              {info.status === "downloading" &&
                info.speed_mbps !== undefined && (
                  <span style={{ marginLeft: "auto" }}>
                    {info.speed_mbps.toFixed(1)} MB/s · ETA:{" "}
                    {formatETA(info.eta_seconds || 0)}
                  </span>
                )}
            </>
          )}
        </div>
      </div>
    );
  };

  // Handle update button click
  const handleUpdateClick = async () => {
    if (isDownloading) {
      // Cancel the update
      const dlId = downloadInfo?.id || `${gameInfo.store}:${gameInfo.game_id}`;
      const result = await call<[string], { success: boolean; error?: string }>(
        "cancel_download_by_id",
        dlId,
      );
      if (result.success) {
        setIsUpdating(false);
        toaster.toast({
          title: "Update Cancelled",
          body: `Update for ${gameInfo.title} was cancelled.`,
          duration: 5000,
        });
      }
      return;
    }

    // Amazon special case: confirm full re-download
    if (gameInfo.store === "amazon") {
      showModal(
        <ConfirmModal
          strTitle="Full Re-download Required"
          strDescription={`Amazon updates require a full re-download of ${gameInfo.title}. This may take a while. Continue?`}
          strOKButtonText="Update"
          strCancelButtonText="Cancel"
          onOK={async () => {
            setIsUpdating(true);
            const result = await call<
              [number],
              { success: boolean; error?: string }
            >("update_game", appId);
            if (!result.success) {
              setIsUpdating(false);
              toaster.toast({
                title: "Update Failed",
                body: result.error || "Failed to start update.",
                duration: 10000,
                critical: true,
              });
            }
          }}
        />,
      );
      return;
    }

    // Epic / GOG — start update directly
    setIsUpdating(true);
    const result = await call<[number], { success: boolean; error?: string }>(
      "update_game",
      appId,
    );
    if (!result.success) {
      setIsUpdating(false);
      toaster.toast({
        title: "Update Failed",
        body: result.error || "Failed to start update.",
        duration: 10000,
        critical: true,
      });
    }
  };

  // ========== UPDATE AVAILABLE / UPDATING STATE ==========
  if (
    gameInfo.is_installed &&
    (updateAvailable || isUpdating || isDownloading) &&
    !isRunning
  ) {
    return (
      <Focusable
        ref={wrapperRef}
        data-unifideck-play-wrapper="true"
        className={playSectionClassName || undefined}
        flow-children="row"
        onFocus={(e: any) =>
          e.target.scrollIntoView({ behavior: "smooth", block: "center" })
        }
        style={{
          display: "flex",
          alignItems: "center",
          width: "100%",
          padding: "16px",
          boxSizing: "border-box",
          background: "rgba(14, 20, 27, 0.33)",
          position: "relative" as const,
          zIndex: 2,
        }}
      >
        <style>{buttonStyles}</style>

        {/* Update button — acts as cancel when update is in progress */}
        <DialogButton
          className="unifideck-update-btn"
          onClick={handleUpdateClick}
          style={actionBtnStyle}
        >
          <svg viewBox="0 0 24 24" fill="currentColor" width="1em" height="1em">
            {isDownloading ? (
              /* X icon when updating (acts as cancel) */
              <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
            ) : (
              /* Sync/refresh icon when idle */
              <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z" />
            )}
          </svg>
          {isDownloading ? t("installButton.cancel", "Cancel") : "UPDATE"}
        </DialogButton>

        {/* Progress area — shows during update download or "Update Available" when idle */}
        {isDownloading && downloadInfo ? (
          renderUnifiedProgress(downloadInfo)
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              marginLeft: "20px",
              flex: "1 1 auto",
              minWidth: 0,
            }}
          >
            <div
              style={{
                fontSize: "11px",
                fontWeight: 600,
                textTransform: "uppercase",
                color: "#8f98a0",
                letterSpacing: "0.08em",
              }}
            >
              UPDATE AVAILABLE
            </div>
            <div style={{ fontSize: "14px", color: "#dcdedf" }}>
              A new version is ready to install.
            </div>
          </div>
        )}
        {/* Right icon buttons - controller config + app settings */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            marginLeft: "auto",
            flex: "0 0 auto",
          }}
        >
          <DialogButton
            onClick={() =>
              window.SteamClient?.Apps?.ShowControllerConfigurator?.(appId)
            }
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "48px",
              height: "48px",
              minWidth: "48px",
              padding: "0",
              background: "rgba(255, 255, 255, 0.1)",
              borderRadius: "4px",
            }}
          >
            <svg
              viewBox="35 31 31 24"
              fill="currentColor"
              width="28"
              height="28"
            >
              <path
                fillRule="evenodd"
                d="M38.562 35.88C37.724 37.501 36.752 41.257 36.403 44.227C35.895 48.548 36.106 49.963 37.456 51.313C39.925 53.783 41.749 53.387 43.5 50C44.938 47.219 45.452 47 50.547 47C55.406 47 56.172 47.283 57.157 49.445C58.551 52.504 60.548 53.312 63.202 51.892C65.02 50.919 65.198 50.118 64.734 44.999C64.445 41.814 63.594 37.923 62.843 36.354C61.481 33.508 61.446 33.499 50.782 33.217L40.086 32.933 38.562 35.88zM40.037 36.931C39.254 38.395 39.394 39.251 40.618 40.475C41.506 41.363 42.71 41.93 43.295 41.735C45.057 41.148 46.359 38.377 45.691 36.636C44.829 34.391 41.298 34.575 40.037 36.931zM55.445 37.174C54.533 40.048 57.439 42.371 60.138 40.926C61.162 40.378 62 39.532 62 39.047C62 34.795 56.682 33.276 55.445 37.174z"
              />
            </svg>
          </DialogButton>
          <DialogButton
            onClick={() =>
              window.SteamClient?.Apps?.OpenAppSettingsDialog?.(
                appId,
                "general",
              )
            }
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "48px",
              height: "48px",
              minWidth: "48px",
              padding: "0",
              background: "rgba(255, 255, 255, 0.1)",
              borderRadius: "4px",
            }}
          >
            <svg
              viewBox="-32 -32 64 64"
              fill="currentColor"
              width="24"
              height="24"
            >
              <path
                fillRule="evenodd"
                d="M-5.96-19.09L-5.79-26.37 5.79-26.37 5.96-19.09 9.29-17.71 14.56-22.74 22.74-14.56 17.71-9.29 19.09-5.96 26.37-5.79 26.37 5.79 19.09 5.96 17.71 9.29 22.74 14.56 14.56 22.74 9.29 17.71 5.96 19.09 5.79 26.37-5.79 26.37-5.96 19.09-9.29 17.71-14.56 22.74-22.74 14.56-17.71 9.29-19.09 5.96-26.37 5.79-26.37-5.79-19.09-5.96-17.71-9.29-22.74-14.56-14.56-22.74-9.29-17.71Z M9 0A9 9 0 1 0-9 0A9 9 0 1 0 9 0Z"
              />
            </svg>
          </DialogButton>
        </div>
      </Focusable>
    );
  }

  // ========== INSTALLED STATE: Play / Resume + X ==========
  if (gameInfo.is_installed && !isDownloading) {
    return (
      <Focusable
        ref={wrapperRef}
        data-unifideck-play-wrapper="true"
        className={playSectionClassName || undefined}
        flow-children="row"
        onFocus={(e: any) =>
          e.target.scrollIntoView({ behavior: "smooth", block: "center" })
        }
        style={{
          display: "flex",
          alignItems: "center",
          width: "100%",
          padding: "16px",
          boxSizing: "border-box",
          background: "rgba(14, 20, 27, 0.33)",
          position: "relative" as const,
          zIndex: 2,
        }}
      >
        <style>{buttonStyles}</style>

        {/* Action buttons — Play or Resume+X depending on running state */}
        {isRunning ? (
          <>
            {/* Resume button — brings running game to foreground */}
            <DialogButton
              className="unifideck-resume-btn"
              onClick={handlePlay}
              style={actionBtnStyle}
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                width="1em"
                height="1em"
              >
                <path d="M8 5v14l11-7z" />
              </svg>
              {t("installButton.resume", "Resume")}
            </DialogButton>
            {/* X close button — terminates the running game */}
            <DialogButton
              className="unifideck-stop-btn"
              onClick={handleStop}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: "48px",
                height: "48px",
                minWidth: "48px",
                padding: "0",
                borderRadius: "4px",
                border: "none",
                color: "#fff",
              }}
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                width="20"
                height="20"
              >
                <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
              </svg>
            </DialogButton>
          </>
        ) : (
          /* Play button — launches the game */
          <DialogButton
            className="unifideck-play-btn"
            onClick={handlePlay}
            style={actionBtnStyle}
          >
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              width="1em"
              height="1em"
            >
              <path d="M8 5v14l11-7z" />
            </svg>
            {t("installButton.play", "Play")}
          </DialogButton>
        )}

        {/* Last Played stat — hidden when background download is active */}
        {!isDownloading && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              marginLeft: "24px",
            }}
          >
            <div
              style={{
                fontSize: "11px",
                fontWeight: 600,
                textTransform: "uppercase",
                color: "#8f98a0",
                letterSpacing: "0.08em",
                lineHeight: "1",
                marginBottom: "5px",
              }}
            >
              LAST PLAYED
            </div>
            <div style={{ fontSize: "14px", color: "#dcdedf" }}>
              {formatLastPlayed(lastPlayedTimestamp)}
            </div>
          </div>
        )}

        {/* Unified progress bar — shown when background download/update is active */}
        {isDownloading && downloadInfo && renderUnifiedProgress(downloadInfo)}

        {/* Right icon buttons - controller config + app settings */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            marginLeft: "auto",
            flex: "0 0 auto",
          }}
        >
          <DialogButton
            onClick={() =>
              window.SteamClient?.Apps?.ShowControllerConfigurator?.(appId)
            }
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "48px",
              height: "48px",
              minWidth: "48px",
              padding: "0",
              background: "rgba(255, 255, 255, 0.1)",
              borderRadius: "4px",
            }}
          >
            <svg
              viewBox="35 31 31 24"
              fill="currentColor"
              width="28"
              height="28"
            >
              <path
                fillRule="evenodd"
                d="M38.562 35.88C37.724 37.501 36.752 41.257 36.403 44.227C35.895 48.548 36.106 49.963 37.456 51.313C39.925 53.783 41.749 53.387 43.5 50C44.938 47.219 45.452 47 50.547 47C55.406 47 56.172 47.283 57.157 49.445C58.551 52.504 60.548 53.312 63.202 51.892C65.02 50.919 65.198 50.118 64.734 44.999C64.445 41.814 63.594 37.923 62.843 36.354C61.481 33.508 61.446 33.499 50.782 33.217L40.086 32.933 38.562 35.88zM40.037 36.931C39.254 38.395 39.394 39.251 40.618 40.475C41.506 41.363 42.71 41.93 43.295 41.735C45.057 41.148 46.359 38.377 45.691 36.636C44.829 34.391 41.298 34.575 40.037 36.931zM55.445 37.174C54.533 40.048 57.439 42.371 60.138 40.926C61.162 40.378 62 39.532 62 39.047C62 34.795 56.682 33.276 55.445 37.174z"
              />
            </svg>
          </DialogButton>
          <DialogButton
            onClick={() =>
              window.SteamClient?.Apps?.OpenAppSettingsDialog?.(
                appId,
                "general",
              )
            }
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "48px",
              height: "48px",
              minWidth: "48px",
              padding: "0",
              background: "rgba(255, 255, 255, 0.1)",
              borderRadius: "4px",
            }}
          >
            <svg
              viewBox="-32 -32 64 64"
              fill="currentColor"
              width="24"
              height="24"
            >
              <path
                fillRule="evenodd"
                d="M-5.96-19.09L-5.79-26.37 5.79-26.37 5.96-19.09 9.29-17.71 14.56-22.74 22.74-14.56 17.71-9.29 19.09-5.96 26.37-5.79 26.37 5.79 19.09 5.96 17.71 9.29 22.74 14.56 14.56 22.74 9.29 17.71 5.96 19.09 5.79 26.37-5.79 26.37-5.96 19.09-9.29 17.71-14.56 22.74-22.74 14.56-17.71 9.29-19.09 5.96-26.37 5.79-26.37-5.79-19.09-5.96-17.71-9.29-22.74-14.56-14.56-22.74-9.29-17.71Z M9 0A9 9 0 1 0-9 0A9 9 0 1 0 9 0Z"
              />
            </svg>
          </DialogButton>
        </div>
      </Focusable>
    );
  }

  // ========== UNINSTALLED / DOWNLOADING STATE: Install/Cancel button ==========
  const displayText = ubisoftInstalling
    ? t("installButton.cancel")
    : isDownloading
      ? t("installButton.cancel")
      : t("installButton.installNative", "Install");

  return (
    <Focusable
      ref={wrapperRef}
      data-unifideck-play-wrapper="true"
      className={playSectionClassName || undefined}
      flow-children="row"
      onFocus={(e: any) =>
        e.target.scrollIntoView({ behavior: "smooth", block: "center" })
      }
      style={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        padding: "16px",
        boxSizing: "border-box",
        background: "rgba(14, 20, 27, 0.33)",
        position: "relative" as const,
        zIndex: 2,
      }}
    >
      <style>{buttonStyles}</style>

      {/* Install/Cancel button — explicit styling matching native Steam button */}
      <DialogButton
        className={
          isDownloading || ubisoftInstalling ? "unifideck-cancel-btn" : "unifideck-install-btn"
        }
        onClick={handleClick}
        style={actionBtnStyle}
      >
        {!isDownloading && !ubisoftInstalling && (
          <svg viewBox="0 0 24 24" fill="currentColor" width="1em" height="1em">
            <path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z" />
          </svg>
        )}
        {displayText}
      </DialogButton>

      {/* Stats or Progress - inline layout matching native spacing */}
      {isDownloading && downloadInfo ? (
        renderUnifiedProgress(downloadInfo)
      ) : (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "32px",
            marginLeft: "20px",
            flex: "0 1 auto",
          }}
        >
          <div>
            <div
              style={{
                fontSize: "11px",
                fontWeight: 600,
                textTransform: "uppercase",
                color: "#8f98a0",
                letterSpacing: "0.08em",
                lineHeight: "1",
                marginBottom: "5px",
              }}
            >
              SPACE REQUIRED
            </div>
            <div style={{ fontSize: "14px", color: "#dcdedf" }}>
              {gameInfo?.size_formatted || "\u2014"}
            </div>
          </div>
          <div>
            <div
              style={{
                fontSize: "11px",
                fontWeight: 600,
                textTransform: "uppercase",
                color: "#8f98a0",
                letterSpacing: "0.08em",
                lineHeight: "1",
                marginBottom: "5px",
              }}
            >
              LAST PLAYED
            </div>
            <div style={{ fontSize: "14px", color: "#dcdedf" }}>
              {formatLastPlayed(lastPlayedTimestamp)}
            </div>
          </div>
        </div>
      )}

      {/* Right icon buttons - controller config + app settings */}
      <div
        style={{
          display: "flex",
          gap: "8px",
          marginLeft: "auto",
          flex: "0 0 auto",
        }}
      >
        <DialogButton
          onClick={() =>
            window.SteamClient?.Apps?.ShowControllerConfigurator?.(appId)
          }
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: "48px",
            height: "48px",
            minWidth: "48px",
            padding: "0",
            background: "rgba(255, 255, 255, 0.1)",
            borderRadius: "4px",
          }}
        >
          <svg viewBox="35 31 31 24" fill="currentColor" width="28" height="28">
            <path
              fillRule="evenodd"
              d="M38.562 35.88C37.724 37.501 36.752 41.257 36.403 44.227C35.895 48.548 36.106 49.963 37.456 51.313C39.925 53.783 41.749 53.387 43.5 50C44.938 47.219 45.452 47 50.547 47C55.406 47 56.172 47.283 57.157 49.445C58.551 52.504 60.548 53.312 63.202 51.892C65.02 50.919 65.198 50.118 64.734 44.999C64.445 41.814 63.594 37.923 62.843 36.354C61.481 33.508 61.446 33.499 50.782 33.217L40.086 32.933 38.562 35.88zM40.037 36.931C39.254 38.395 39.394 39.251 40.618 40.475C41.506 41.363 42.71 41.93 43.295 41.735C45.057 41.148 46.359 38.377 45.691 36.636C44.829 34.391 41.298 34.575 40.037 36.931zM55.445 37.174C54.533 40.048 57.439 42.371 60.138 40.926C61.162 40.378 62 39.532 62 39.047C62 34.795 56.682 33.276 55.445 37.174z"
            />
          </svg>
        </DialogButton>
        <DialogButton
          onClick={() =>
            window.SteamClient?.Apps?.OpenAppSettingsDialog?.(appId, "general")
          }
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: "48px",
            height: "48px",
            minWidth: "48px",
            padding: "0",
            background: "rgba(255, 255, 255, 0.1)",
            borderRadius: "4px",
          }}
        >
          <svg
            viewBox="-32 -32 64 64"
            fill="currentColor"
            width="24"
            height="24"
          >
            <path
              fillRule="evenodd"
              d="M-5.96-19.09L-5.79-26.37 5.79-26.37 5.96-19.09 9.29-17.71 14.56-22.74 22.74-14.56 17.71-9.29 19.09-5.96 26.37-5.79 26.37 5.79 19.09 5.96 17.71 9.29 22.74 14.56 14.56 22.74 9.29 17.71 5.96 19.09 5.79 26.37-5.79 26.37-5.96 19.09-9.29 17.71-14.56 22.74-22.74 14.56-17.71 9.29-19.09 5.96-26.37 5.79-26.37-5.79-19.09-5.96-17.71-9.29-22.74-14.56-14.56-22.74-9.29-17.71Z M9 0A9 9 0 1 0-9 0A9 9 0 1 0 9 0Z"
            />
          </svg>
        </DialogButton>
      </div>
    </Focusable>
  );
};

// Error boundary: catches render-time crashes (e.g. in Steam offline mode where
// the React tree changes) and falls back to native Steam game details instead of
// blanking the entire page.
class PlaySectionErrorBoundary extends React.Component<
  { children: React.ReactNode; appId: number },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(
      `[Unifideck] PlaySectionWrapper crashed for app ${this.props.appId}:`,
      error,
      info,
    );
    // Unhide native play section so Steam's default UI shows through
    removeHidePlaySectionCDP(this.props.appId);
  }

  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

export const PlaySectionWrapper: FC<PlaySectionWrapperProps> = (props) => (
  <PlaySectionErrorBoundary appId={props.appId}>
    <PlaySectionWrapperInner {...props} />
  </PlaySectionErrorBoundary>
);

export default PlaySectionWrapper;
