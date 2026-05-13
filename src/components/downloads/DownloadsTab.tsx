/**
 * DownloadsTab — full queue view inside the QuickAccess
 * panel.
 *
 * Three sections : "Now downloading" (current item),
 * "Queued" (waiting), "Recently finished" (last 5
 * completed). Empty state shows "No downloads".
 *
 * The data comes from `useDownloads()` (Phase F2) ; live
 * updates from EventBus mean this view re-renders
 * automatically when downloads progress, complete, fail.
 */
import React, { FC } from "react";
import { PanelSection } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useDownloads } from "../../contexts/DownloadContext";
import { DownloadItemRow } from "./DownloadItemRow";

/**
 * Quick Access Menu tab listing every active and
 * recently-finished download. Reactive on
 * DownloadContext so progress bars update in real time
 * without per-row polling.
 */
export const DownloadsTab: FC = () => {
  const { t } = useTranslation();
  const { queue, loading } = useDownloads();
  if (loading || !queue) return null;
  const empty = !queue.current
    && queue.queued.length === 0
    && queue.finished.length === 0;
  if (empty) {
    return (
      <PanelSection title={t("downloads.title")}>
        <div style={{ padding: 16, textAlign: "center", color: "#94a3b8" }}>
          {t("downloads.empty")}
        </div>
      </PanelSection>
    );
  }
  return (
    <>
      {queue.current && (
        <PanelSection title={t("downloads.current")}>
          <DownloadItemRow item={queue.current} variant="current" />
        </PanelSection>
      )}
      {queue.queued.length > 0 && (
        <PanelSection title={t("downloads.queued")}>
          {queue.queued.map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="queued" />
          ))}
        </PanelSection>
      )}
      {queue.finished.length > 0 && (
        <PanelSection title={t("downloads.finished")}>
          {queue.finished.slice(-5).map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="finished" />
          ))}
        </PanelSection>
      )}
    </>
  );
};
