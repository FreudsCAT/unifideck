/**
 * LibrarySync — sync controls + progress display.
 *
 * Two buttons (Sync, Force sync) that drive `useSync`, plus
 * a progress bar that reads `progress` from the same
 * context. The progress is reactive via the EventBus
 * (Phase F2) so no polling is needed.
 *
 * Replaces the 245L legacy LibrarySync.tsx. The reduction
 * comes from moving state into SyncContext (Phase F2).
 */
import React, { FC } from "react";
import {
  PanelSection, PanelSectionRow, ButtonItem, ProgressBarItem,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useSync } from "../../contexts/SyncContext";

/**
 * Library sync controls : Sync now, Force resync, and a
 * read-only progress block fed by SyncContext while a
 * sync is in flight.
 */
export const LibrarySync: FC = () => {
  const { t } = useTranslation();
  const sync = useSync();
  const isSyncing = sync.isSyncing;
  const isCancelling = sync.isCancelling;
  const progress = sync.progress;
  return (
    <PanelSection title={t("settings.librarySync")}>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing}
          onClick={() => void sync.startSync()}
        >
          {isSyncing ? t("sync.syncing") : t("sync.start")}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing}
          onClick={() => void sync.forceSync()}
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
                progress.current_game?.label ?? t("sync.preparing")
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
