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
import { FC } from "react";
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
  // Defensive : backend may omit any of these keys on early
  // boot or partial-failure responses. Treat missing as empty.
  const current = queue.current ?? null;
  const queued = queue.queued ?? [];
  const finished = queue.finished ?? [];
  const empty = !current && queued.length === 0 && finished.length === 0;
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
      {current && (
        <PanelSection title={t("downloads.current")}>
          <DownloadItemRow item={current} variant="current" />
        </PanelSection>
      )}
      {queued.length > 0 && (
        <PanelSection title={t("downloads.queued")}>
          {queued.map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="queued" />
          ))}
        </PanelSection>
      )}
      {finished.length > 0 && (
        <PanelSection title={t("downloads.finished")}>
          {finished.slice(-5).map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="finished" />
          ))}
        </PanelSection>
      )}
    </>
  );
};
