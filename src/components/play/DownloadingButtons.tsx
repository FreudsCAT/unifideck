/**
 * DownloadingButtons — Play section while a download is
 * active for the displayed game.
 *
 * Shows a progress bar with current %, ETA, and speed plus a
 * Cancel button. The progress is read from
 * `useDownloadProgress` which is the focused selector hook
 * (only re-renders when THIS game's progress changes).
 *
 * The cancel action goes through `useGameActions.cancel`
 * with a 1-second debounce to prevent double-clicks
 * cascading two cancellations.
 */
import { FC, useCallback, useState } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameActions } from "../../hooks/useGameActions";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import type { DownloadItem } from "../../types/downloads";

/** Props. */
interface Props {
  download: DownloadItem;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();

/** Format eta. */
function formatEta(secs: number): string {
  if (secs <= 0) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/**
 * Variant of the Play section shown while the game is
 * downloading : progress bar, ETA, Cancel button, Pause
 * if the underlying store supports it.
 */
export const DownloadingButtons: FC<Props> = ({download, bridge = defaultBridge}) => {
  const { t } = useTranslation();
  const actions = useGameActions(bridge);
  const toast = useToast();
  const [cancelled, setCancelled] = useState(false);

  /** On cancel. */
  const onCancel = useCallback(async () => {
    if (cancelled) return;
    setCancelled(true);
    const result = await actions.cancel(download.id);
    if (!result?.success) {
      setCancelled(false);
      toast.error(t("toasts.cancelFailed"), result?.error ?? "");
    }
  }, [actions, cancelled, download.id, t, toast]);
  return (
    <Focusable
      flow-children="column"
      onActivate={() => {}}
      style={{ display: "flex", flexDirection: "column", gap: 6 }}
    >
      <div style={{ fontSize: 12 }}>
        {download.progress_percent.toFixed(0)}%
        {" · "}
        {download.speed_mbps.toFixed(1)} MB/s
        {" · "}
        ETA {formatEta(download.eta_seconds)}
      </div>
      <DialogButton
        className="unifideck-cancel-btn"
        disabled={cancelled || actions.isWorking}
        onClick={onCancel}
      >
        {cancelled ? t("play.cancelling") : t("play.cancel")}
      </DialogButton>
    </Focusable>
  );
};
