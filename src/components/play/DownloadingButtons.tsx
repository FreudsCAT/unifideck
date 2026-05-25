/**
 * DownloadingButtons — Play section variant while a download
 * is active.
 *
 * Single horizontal row inside {@link PlayShell} :
 *
 *   [ Cancel ]   Extracting · 76% · 404 MB / 564 MB · 19.6 MB/s · ETA 00:00:08
 *               ────────────────────  (progress bar)
 *
 * Indeterminate phases (``extracting`` / ``verifying``) render
 * the slide animation instead of a fractional bar so the user
 * still sees a "working" signal. Cancel opens a ``ConfirmModal``
 * (destructive) before dispatching the cancel RPC — the staging
 * UX had instant cancel which led to accidental cancellations
 * during animation flashes.
 */
import { FC, useCallback, useState } from "react";
import { DialogButton, ConfirmModal, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaTimes } from "react-icons/fa";
import { useGameActions } from "../../hooks/useGameActions";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import type { DownloadItem, DownloadPhase } from "../../types/downloads";
import { PlayShell, actionBtnStyle, formatBytes } from "./PlayMeta";

interface Props {
  download: DownloadItem;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();

function formatEta(secs: number): string {
  if (!secs || secs <= 0) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function isIndeterminate(phase: DownloadPhase | undefined): boolean {
  return phase === "extracting" || phase === "verifying";
}

function statusLabel(
  t: (k: string) => string,
  status: DownloadItem["status"],
  phase: DownloadPhase | undefined,
  phase_message: string | undefined,
): string {
  if (phase_message) return phase_message;
  if (status === "queued") return t("play.queued");
  if (phase === "extracting") return t("play.extracting");
  if (phase === "verifying") return t("play.verifying");
  if (phase === "complete") return t("play.finalizing");
  return t("play.downloading");
}

export const DownloadingButtons: FC<Props> = ({ download, bridge = defaultBridge }) => {
  const { t } = useTranslation();
  const actions = useGameActions(bridge);
  const toast = useToast();
  const [cancelled, setCancelled] = useState(false);

  const doCancel = useCallback(async () => {
    setCancelled(true);
    const result = await actions.cancel(download.id);
    if (!result?.success) {
      setCancelled(false);
      toast.error(t("toasts.cancelFailed"), result?.error ?? "");
    }
  }, [actions, download.id, t, toast]);

  const onCancelClick = useCallback(() => {
    if (cancelled) return;
    showModal(
      <ConfirmModal
        strTitle={t("play.cancelConfirmTitle")}
        strDescription={t("play.cancelConfirmBody", { title: download.game_title })}
        strOKButtonText={t("play.cancelConfirmConfirm")}
        strCancelButtonText={t("play.cancelConfirmCancel")}
        bDestructiveWarning
        onOK={() => { void doCancel(); }}
      />,
    );
  }, [cancelled, doCancel, download.game_title, t]);

  const indeterminate = isIndeterminate(download.download_phase);
  const pct = Math.max(0, Math.min(100, download.progress_percent));
  const label = statusLabel(t, download.status, download.download_phase, download.phase_message);

  return (
    <PlayShell>
      <DialogButton
        className="unifideck-cancel-btn"
        disabled={cancelled || actions.isWorking}
        onClick={onCancelClick}
        style={actionBtnStyle}
      >
        <FaTimes />
        {cancelled ? t("play.cancelling") : t("play.cancel")}
      </DialogButton>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
          marginLeft: 20,
          flex: "1 1 auto",
          minWidth: 0,
        }}
      >
        <div style={{ fontSize: 13, color: "#dcdedf" }}>
          <span style={{ fontWeight: 600 }}>{label}</span>
          {!indeterminate && download.progress_percent > 0 && (
            <> &nbsp;·&nbsp; {pct.toFixed(1)}%</>
          )}
        </div>
        <div
          style={{
            height: 4,
            background: "rgba(255, 255, 255, 0.08)",
            borderRadius: 2,
            overflow: "hidden",
            position: "relative",
          }}
        >
          {indeterminate ? (
            <div
              style={{
                position: "absolute",
                inset: 0,
                width: "40%",
                background: "linear-gradient(90deg, transparent 0%, #1a9fff 50%, transparent 100%)",
                animation: "unifideck-slide 1.5s linear infinite",
              }}
            />
          ) : (
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: "linear-gradient(90deg, #1a9fff 0%, #1570b5 100%)",
                transition: "width 0.3s ease",
                borderRadius: 2,
              }}
            />
          )}
        </div>
        <div style={{ fontSize: 11, color: "#8f98a0", letterSpacing: "0.02em" }}>
          {formatBytes(download.downloaded_bytes)} / {formatBytes(download.total_bytes)}
          {download.speed_mbps > 0 && (
            <> &nbsp;·&nbsp; {download.speed_mbps.toFixed(1)} MB/s</>
          )}
          {download.eta_seconds > 0 && (
            <> &nbsp;·&nbsp; ETA {formatEta(download.eta_seconds)}</>
          )}
        </div>
      </div>
    </PlayShell>
  );
};
