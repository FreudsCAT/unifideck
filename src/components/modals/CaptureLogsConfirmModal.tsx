/**
 * CaptureLogsConfirmModal — privacy disclosure shown before a
 * diagnostic bundle is written.
 *
 * Not destructive: it only creates a new zip in Downloads. The modal
 * exists because we are about to nudge the user into uploading that zip
 * to a public bug-report channel, and the bundle carries their game
 * titles and install paths. Consent for that should be affirmative and
 * per-run, not buried in a panel subtitle — and the panel row is far too
 * narrow to state what is and, more importantly, is not included.
 *
 * Structure mirrors CleanupConfirmModal, minus the destructive styling.
 * `onOK` closes the modal *before* invoking the callback, so the
 * caller's "started" toast has to be deferred — see
 * CaptureLogsSection.runCapture.
 */
import { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

interface Props {
  onConfirm: () => void;
  closeModal?: () => void;
}

export const CaptureLogsConfirmModal: FC<Props> = ({
  onConfirm,
  closeModal,
}) => {
  const { t } = useTranslation();

  return (
    <ConfirmModal
      strTitle={t("captureLogs.modalTitle")}
      strDescription={t("captureLogs.modalDescription")}
      strOKButtonText={t("captureLogs.modalConfirm")}
      strCancelButtonText={t("captureLogs.cancel")}
      onOK={() => {
        closeModal?.();
        onConfirm();
      }}
      onCancel={closeModal}
      bHideCloseIcon={false}
    >
      <div
        style={{
          padding: 12,
          background: "rgba(59, 130, 246, 0.08)",
          border: "1px solid rgba(59, 130, 246, 0.3)",
          borderRadius: 8,
          marginTop: 8,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          fontSize: 13,
        }}
      >
        <span>{t("captureLogs.modalIncludes")}</span>
        <span>{t("captureLogs.modalExcludes")}</span>
      </div>
    </ConfirmModal>
  );
};
