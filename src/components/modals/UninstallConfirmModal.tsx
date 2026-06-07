/**
 * UninstallConfirmModal — delete confirmation for an
 * installed Unifideck game, with optional Proton-prefix wipe.
 *
 * Ported from staging: rich body with a description, a
 * ToggleField for "also delete Proton prefix", a conditional
 * red warning when the toggle is on, and two custom DialogButtons
 * (Cancel + red Uninstall with trash icon). The parent receives
 * the toggle state via `onConfirm(deletePrefix)`.
 */
import { FC, useState } from "react";
import { ConfirmModal, DialogButton, ToggleField } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaExclamationTriangle, FaTrash } from "react-icons/fa";

interface Props {
  gameId: number;
  gameTitle: string;
  onConfirm: (deletePrefix: boolean) => Promise<void> | void;
  closeModal: () => void;
}

export const UninstallConfirmModal: FC<Props> = ({
  gameId: _gameId,
  gameTitle,
  onConfirm,
  closeModal,
}) => {
  const { t } = useTranslation();
  const [deletePrefix, setDeletePrefix] = useState(false);
  return (
    <>
      <style>{`
        .unifideck-uninstall-modal + div { display: none !important; }
        .DialogFooter { display: none !important; }
      `}</style>
      <ConfirmModal
        strTitle={t("uninstallModal.title")}
        strDescription=""
        bHideCloseIcon={false}
        onOK={closeModal}
        onCancel={closeModal}
      >
        <div
          className="unifideck-uninstall-modal"
          style={{ padding: "10px 0" }}
        >
          <div
            style={{
              marginBottom: 16,
              color: "#ccc",
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            {t("uninstallModal.description", { title: gameTitle })}
          </div>
          <div
            style={{
              marginBottom: 12,
              padding: 12,
              backgroundColor: "rgba(0, 0, 0, 0.2)",
              borderRadius: 8,
            }}
          >
            <ToggleField
              label={t("uninstallModal.deleteProtonLabel")}
              description={t("uninstallModal.deleteProtonDescription")}
              checked={deletePrefix}
              onChange={setDeletePrefix}
            />
          </div>
          {deletePrefix && (
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
                padding: 12,
                backgroundColor: "rgba(239, 68, 68, 0.15)",
                borderRadius: 8,
                marginBottom: 16,
                border: "1px solid rgba(239, 68, 68, 0.3)",
              }}
            >
              <FaExclamationTriangle
                style={{ color: "#ef4444", marginTop: 2, flexShrink: 0 }}
              />
              <div style={{ color: "#fca5a5", fontSize: 13, lineHeight: 1.4 }}>
                <strong>{t("uninstallModal.warningTitle")}</strong>{" "}
                {t("uninstallModal.warningBody")}
              </div>
            </div>
          )}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <DialogButton onClick={closeModal} style={{ minWidth: 100 }}>
              {t("uninstallModal.cancel")}
            </DialogButton>
            <DialogButton
              onClick={async () => {
                closeModal();
                await onConfirm(deletePrefix);
              }}
              style={{
                minWidth: 100,
                color: "#ef4444",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
              }}
            >
              <FaTrash /> {t("uninstallModal.uninstall")}
            </DialogButton>
          </div>
        </div>
      </ConfirmModal>
    </>
  );
};
