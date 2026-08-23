/**
 * ManualInstallExeModal — pick the game's executable after installing.
 *
 * Shown by the manual-install listener when the installer app exits and
 * the game's record is still pending. The picker starts in the game's
 * install directory (the folder mapped as drive `D:` inside the
 * wizard); if the user installed onto `C:` instead, they can navigate
 * into the prefix from there. "Later" keeps the record pending —
 * pressing Play re-runs the installer and this modal comes back when it
 * exits.
 */
import { FC, useState } from "react";
import { ConfirmModal, DialogButton, Focusable, showModal } from "@decky/ui";
import { call, openFilePicker, FileSelectionType } from "@decky/api";
import { useTranslation } from "react-i18next";
import { FaFolderOpen } from "react-icons/fa";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { useToast } from "../../hooks/useToast";
import { SteamRestartModal } from "./SteamRestartModal";

interface Props {
  gameId: string;
  gameTitle: string;
  installPath: string;
  closeModal?: () => void;
}

export const ManualInstallExeModal: FC<Props> = ({
  gameId,
  gameTitle,
  installPath,
  closeModal,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const finalize = async (exePath: string) => {
    setBusy(true);
    try {
      const raw = await call<[string, string], unknown>(
        rpcRoutes.manualInstallFinalize,
        gameId,
        exePath,
      );
      unwrapRpcEnvelope(raw, { route: rpcRoutes.manualInstallFinalize });
      toast.success(t("manualInstall.ready"), gameTitle);
      closeModal?.();
      // The shortcut was written to shortcuts.vdf this session, so the
      // library tile only appears after Steam reloads the file.
      showModal(<SteamRestartModal />);
    } catch {
      toast.error(t("manualInstall.finalizeFailed"), exePath);
    } finally {
      setBusy(false);
    }
  };

  const browse = async () => {
    try {
      // Same picker contract as ChangeExecutableModal: no RegExp
      // filter (it cannot cross the JS→Python bridge); the extensions
      // array drives the dropdown + server-side filter.
      const res = await openFilePicker(
        FileSelectionType.FILE,
        installPath,
        true, // includeFiles
        true, // includeFolders (navigate into subdirectories)
        undefined, // filter — see note above
        ["exe"], // extensions
        false, // showHiddenFiles
        true, // allowAllFiles
      );
      const abs = res?.realpath || res?.path;
      if (abs) await finalize(abs);
    } catch {
      // user cancelled the picker — modal stays open
    }
  };

  return (
    <ConfirmModal
      strTitle={t("manualInstall.exeModalTitle", { game: gameTitle })}
      bAlertDialog
      strOKButtonText={t("manualInstall.exeModalLater")}
      onOK={() => closeModal?.()}
      onCancel={() => closeModal?.()}
    >
      <div style={{ marginBottom: 12, opacity: 0.8, fontSize: "0.9em" }}>
        {t("manualInstall.exeModalSubtitle")}
      </div>
      <Focusable style={{ display: "flex", gap: 8 }}>
        <DialogButton disabled={busy} onClick={() => void browse()}>
          <FaFolderOpen style={{ marginInlineEnd: 8 }} />
          {t("manualInstall.exeModalBrowse")}
        </DialogButton>
      </Focusable>
    </ConfirmModal>
  );
};
