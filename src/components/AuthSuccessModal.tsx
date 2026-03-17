/**
 * AuthSuccessModal.tsx
 *
 * Full-screen modal displayed after successful store authentication.
 * Pressing B/OK closes the modal and navigates to Steam library home.
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
    // Navigate to the Steam library home
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
      strOKButtonText={t("authSuccess.close")}
      onOK={handleClose}
      onCancel={handleClose}
    />
  );
};

export default AuthSuccessModal;
