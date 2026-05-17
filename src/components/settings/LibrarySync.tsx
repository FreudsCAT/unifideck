/**
 * LibrarySync — sync controls + staging-style progress display.
 *
 * Ported from ``staging:src/components/settings/LibrarySync.tsx``.
 * While a sync is in flight the component shows:
 *  - A colour-coded progress bar (blue=sync, orange=artwork/metadata, green=complete)
 *  - The current phase label via i18n (e.g. "Syncing Epic Games…")
 *  - Per-phase X / Y counter lines (games synced, artwork downloaded, metadata extracted)
 *
 * Progress is read from `SyncContext` reactively — the 500ms polling
 * loop inside the provider feeds ``sync.progress``.
 */
import React, { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaSync } from "react-icons/fa";
import { useSync } from "../../contexts/SyncContext";
import { useSyncCooldown } from "../../hooks/useSyncCooldown";
import { ForceSyncModal } from "../modals/ForceSyncModal";

export const LibrarySync: FC = () => {
  const { t } = useTranslation();
  const sync = useSync();
  const cooldown = useSyncCooldown();
  const isSyncing = sync.isSyncing;
  const isCancelling = sync.isCancelling;
  const progress = sync.progress;

  const onForceSync = (): void => {
    showModal(
      <ForceSyncModal
        onResyncArtwork={() => void sync.forceSync(true)}
        onKeepArtwork={() => void sync.forceSync(false)}
        closeModal={() => {}}
      />,
    );
  };

  const isArtwork = progress?.status === "artwork";
  const isComplete = progress?.status === "complete";
  const isError = progress?.status === "error";
  const barColor = isError ? "#ff6b6b"
    : isComplete ? "#4caf50"
    : isArtwork ? "#ff9800"
    : "#1a9fff";

  return (
    <PanelSection title={t("librarySync.title")}>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing || !cooldown.canSync}
          onClick={() => void sync.startSync()}
        >
          <div style={{
            display: "flex", alignItems: "center",
            gap: 8, justifyContent: "center",
          }}>
            <FaSync style={{
              animation: isSyncing ? "spin 1s linear infinite" : "none",
              opacity: cooldown.canSync ? 1 : 0.5,
            }} />
            {isSyncing
              ? t("librarySync.syncing")
              : !cooldown.canSync
                ? `${cooldown.remainingSecs}s`
                : t("librarySync.syncLibraries")}
          </div>
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing || !cooldown.canSync}
          onClick={onForceSync}
        >
          <div style={{
            display: "flex", alignItems: "center",
            gap: 8, justifyContent: "center",
          }}>
            <FaSync style={{ opacity: cooldown.canSync ? 1 : 0.5 }} />
            {isSyncing ? "…" : t("librarySync.forceSync")}
          </div>
        </ButtonItem>
      </PanelSectionRow>
      {isSyncing && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void sync.cancelSync()}
            disabled={isCancelling}>
            {isCancelling
              ? t("librarySync.cancelling", "Cancelling…")
              : t("librarySync.cancelSync")}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {progress && progress.status !== "idle" && (
        <div style={{ fontSize: 12, width: "100%" }}>
          <div style={{ marginBottom: 5, opacity: 0.9 }}>
            {progress.current_game?.label
              ? t(progress.current_game.label, progress.current_game.values ?? {})
              : "…"}
          </div>
          <div style={{
            width: "100%", height: 4,
            backgroundColor: "#333", borderRadius: 2, overflow: "hidden",
          }}>
            <div style={{
              width: `${progress.progress_percent ?? 0}%`,
              height: "100%",
              backgroundColor: barColor,
              transition: "width 0.3s ease",
            }} />
          </div>
          <div style={{ marginTop: 5, opacity: 0.7 }}>
            {isArtwork ? (
              <>
                <div>
                  {t("librarySync.artworkDownloaded", {
                    synced: progress.artwork_synced ?? 0,
                    total: progress.artwork_total ?? 0,
                  })}
                </div>
              </>
            ) : progress.status === "metadata" ? (
              <>
                <div>
                  {t("librarySync.metadataExtracted", {
                    synced: progress.metacritic_synced ?? 0,
                    total: progress.metacritic_total ?? 0,
                  })}
                </div>
              </>
            ) : (
              <>
                <div>
                  {t("librarySync.gamesSynced", {
                    synced: progress.synced_games ?? 0,
                    total: progress.total_games ?? 0,
                  })}
                </div>
                {(progress.steam_total ?? 0) > 0 && (
                  <div>
                    {t("librarySync.steamMetadataDownloaded", {
                      synced: progress.steam_synced ?? 0,
                      total: progress.steam_total ?? 0,
                    })}
                  </div>
                )}
              </>
            )}
          </div>
          {progress.error && (
            <div style={{ color: "#ff6b6b", marginTop: 5 }}>
              {t("librarySync.error")}: {progress.error}
            </div>
          )}
        </div>
      )}
    </PanelSection>
  );
};
