import { FC } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmModal } from "@decky/ui";

interface SteamRestartModalProps {
  store?: string;
  reason?: "sync" | "cleanup";
  closeModal?: () => void;
}

export const SteamRestartModal: FC<SteamRestartModalProps> = ({
  store,
  reason = "sync",
  closeModal,
}) => {
  const { t } = useTranslation();

  const handleRestartNow = () => {
    closeModal?.();

    // Use StartShutdown instead of StartRestart (safer and works better)
    // On Steam Deck, gamescope-session will automatically restart Steam
    // See: https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/steamfixes/README.md
    // StartRestart() breaks CEF debugging, StartShutdown(false) doesn't
    if (window.SteamClient?.User?.StartShutdown) {
      console.log("[SteamRestartModal] Restarting Steam via StartShutdown");
      window.SteamClient.User.StartShutdown(false);
    } else {
      console.error(
        "[SteamRestartModal] SteamClient.User.StartShutdown not available",
      );
    }
  };

  const title =
    reason === "cleanup"
      ? t("confirmModals.steamRestartCleanupTitle")
      : t("confirmModals.steamRestartTitle");

  const description =
    reason === "cleanup"
      ? t("confirmModals.steamRestartCleanupDescription")
      : store
        ? t("confirmModals.steamRestartDescriptionStore", { store })
        : t("confirmModals.steamRestartDescription");

  return (
    <ConfirmModal
      strTitle={title}
      strDescription={description}
      strOKButtonText={t("confirmModals.restartNow")}
      strCancelButtonText={t("confirmModals.later")}
      onOK={handleRestartNow}
      onCancel={closeModal}
    />
  );
};

export default SteamRestartModal;
