/**
 * ManualInstallExeModal — pick the game's executable after installing.
 *
 * Shown when the installer app exits and the game's record is still
 * pending (and re-shown after ANY later run while it stays pending —
 * see `services/manual-install-listener`). Instead of a blind file
 * browser, the backend scans the game's install dir (drive `D:`) AND
 * the prefix's `drive_c` (the user may have installed onto `C:`) and
 * the modal lists the candidates — one tap for the common case,
 * whichever location the wizard targeted. "Browse…" remains as the
 * fallback; "Later" keeps the record pending and the prompt returns
 * after the next run.
 */
import { FC, useState } from "react";
import { ConfirmModal, DialogButton, Focusable } from "@decky/ui";
import { call, openFilePicker, FileSelectionType } from "@decky/api";
import { useTranslation } from "react-i18next";
import { FaFolderOpen } from "react-icons/fa";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope, useRPCQuery } from "../../api/useRPC";
import { useToast } from "../../hooks/useToast";
import {
  ensureManualShortcut,
  offerRestartIfTileMissing,
} from "../../lib/manual-restart";

interface ExeCandidate {
  path: string;
  rel: string;
  name: string;
  in_prefix: boolean;
}

interface CandidatesPayload {
  install_path?: string;
  candidates?: ExeCandidate[];
}

interface Props {
  gameId: string;
  gameTitle: string;
  installPath: string;
  closeModal?: () => void;
  /** Invoked whenever the modal goes away (picked, Later, or browse
   *  success) — the listener uses it to release its re-prompt guard. */
  onClosed?: () => void;
}

const candidateRowStyle = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  width: "100%",
} as const;

export const ManualInstallExeModal: FC<Props> = ({
  gameId,
  gameTitle,
  installPath,
  closeModal,
  onClosed,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const scan = useRPCQuery<[string], CandidatesPayload>(
    rpcRoutes.manualExeCandidates,
    [gameId],
  );
  const candidates = scan.data?.candidates ?? [];

  // Whether the user picked the exe or pressed "Later", the persistent
  // shortcut is re-written first (Steam's temp-shortcut flushes erase
  // it — see lib/manual-restart) and then the restart is offered so the
  // tile appears (skipped automatically when it is already live).
  const dismiss = () => {
    onClosed?.();
    closeModal?.();
    void ensureManualShortcut(gameId).finally(() =>
      offerRestartIfTileMissing(gameId),
    );
  };

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
      dismiss();
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
        scan.data?.install_path || installPath,
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
      onOK={dismiss}
      onCancel={dismiss}
    >
      <div style={{ marginBottom: 12, opacity: 0.8, fontSize: "0.9em" }}>
        {t("manualInstall.exeModalSubtitle")}
      </div>

      {scan.loading && <div>{t("common.loading")}</div>}
      {!scan.loading && candidates.length === 0 && (
        <div style={{ marginBottom: 8, opacity: 0.7, fontSize: "0.9em" }}>
          {t("manualInstall.exeModalNoCandidates")}
        </div>
      )}

      <Focusable style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {candidates.map((c) => (
          <DialogButton
            key={c.path}
            disabled={busy}
            onClick={() => void finalize(c.path)}
            style={{ padding: "8px 12px" }}
          >
            <div style={candidateRowStyle}>
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {c.name}
                <span
                  style={{
                    opacity: 0.5,
                    marginInlineStart: 8,
                    fontSize: "0.8em",
                  }}
                >
                  {c.in_prefix ? `C: ${c.rel}` : c.rel}
                </span>
              </span>
            </div>
          </DialogButton>
        ))}
      </Focusable>

      <Focusable style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <DialogButton disabled={busy} onClick={() => void browse()}>
          <FaFolderOpen style={{ marginInlineEnd: 8 }} />
          {t("manualInstall.exeModalBrowse")}
        </DialogButton>
      </Focusable>
    </ConfirmModal>
  );
};
