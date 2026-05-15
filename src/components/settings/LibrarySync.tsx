/**
 * LibrarySync — sync controls + progress display.
 *
 * Three primary actions :
 *   - Sync now       → `useSync.startSync()`
 *   - Force resync   → opens `<ForceSyncModal>` (resync artwork
 *                      vs keep), then dispatches `forceSync(bool)`
 *   - Cancel         → `useSync.cancelSync()`, shown only while
 *                      a sync is in flight.
 *
 * Progress is read from `SyncContext` reactively (no polling).
 * A cooldown timer (via `useSyncCooldown`) blocks the Sync
 * button for a short window after each completed run so users
 * can't hammer the manual button.
 */
import React, { FC } from "react";
import {
  PanelSection, PanelSectionRow, ButtonItem, ProgressBarItem,
  showModal,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useSync } from "../../contexts/SyncContext";
import { useSyncCooldown } from "../../hooks/useSyncCooldown";
import { ForceSyncModal } from "../modals/ForceSyncModal";

/**
 * Library sync controls : Sync now, Force resync, and a
 * read-only progress block fed by SyncContext while a
 * sync is in flight.
 */
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

  return (
    <PanelSection title={t("settings.librarySync")}>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing || !cooldown.canSync}
          onClick={() => void sync.startSync()}
        >
          {isSyncing
            ? t("sync.syncing")
            : !cooldown.canSync
              ? t("sync.cooldown", { secs: cooldown.remainingSecs })
              : t("sync.start")}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing}
          onClick={onForceSync}
        >
          {t("sync.force")}
        </ButtonItem>
      </PanelSectionRow>
      {isSyncing && progress && (
        <>
          <PanelSectionRow>
            <ProgressBarItem
              nProgress={progress.progress_percent}
              indeterminate={false}
              description={
                progress.current_phase === "artwork"
                  ? t("sync.artworkPhase", {
                    done: progress.artwork_synced ?? 0,
                    total: progress.artwork_total ?? 0,
                  })
                  : progress.current_game?.label ?? t("sync.preparing")
              }
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={isCancelling}
              onClick={() => void sync.cancelSync()}
            >
              {isCancelling ? t("sync.cancelling") : t("sync.cancel")}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
};
