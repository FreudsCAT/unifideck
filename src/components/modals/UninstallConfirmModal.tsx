/**
 * UninstallConfirmModal — delete confirmation for an
 * installed Unifideck game.
 *
 * Plain text confirmation : "Uninstall <Title>?". Two
 * buttons : "Uninstall" (proceeds) and "Cancel" (no-op).
 * The actual uninstall RPC call is the responsibility of
 * the parent — this modal just gates the user's intent.
 */
import React, { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Props. */
interface Props {
  gameId: number;
  gameTitle: string;
  onConfirm: () => Promise<void> | void;
  closeModal: () => void;
}

/**
 * Two-step confirmation for game uninstall : the first
 * step shows the install size that will be reclaimed,
 * the second confirms the destructive action.
 */
export const UninstallConfirmModal: FC<Props> = ({gameTitle, onConfirm, closeModal}) => {
  const { t } = useTranslation();
  return (
    <ConfirmModal
      strTitle={t("uninstall.title", { title: gameTitle })}
      strDescription={t("uninstall.body")}
      strOKButtonText={t("uninstall.confirm")}
      strCancelButtonText={t("common.cancel")}
      bAlertDialog={false}
      bDestructiveWarning={true}
      onOK={async () => {
        await onConfirm();
        closeModal();
      }}
      onCancel={() => closeModal()}
    />
  );
};
