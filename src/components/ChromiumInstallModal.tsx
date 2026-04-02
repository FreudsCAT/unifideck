/**
 * ChromiumInstallModal — prompts the user to install Microsoft Edge for xCloud.
 *
 * Shown when the user tries to connect Microsoft and no compatible browser is
 * found. Offers a one-click Flatpak install with progress feedback.
 */

import { ConfirmModal, Spinner } from "@decky/ui";
import { call } from "@decky/api";
import { useState } from "react";
import { t } from "../i18n";

interface ChromiumInstallModalProps {
  closeModal: () => void;
  onInstalled: () => void;
}

export const ChromiumInstallModal = ({
  closeModal,
  onInstalled,
}: ChromiumInstallModalProps) => {
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [installed, setInstalled] = useState(false);

  const handleInstall = async () => {
    setInstalling(true);
    setError(null);

    try {
      const result = await call<
        [],
        { success: boolean; message?: string; error?: string }
      >("install_chromium");

      if (result.success) {
        setInstalled(true);
        setTimeout(() => {
          closeModal();
          onInstalled();
        }, 3000);
      } else {
        setError(result.error ? t(result.error) : t("microsoft.chromiumInstallFailed"));
      }
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setInstalling(false);
    }
  };

  return (
    <ConfirmModal
      strTitle={t("microsoft.chromiumRequired")}
      strOKButtonText={
        installed
          ? t("microsoft.chromiumInstalled")
          : installing
            ? t("microsoft.chromiumInstalling")
            : t("microsoft.chromiumInstallButton")
      }
      strCancelButtonText={t("common.cancel", "Cancel")}
      onOK={installed ? closeModal : handleInstall}
      onCancel={closeModal}
      bOKDisabled={installing}
    >
      <div style={{ padding: "8px 0" }}>
        {!installing && !installed && !error && (
          <p style={{ fontSize: "14px", lineHeight: "1.5" }}>
            {t("microsoft.chromiumRequiredMessage")}
          </p>
        )}

        {installing && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              padding: "16px 0",
            }}
          >
            <Spinner width={24} height={24} />
            <span style={{ fontSize: "14px" }}>
              {t("microsoft.chromiumInstalling")}
            </span>
          </div>
        )}

        {installed && (
          <p style={{ fontSize: "14px", color: "#4ade80" }}>
            {t("microsoft.chromiumInstalled")}
          </p>
        )}

        {error && (
          <p style={{ fontSize: "14px", color: "#ef4444" }}>
            {error}
          </p>
        )}
      </div>
    </ConfirmModal>
  );
};
