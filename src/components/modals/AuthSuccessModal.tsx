/**
 * AuthSuccessModal — post-auth confirmation prompt.
 *
 * Shown after a store auth flow succeeds. The backend emits
 * `STORE_AUTH_COMPLETE` via the EventBus ; `ToastEventListener`
 * opens this modal so the user has visible confirmation that
 * the auth handshake actually persisted before the sync kicks
 * in.
 *
 * Closing the modal navigates back to the library home — the
 * legacy behaviour staging users were trained on. The default
 * Confirm/Cancel button row is hidden via inline CSS so the
 * modal feels like a passive notification, not an action prompt.
 */
import { FC } from "react";
import { ConfirmModal, Navigation } from "@decky/ui";
import { useTranslation } from "react-i18next";

interface Props {
  /** Optional store label (e.g. "Epic Games", "GOG"). */
  store?: string;
  closeModal?: () => void;
}

/**
 * Headless modal — appears after STORE_AUTH_COMPLETE and
 * routes the user back to Library Home on dismiss.
 */
export const AuthSuccessModal: FC<Props> = ({ store, closeModal }) => {
  const { t } = useTranslation();

  const handleClose = (): void => {
    closeModal?.();
    try {
      Navigation.Navigate("/library/home");
    } catch (e) {
      console.error("[AuthSuccessModal] Navigation failed:", e);
    }
  };

  return (
    <ConfirmModal
      strTitle={
        store
          ? t("authSuccess.titleStore", { store })
          : t("authSuccess.title")
      }
      strDescription={t("authSuccess.subtitle")}
      strOKButtonText={t("authSuccess.title")}
      onOK={handleClose}
      onCancel={handleClose}
      bHideCloseIcon={true}
      className="unifideck-auth-success-modal"
    >
      <style>{`
        .unifideck-auth-success-modal [class*="DialogButtons"],
        .unifideck-auth-success-modal [class*="StandardButton"],
        .unifideck-auth-success-modal [class*="DialogButton"],
        .unifideck-auth-success-modal button {
          display: none !important;
        }
      `}</style>
    </ConfirmModal>
  );
};
