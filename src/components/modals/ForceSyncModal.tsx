/**
 * ForceSyncModal — confirmation before a destructive force-sync.
 *
 * `forceSync()` discards cached library state and re-fetches
 * every entry from every connected store. Optionally it also
 * re-downloads all artwork (slow, bandwidth-heavy). This modal
 * exposes both options so users don't accidentally trigger the
 * artwork re-sync.
 *
 * Pure presentational : the actual sync RPC is the caller's
 * responsibility — this modal only collects the user's choice
 * between two button actions.
 */
import { FC } from "react";
import { ConfirmModal, DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaImage, FaSync } from "react-icons/fa";

interface Props {
  onResyncArtwork: () => void;
  onKeepArtwork: () => void;
  closeModal?: () => void;
}

/**
 * Two-button picker : "Resync everything (including artwork)"
 * vs "Resync library, keep artwork". The native ConfirmModal
 * footer is hidden so only the two stacked DialogButtons are
 * actionable.
 */
export const ForceSyncModal: FC<Props> = ({
  onResyncArtwork,
  onKeepArtwork,
  closeModal,
}) => {
  const { t } = useTranslation();

  return (
    <>
      <style>{`
        .unifideck-force-sync-modal + div { display: none !important; }
        .unifideck-force-sync-modal .DialogFooter { display: none !important; }
      `}</style>
      <ConfirmModal
        strTitle={t("confirmModals.forceSyncTitle")}
        strDescription=""
        bHideCloseIcon={false}
        onOK={closeModal}
        onCancel={closeModal}
      >
        <div
          className="unifideck-force-sync-modal"
          style={{ padding: "10px 0" }}
        >
          <div
            style={{
              marginBottom: 20,
              color: "#cbd5e1",
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            {t("confirmModals.forceSyncDescription")}
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            <DialogButton
              onClick={() => {
                closeModal?.();
                onResyncArtwork();
              }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                color: "#ef4444",
              }}
            >
              <FaImage /> {t("confirmModals.resyncArtwork")}
            </DialogButton>
            <DialogButton
              onClick={() => {
                closeModal?.();
                onKeepArtwork();
              }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
              }}
            >
              <FaSync /> {t("confirmModals.keepArtwork")}
            </DialogButton>
          </div>
        </div>
      </ConfirmModal>
    </>
  );
};
