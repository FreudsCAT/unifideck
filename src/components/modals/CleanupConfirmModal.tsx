/**
 * CleanupConfirmModal — confirmation before wiping every
 * Unifideck-managed shortcut, artwork, auth, and cache. Uses
 * Decky's native ConfirmModal so the destructive choice is a
 * proper modal dialog (not an inline panel toggle).
 */
import { FC, useState } from "react";
import { ConfirmModal, ToggleField } from "@decky/ui";
import { useTranslation } from "react-i18next";

interface Props {
  onConfirm: (deleteFiles: boolean) => void;
  closeModal?: () => void;
}

export const CleanupConfirmModal: FC<Props> = ({ onConfirm, closeModal }) => {
  const { t } = useTranslation();
  const [deleteFiles, setDeleteFiles] = useState(false);

  return (
    <ConfirmModal
      strTitle={t("cleanup.modalTitle")}
      strDescription={t("cleanup.modalDescription")}
      strOKButtonText={t("cleanup.confirmDelete")}
      strCancelButtonText={t("cleanup.cancel")}
      onOK={() => {
        closeModal?.();
        onConfirm(deleteFiles);
      }}
      onCancel={closeModal}
      bHideCloseIcon={false}
      bDestructiveWarning
    >
      <div
        style={{
          padding: 12,
          background: "rgba(239, 68, 68, 0.08)",
          border: "1px solid rgba(239, 68, 68, 0.3)",
          borderRadius: 8,
          marginTop: 8,
        }}
      >
        <ToggleField
          label={t("cleanup.deleteFilesLabel")}
          description={t("cleanup.deleteFilesDescription")}
          checked={deleteFiles}
          onChange={setDeleteFiles}
        />
      </div>
    </ConfirmModal>
  );
};
