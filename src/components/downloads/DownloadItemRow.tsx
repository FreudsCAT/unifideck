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
import { ButtonItem } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameActions } from "../../hooks/useGameActions";
import { SteamBridge } from "../../lib/steam-bridge";
import { StoreIcon } from "../shared/StoreIcon";
import type { DownloadItem, DownloadStatus } from "../../types/downloads";
import { DownloadProgressRow } from "./DownloadProgressRow";

/** Map backend ``DownloadStatus`` to the i18n outcome key
 *  (kept stable across 14 locale files). Backend says
 *  ``"complete"``/``"failed"``/``"running"`` while the i18n
 *  bundle uses ``"completed"``/``"error"``/``"downloading"`` —
 *  the rename would touch every locale, so we adapt instead. */
function outcomeKey(status: DownloadStatus): string {
  if (status === "complete") return "completed";
  if (status === "failed") return "error";
  if (status === "running") return "downloading";
  return status;
}

function outcomeColor(status: DownloadStatus): string {
  if (status === "complete") return "#22c55e";
  if (status === "cancelled") return "#94a3b8";
  return "#ef4444";
}

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
      display: "flex", flexDirection: "column", gap: 6, padding: 6,
    }}>
      {/* Title row — full width, never competes with badges or
          status text. The QAM panel is narrow, so leaving room
          for the title to lay out on one or two lines (instead of
          wrapping around a sidebar badge) is the readability win. */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6, minWidth: 0,
      }}>
        <StoreIcon store={item.store} size={14} />
        <span style={{
          flex: 1, fontWeight: 500, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis",
        }}>
          {item.game_title}
        </span>
      </div>

      {variant === "finished" && (
        <span style={{
          alignSelf: "flex-start",
          fontSize: 11, padding: "2px 6px", borderRadius: 3,
          background: outcomeColor(item.status),
          color: "#0f172a",
        }}>
          {t(`downloads.outcome.${outcomeKey(item.status)}`)}
        </span>
      )}

      {variant === "current" && (
        <>
          <DownloadProgressRow download={item} />
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
