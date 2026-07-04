/**
 * CloudSaveConflictModal — picker when local and remote
 * cloud saves diverge.
 *
 * Three choices : keepLocal (push), keepRemote (pull),
 * cancel (skip launch). Visual treatment ported from staging:
 * a yellow warning header, a side-by-side comparison panel
 * with desktop/cloud icons + timestamps + "newer" highlight,
 * and two prominent DialogButtons.
 */
import { FC } from "react";
import { ConfirmModal, DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaCloud, FaDesktop, FaExclamationTriangle } from "react-icons/fa";

interface SaveSnapshot {
  timestamp: number;
  file_count: number;
  total_bytes: number;
}

interface Props {
  gameTitle: string;
  local: SaveSnapshot;
  remote: SaveSnapshot;
  onKeepLocal: () => Promise<void> | void;
  onKeepRemote: () => Promise<void> | void;
  onCancel: () => void;
  closeModal: () => void;
}

function formatTs(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i > 0 && value < 10 ? 1 : 0)} ${units[i]}`;
}

export const CloudSaveConflictModal: FC<Props> = ({
  gameTitle,
  local,
  remote,
  onKeepLocal,
  onKeepRemote,
  onCancel,
  closeModal,
}) => {
  const { t } = useTranslation();
  const localNewer = local.timestamp >= remote.timestamp;
  return (
    <ConfirmModal
      strTitle={t("cloudSave.title")}
      strDescription=""
      onOK={closeModal}
      onCancel={() => {
        onCancel();
        closeModal();
      }}
    >
      <div style={{ padding: "10px 0" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 15,
            color: "#ffc107",
          }}
        >
          <FaExclamationTriangle size={24} />
          <span style={{ fontSize: 14 }}>
            {t("cloudSave.description", { game: gameTitle })}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            marginBottom: 20,
            backgroundColor: "rgba(255,255,255,0.05)",
            padding: 15,
            borderRadius: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <FaDesktop size={16} /> <span>{t("cloudSave.local")}</span>
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-end",
              }}
            >
              <span
                style={{
                  color: localNewer ? "#4caf50" : "#888",
                  fontWeight: localNewer ? "bold" : "normal",
                }}
              >
                {formatTs(local.timestamp)} {localNewer && t("cloudSave.newer")}
              </span>
              <span style={{ fontSize: 12, color: "#aaa" }}>
                {t("cloudSave.filesSize", {
                  count: local.file_count ?? 0,
                  size: formatBytes(local.total_bytes ?? 0),
                })}
              </span>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <FaCloud size={16} /> <span>{t("cloudSave.cloud")}</span>
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-end",
              }}
            >
              <span
                style={{
                  color: !localNewer ? "#4caf50" : "#888",
                  fontWeight: !localNewer ? "bold" : "normal",
                }}
              >
                {formatTs(remote.timestamp)}{" "}
                {!localNewer && t("cloudSave.newer")}
              </span>
              <span style={{ fontSize: 12, color: "#aaa" }}>
                {t("cloudSave.filesSize", {
                  count: remote.file_count ?? 0,
                  size: formatBytes(remote.total_bytes ?? 0),
                })}
              </span>
            </div>
          </div>
        </div>
        <div
          style={{
            display: "flex",
            gap: 10,
            justifyContent: "center",
            marginBottom: 15,
          }}
        >
          <DialogButton
            onClick={async () => {
              await onKeepRemote();
              closeModal();
            }}
            style={{
              minWidth: 140,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            <FaCloud /> {t("cloudSave.useCloud")}
          </DialogButton>
          <DialogButton
            onClick={async () => {
              await onKeepLocal();
              closeModal();
            }}
            style={{
              minWidth: 140,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            <FaDesktop /> {t("cloudSave.useLocal")}
          </DialogButton>
        </div>
      </div>
    </ConfirmModal>
  );
};
