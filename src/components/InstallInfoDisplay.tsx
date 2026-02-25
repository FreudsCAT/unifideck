import React, { FC, useState, useEffect, useRef } from "react";
import { DialogButton, showModal } from "@decky/ui";
import { call, toaster } from "@decky/api";
import { useTranslation } from "react-i18next";
import StoreIcon from "./StoreIcon";
import { UninstallConfirmModal } from "./UninstallConfirmModal";
import { updateSingleGameStatus } from "../tabs";

// Cache ref — set from index.tsx during init via setInstallInfoCacheRef()
let gameInfoCacheRef: Map<number, { info: any; timestamp: number }> | null =
  null;
const CACHE_TTL = 5000;

export function setInstallInfoCacheRef(
  cache: Map<number, { info: any; timestamp: number }>,
) {
  gameInfoCacheRef = cache;
}

// View mode constants (same as GameInfoPanel — component isolation)
const GAME_DETAILS_VIEW_MODE_KEY = "unifideck-game-details-view-mode";
const VIEW_MODE_CHANGE_EVENT = "unifideck-view-mode-change";
type GameDetailsViewMode = "simple" | "detailed";

const getStoredViewMode = (): GameDetailsViewMode => {
  try {
    const stored = localStorage.getItem(GAME_DETAILS_VIEW_MODE_KEY);
    return stored === "simple" ? "simple" : "detailed";
  } catch {
    return "detailed";
  }
};

