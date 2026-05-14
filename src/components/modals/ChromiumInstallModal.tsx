/**
 * ChromiumInstallModal — Microsoft Edge prereq installer.
 *
 * Browser-based OAuth (Epic / GOG / Amazon / Microsoft) needs
 * the ``com.microsoft.Edge`` flatpak installed before the
 * launcher can open the auth URL. When `store_auth` returns
 * ``error: "edge_not_installed"`` the frontend spawns this
 * modal so the user can install Edge with a single click
 * instead of dropping into a terminal.
 *
 * The "Install" button calls the `install_edge` RPC
 * (proxied by `EdgeRPCMixin` to `MicrosoftStore.install_edge`,
 * which delegates to `EdgeInstaller`). The flatpak install
 * takes 30–90 s on a fresh prefix — the spinner stays up the
 * whole time. On success, the modal closes and invokes
 * `onInstalled()` so the caller can retry the original auth
 * flow automatically.
 *
 * Restored from the staging branch (`src893/staging`) where it
 * lived before the F1-F8 refactor erroneously dropped it.
 */
import React, { FC, useState } from "react";
import { ConfirmModal, Spinner } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { call } from "@decky/api";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { useToast } from "../../hooks/useToast";

interface Props {
  /** Optional callback after a successful install — typically
   *  the original auth flow that triggered the modal. */
  onInstalled?: () => void;
  closeModal?: () => void;
}

interface InstallEdgeResponse {
  success: boolean;
  error?: string;
}

/** Three-state install UI : idle (Install / Cancel), in-flight
 *  (Spinner), and result (toast + close). Buttons are disabled
 *  while a call is in flight so the user can't double-click. */
export const ChromiumInstallModal: FC<Props> = ({
  onInstalled, closeModal,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const [installing, setInstalling] = useState(false);

  const handleInstall = async (): Promise<void> => {
    setInstalling(true);
    try {
      const raw = await call<[], unknown>(rpcRoutes.installEdge);
      const result = unwrapRpcEnvelope<InstallEdgeResponse>(raw, {
        route: rpcRoutes.installEdge, throwing: false,
      });
      if (result?.success) {
        toast.success(t("microsoft.browserInstalled"));
        closeModal?.();
        onInstalled?.();
      } else {
        toast.error(
          t("microsoft.chromiumInstallFailed"),
          result?.error ?? "",
        );
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(t("microsoft.chromiumInstallFailed"), message);
    } finally {
      setInstalling(false);
    }
  };

  return (
    <ConfirmModal
      strTitle={t("microsoft.chromiumRequired")}
      strDescription={t("microsoft.chromiumRequiredMessage")}
      strOKButtonText={
        installing
          ? t("microsoft.chromiumInstalling")
          : t("microsoft.chromiumInstallButton")
      }
      strCancelButtonText={t("common.cancel")}
      onOK={installing ? () => {} : handleInstall}
      onCancel={installing ? () => {} : closeModal}
      bHideCloseIcon={installing}
    >
      {installing && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: 12,
            color: "#cbd5e1",
          }}
        >
          <Spinner />
          <span>{t("microsoft.chromiumInstalling")}</span>
        </div>
      )}
    </ConfirmModal>
  );
};
