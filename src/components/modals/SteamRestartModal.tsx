/**
 * SteamRestartModal — prompts the user to restart Steam.
 *
 * Shown after a flow that writes to shortcuts.vdf : Steam
 * caches the file at startup, so changes only become visible
 * post-restart. The modal explains why and offers a
 * SteamClient.User.StartShutdown call (Steam's own restart
 * path). Restart-on-demand: never restart automatically.
 */
import React, { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Props. */
interface Props {
  closeModal?: () => void;
}

/**
 * Modal shown after operations that require Steam to
 * restart for changes to apply (shortcut creation, VDF
 * mutation). Offers a Restart-now button that calls
 * `SteamClient.User.StartRestart`.
 */
export const SteamRestartModal: FC<Props> = ({ closeModal }) => {
  const { t } = useTranslation();
  return (
    <ConfirmModal
      strTitle={t("restart.title")}
      strDescription={t("restart.body")}
      strOKButtonText={t("restart.confirm")}
      strCancelButtonText={t("restart.later")}
      onOK={() => {
        // SteamClient.User.StartShutdown is observed-not-typed
        const sc = (window as { SteamClient?: {
          User?: { StartShutdown?: (force: boolean) => void };
        } }).SteamClient;
        sc?.User?.StartShutdown?.(false);
        closeModal?.();
      }}
      onCancel={() => closeModal?.()}
    />
  );
};