// Install Info Display Component - shows download size next to play section
const InstallInfoDisplayInner: FC<{ appId: number }> = ({ appId }) => {
  const [gameInfo, setGameInfo] = useState<any>(null);
  const [processing, setProcessing] = useState(false);
  const [downloadState, setDownloadState] = useState<{
    isDownloading: boolean;
    progress?: number;
    downloadId?: string;
  }>({ isDownloading: false });
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Track view mode with event listener for live updates
  const [viewMode, setViewMode] = useState<GameDetailsViewMode>(
    getStoredViewMode(),
  );

  useEffect(() => {
    const handleViewModeChange = (e: Event) => {
      const mode = (e as CustomEvent).detail as GameDetailsViewMode;
      setViewMode(mode);
    };
    window.addEventListener(VIEW_MODE_CHANGE_EVENT, handleViewModeChange);
    return () =>
      window.removeEventListener(VIEW_MODE_CHANGE_EVENT, handleViewModeChange);
  }, []);

  const { t } = useTranslation();

  // Fetch game info on mount
  useEffect(() => {
    const cached = gameInfoCacheRef?.get(appId);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
      console.log("[InstallInfoDisplay] Using cached game info:", cached.info);
      setGameInfo(cached.info);
      return;
    }

    call<[number], any>("get_game_info", appId)
      .then((info) => {
        console.log("[InstallInfoDisplay] Fetched game info:", info);
        const processedInfo = info?.error ? null : info;
        setGameInfo(processedInfo);
        gameInfoCacheRef?.set(appId, {
          info: processedInfo,
          timestamp: Date.now(),
        });
      })
      .catch(() => setGameInfo(null));
  }, [appId]);

  // Poll for download state when we have game info
  useEffect(() => {
    if (!gameInfo) return;

    const checkDownloadState = async () => {
      try {
        const result = await call<
          [string, string],
          {
            success: boolean;
            is_downloading: boolean;
            download_info?: {
              id: string;
              progress_percent: number;
              status: string;
            };
          }
        >("is_game_downloading", gameInfo.game_id, gameInfo.store);

        setDownloadState((prevState) => {
          const newState = {
            isDownloading: false,
            progress: 0,
            downloadId: undefined as string | undefined,
          };

          if (result.success && result.is_downloading && result.download_info) {
            const status = result.download_info.status;
            // Only show as downloading if status is actively downloading or queued
            // Cancelled/error items should not be shown as active downloads
            if (status === "downloading" || status === "queued") {
              newState.isDownloading = true;
              newState.progress = result.download_info.progress_percent;
              newState.downloadId = result.download_info.id;
            }
            // If status is cancelled/error/completed, isDownloading stays false
          }

          // Detect transition from Downloading -> Not Downloading (Completion)
          if (prevState.isDownloading && !newState.isDownloading) {
            console.log(
              "[InstallInfoDisplay] Download stopped, checking status...",
            );

            // Check the status from the download info to determine actual completion
            // result.download_info might be available even if is_downloading is false
            const finalStatus = result.download_info?.status;

            if (finalStatus === "completed") {
              console.log(
                "[InstallInfoDisplay] Download successfully finished",
              );

              // Show installation complete toast
              toaster.toast({
                title: t("toasts.installComplete"),
                body: t("toasts.installCompleteMessage", {
                  title: gameInfo?.title || "Game",
                }),
                duration: 10000,
                critical: true,
              });

              // Invalidate cache first to ensure fresh data
              gameInfoCacheRef?.delete(appId);

              // Refresh game info to update button state (Install -> Play/Uninstall)
              call<[number], any>("get_game_info", appId).then((info) => {
                const processedInfo = info?.error ? null : info;
                setGameInfo(processedInfo);
                if (processedInfo) {
                  gameInfoCacheRef?.set(appId, {
                    info: processedInfo,
                    timestamp: Date.now(),
                  });
                  // Update tab cache immediately so UI reflects change
                  updateSingleGameStatus({
                    appId,
                    store: processedInfo.store,
                    isInstalled: processedInfo.is_installed,
                  });
                }
              });
            } else if (finalStatus === "cancelled") {
              console.log(
                "[InstallInfoDisplay] Download was cancelled - suppressing success message",
              );
            } else if (finalStatus === "error") {
              console.log(
                "[InstallInfoDisplay] Download failed - suppressing success message",
              );
            } else {
              // Fallback: If no status info (legacy behavior or edge case), verify installation again
              console.log(
                "[InstallInfoDisplay] No final status, verifying installation...",
              );
              call<[number], any>("get_game_info", appId).then((info) => {
                if (info && info.is_installed) {
                  // It is installed, likely success
                  toaster.toast({
                    title: t("toasts.installComplete"),
                    body: t("toasts.installCompleteMessage", {
                      title: gameInfo?.title || "Game",
                    }),
                    duration: 10000,
                  });
                  setGameInfo(info);
                }
              });
            }
          }

          return newState;
        });
      } catch (error) {
        console.error(
          "[InstallInfoDisplay] Error checking download state:",
          error,
        );
      }
    };

    // Initial check
    checkDownloadState();

    // Poll every second when displaying
    pollIntervalRef.current = setInterval(checkDownloadState, 1000);

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [gameInfo, appId]);

  const handleUninstall = async (deletePrefix: boolean = false) => {
    if (!gameInfo) return;
    setProcessing(true);

    toaster.toast({
      title: t("toasts.uninstalling"),
      body: deletePrefix
        ? t("toasts.uninstallingMessageProton", { title: gameInfo.title })
        : t("toasts.uninstallingMessage", { title: gameInfo.title }),
      duration: 5000,
    });

    const result = await call<[number, boolean], any>(
      "uninstall_game_by_appid",
      appId,
      deletePrefix,
    );

    if (result.success) {
      setGameInfo({ ...gameInfo, is_installed: false });
      gameInfoCacheRef?.delete(appId);

      // Update tab cache immediately so UI reflects change without restart
      if (result.game_update) {
        updateSingleGameStatus(result.game_update);
      }

      toaster.toast({
        title: t("toasts.uninstallComplete"),
        body: deletePrefix
          ? t("toasts.uninstallCompleteMessageProton", {
              title: gameInfo.title,
            })
          : t("toasts.uninstallCompleteMessage", { title: gameInfo.title }),
        duration: 10000,
      });
    }
    // Note: Failure case removed - current logic handles all edge cases:
    // 1. Missing game files -> updates flag to not installed
    // 2. Missing mapping -> updates flag to not installed
    // 3. User clicks uninstall -> removes all flags/files
    setProcessing(false);
  };

  const showUninstallConfirmation = () => {
    showModal(
      <UninstallConfirmModal
        gameTitle={gameInfo?.title || "this game"}
        onConfirm={(deletePrefix) => handleUninstall(deletePrefix)}
      />,
    );
  };

  // Not a Unifideck game - return null
  if (!gameInfo || gameInfo.error) return null;

  const isInstalled = gameInfo.is_installed;

  // Install/Cancel buttons have been moved to PlayButtonOverride (in the play section).
  // This component now only shows:
  // - Uninstall button (for installed games, in simple view mode)
  // - Nothing for uninstalled games (install is handled by PlayButtonOverride)

  // Base button style for uninstall button
  const baseButtonStyle: React.CSSProperties = {
    padding: "10px 16px",
    minHeight: "44px",
    minWidth: "180px",
    fontSize: "14px",
    fontWeight: 600,
    borderRadius: "4px",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    opacity: 1,
    visibility: "visible",
    backdropFilter: "none",
    WebkitBackdropFilter: "none",
  };

  // CSS for controller focus state
  const focusStyles = `
    .unifideck-install-button.gpfocus,
    .unifideck-install-button:hover {
      filter: brightness(1.2) !important;
      box-shadow: 0 0 12px rgba(26, 159, 255, 0.8) !important;
      transform: scale(1.02);
      transition: all 0.15s ease;
    }
    .unifideck-install-button.gpfocus {
      outline: 2px solid #1a9fff !important;
      outline-offset: 2px !important;
    }
  `;

  // In "detailed" mode, the info is shown in GameInfoPanel
  if (viewMode === "detailed") {
    return null;
  }

  // For uninstalled games or active downloads, show nothing here
  // (Install/Cancel is now in PlayButtonOverride in the play section)
  if (!isInstalled || downloadState.isDownloading) {
    return null;
  }

  // Simple mode: Show Uninstall button for installed games
  const sizeText = gameInfo.size_formatted
    ? ` (${gameInfo.size_formatted})`
    : " (- GB)";
  const buttonText =
    t("installButton.uninstall", { title: gameInfo.title }) + sizeText;

  return (
    <>
      <style>{focusStyles}</style>
      <div
        style={{
          position: "absolute",
          top: "40px",
          right: "35px",
          zIndex: 9999,
        }}
        className="unifideck-install-button-container"
      >
        <DialogButton
          onClick={showUninstallConfirmation}
          disabled={processing}
          style={{
            ...baseButtonStyle,
            backgroundColor: "#d32f2f",
            color: "#ffffff",
            border: "2px solid #ff6b6b",
            boxShadow: "0 2px 8px rgba(211, 47, 47, 0.5)",
          }}
          className="unifideck-install-button"
        >
          {processing ? (
            t("installButton.processing")
          ) : (
            <>
              <StoreIcon store={gameInfo.store} size="16px" color="#ffffff" />
              {buttonText}
            </>
          )}
        </DialogButton>
      </div>
    </>
  );
};

// Error boundary: catches render-time crashes (e.g. in Steam offline mode where
// the React tree changes) and falls back to native Steam game details instead of
// blanking the entire page.
class InstallInfoErrorBoundary extends React.Component<
  { children: React.ReactNode; appId: number },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(
      `[Unifideck] InstallInfoDisplay crashed for app ${this.props.appId}:`,
      error,
      info,
    );
  }

  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

const InstallInfoDisplay: FC<{ appId: number }> = (props) => (
  <InstallInfoErrorBoundary appId={props.appId}>
    <InstallInfoDisplayInner {...props} />
  </InstallInfoErrorBoundary>
);

export default InstallInfoDisplay;
