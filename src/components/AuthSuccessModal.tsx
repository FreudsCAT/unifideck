/**
 * AuthSuccessModal.tsx
 *
 * Full-screen modal displayed after successful store authentication.
 * Pressing B closes the modal and navigates to Steam library home.
 * No visible buttons — B is the only interaction.
 *
 * Reusable for any store (Microsoft, Epic, GOG, Amazon).
 * Uses the i18n system for translations (14 locales).
 */

import { FC } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmModal, Navigation } from "@decky/ui";

interface AuthSuccessModalProps {
  /** Store name for display (e.g. "Microsoft Store") */
  store?: string;
  /** Called by Decky when the modal is dismissed */
  closeModal?: () => void;
}

export const AuthSuccessModal: FC<AuthSuccessModalProps> = ({
  store,
  closeModal,
}) => {
  const { t } = useTranslation();

  const handleClose = () => {
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
      className="auth-success-modal"
    >
      <style>{`
        .auth-success-modal [class*="DialogButtons"],
        .auth-success-modal [class*="StandardButton"],
        .auth-success-modal [class*="DialogButton"],
        .auth-success-modal button {
          display: none !important;
        }
      `}</style>
    </ConfirmModal>
  );
};

export default AuthSuccessModal;
