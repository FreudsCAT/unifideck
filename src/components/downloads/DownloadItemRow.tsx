/**
 * DownloadItemRow — single row in the downloads queue.
 *
 * Three variants :
 *  - "current"  : shows progress bar + speed + ETA + cancel
 *  - "queued"   : title + position + cancel-from-queue
 *  - "finished" : title + outcome (success / cancelled /
 *                 error) + remove-from-history
 *
 * The variant is passed by the parent (DownloadsTab) and
 * picks the appropriate rendering. All three variants share
 * the same StoreIcon + title prefix.
 */
import { FC } from "react";
import { ButtonItem, ProgressBarItem } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameActions } from "../../hooks/useGameActions";
import { SteamBridge } from "../../lib/steam-bridge";
import { StoreIcon } from "../shared/StoreIcon";
import type { DownloadItem } from "../../types/downloads";

/** Props. */
interface Props {
  item: DownloadItem;
  variant: "current" | "queued" | "finished";
}

const bridge = new SteamBridge();

/**
 * One row of the downloads tab. Renders the artwork,
 * progress bar, status badge, action buttons. Variants
 * (`current` / `queued` / `finished`) tweak the layout
 * and which actions are exposed.
 */
export const DownloadItemRow: FC<Props> = ({ item, variant }) => {
  const { t } = useTranslation();
  const actions = useGameActions(bridge);
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 4, padding: 6,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <StoreIcon store={item.store} size={14} />
        <span style={{ flex: 1, fontWeight: 500 }}>{item.game_title}</span>
        {variant === "finished" && (
          <span style={{
            fontSize: 11, padding: "2px 6px", borderRadius: 3,
            background: item.status === "completed" ? "#22c55e" :
                        item.status === "cancelled" ? "#94a3b8" : "#ef4444",
            color: "#0f172a",
          }}>
            {t(`downloads.outcome.${item.status}`)}
          </span>
        )}
      </div>
      {variant === "current" && (
        <>
          <ProgressBarItem
            nProgress={item.progress_percent}
            indeterminate={false}
            description={
              `${item.speed_mbps.toFixed(1)} MB/s · ETA ${item.eta_seconds}s`
            }
          />
          <ButtonItem
            layout="below"
            disabled={actions.isWorking}
            onClick={() => void actions.cancel(item.id)}
          >
            {t("downloads.cancel")}
          </ButtonItem>
        </>
      )}
      {variant === "queued" && (
        <ButtonItem
          layout="below"
          disabled={actions.isWorking}
          onClick={() => void actions.cancel(item.id)}
        >
          {t("downloads.removeFromQueue")}
        </ButtonItem>
      )}
    </div>
  );
};
