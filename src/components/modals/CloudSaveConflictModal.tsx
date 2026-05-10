/**
 * CloudSaveConflictModal — picker when local and remote
 * cloud saves diverge.
 *
 * Three choices :
 *  - keepLocal  : push local → remote (overwrites cloud)
 *  - keepRemote : pull remote → local (overwrites disk)
 *  - cancel     : skip launch entirely
 *
 * Each option shows the timestamp + file count so the user
 * can make an informed decision. The actual sync RPC is the
 * caller's responsibility.
 */
import React, { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Save snapshot. */
interface SaveSnapshot {
  timestamp: number;
  file_count: number;
  total_bytes: number;
}

/** Props. */
interface Props {
  gameTitle: string;
  local: SaveSnapshot;
  remote: SaveSnapshot;
  onKeepLocal: () => Promise<void> | void;
  onKeepRemote: () => Promise<void> | void;
  onCancel: () => void;
  closeModal: () => void;
}

/** Format ts. */
function formatTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

/**
 * Cloud-save conflict resolver : displays local vs
 * remote save metadata (date, size, machine) and lets
 * the user pick which side wins. Required because
 * Unifideck stores do not all expose Steam Cloud's
 * automatic merge semantics.
 */
export const CloudSaveConflictModal: FC<Props> = ({
  gameTitle, local, remote,
  onKeepLocal, onKeepRemote, onCancel, closeModal,
}) => {
  const { t } = useTranslation();
  const description = [
    t("cloudSave.conflictHeader", { title: gameTitle }),
    "",
    t("cloudSave.local", {
      ts: formatTs(local.timestamp),
      files: local.file_count,
    }),
    t("cloudSave.remote", {
      ts: formatTs(remote.timestamp),
      files: remote.file_count,
    }),
  ].join("\n");
  return (
    <ConfirmModal
      strTitle={t("cloudSave.title")}
      strDescription={description}
      strOKButtonText={t("cloudSave.keepLocal")}
      strMiddleButtonText={t("cloudSave.keepRemote")}
      strCancelButtonText={t("common.cancel")}
      onOK={async () => {
        await onKeepLocal();
        closeModal();
      }}
      onMiddleButton={async () => {
        await onKeepRemote();
        closeModal();
      }}
      onCancel={() => {
        onCancel();
        closeModal();
      }}
    />
  );
};
