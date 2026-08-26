/**
 * ManualInstallSection — add local games to the library, two ways.
 *
 * INSTALL — pick a setup .exe/.msi: the backend registers the game and
 * asks the frontend to RunGame the installer inside a Proton prefix
 * (see `services/manual-install-listener`); drive `D:` inside the
 * wizard is the game's folder on the real filesystem, and when the
 * installer exits the user picks the game's executable.
 *
 * IMPORT — pick the executable of a game that is ALREADY installed:
 * the record is born ready and the game launches once automatically as
 * a verification run (creating its Proton prefix, so later runs are
 * instant). The files stay exactly where they are (uninstall never
 * deletes a user-managed folder).
 *
 * Both start with the file picker (the `ChangeExecutableModal.browse`
 * contract — no RegExp `filter`, it cannot cross the JS→Python bridge)
 * and the title-confirmation modal, since the title drives unifiDB
 * metadata, artwork lookup and the shortcut's name.
 */
import { FC, useState } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  showModal,
} from "@decky/ui";
import { call, openFilePicker, FileSelectionType } from "@decky/api";
import { useTranslation } from "react-i18next";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { useToast } from "../../hooks/useToast";
import { EventBusClient } from "../../api/event-bus-client";
import { ManualInstallTitleModal } from "../modals/ManualInstallTitleModal";

interface BrowseableDevices {
  devices?: Array<{ path?: string }>;
}

/** A readable title guess from an installer file name:
 *  "setup_dark_forest-1.2.exe" → "Dark Forest 1.2". */
export function suggestTitleFromFile(fileName: string): string {
  const stem = fileName.replace(/\.(exe|msi)$/i, "");
  const words = stem
    .replace(/[-_.+]+/g, " ")
    .replace(/\b(setup|install|installer)\b/gi, " ")
    .replace(/\s{2,}/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean);
  return words
    .map((w) => (/[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Start directory for the pickers — the user's home. */
async function pickerStartPath(): Promise<string> {
  try {
    const raw = await call<[], unknown>(rpcRoutes.getBrowseableDevices);
    const data = unwrapRpcEnvelope<BrowseableDevices>(raw, {
      route: rpcRoutes.getBrowseableDevices,
      throwing: false,
    });
    const home = data?.devices?.[0]?.path;
    if (home) return home;
  } catch {
    // fall through to the root fallback
  }
  return "/";
}

/** Shared picker → title-modal front half of both flows. Resolves with
 *  nothing; the confirmed (path, title) pair goes to `onConfirmed`. */
async function pickAndConfirm(
  extensions: string[],
  onConfirmed: (path: string, title: string) => void,
): Promise<void> {
  const start = await pickerStartPath();
  const res = await openFilePicker(
    FileSelectionType.FILE,
    start,
    true, // includeFiles
    true, // includeFolders (navigate into subdirectories)
    undefined, // filter — RegExp cannot cross the JS→Python bridge
    extensions, // dropdown + server-side filter
    false, // showHiddenFiles
    true, // allowAllFiles
  );
  const abs = res?.realpath || res?.path;
  if (!abs) return;
  const fileName = abs.split("/").pop() ?? abs;
  showModal(
    <ManualInstallTitleModal
      installerPath={abs}
      suggestedTitle={suggestTitleFromFile(fileName)}
      onConfirm={(title) => onConfirmed(abs, title)}
    />,
  );
}

export const ManualInstallSection: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const startInstall = async (installerPath: string, title: string) => {
    try {
      const raw = await call<[string, string], unknown>(
        rpcRoutes.manualInstallStart,
        installerPath,
        title,
      );
      unwrapRpcEnvelope(raw, { route: rpcRoutes.manualInstallStart });
      // The backend answers with MANUAL_INSTALL_LAUNCH_REQUESTED —
      // poll fast so the installer opens without a 2s lag.
      EventBusClient.bumpToFast();
      toast.success(t("manualInstall.starting"), title);
    } catch {
      toast.error(t("manualInstall.startFailed"), title);
    }
  };

  const startImport = async (exePath: string, title: string) => {
    try {
      const raw = await call<[string, string], unknown>(
        rpcRoutes.manualImport,
        exePath,
        title,
      );
      unwrapRpcEnvelope(raw, { route: rpcRoutes.manualImport });
      // The backend answers with MANUAL_INSTALL_LAUNCH_REQUESTED — the
      // game launches once to verify it works (and to create its
      // prefix); poll fast so it opens without a 2s lag. The restart
      // prompt comes after that run ends (manual-install-listener).
      EventBusClient.bumpToFast();
      toast.success(t("manualInstall.importStarting"), title);
    } catch {
      toast.error(t("manualInstall.importFailed"), title);
    }
  };

  const run = async (flow: () => Promise<void>) => {
    setBusy(true);
    try {
      await flow();
    } catch {
      // user cancelled the picker — no-op
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelSection title={t("manualInstall.title")}>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() =>
            void run(() =>
              pickAndConfirm(
                ["exe", "msi"],
                (path, title) => void startInstall(path, title),
              ),
            )
          }
          disabled={busy}
          description={t("manualInstall.description")}
        >
          {t("manualInstall.installButton")}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() =>
            void run(() =>
              pickAndConfirm(
                ["exe"],
                (path, title) => void startImport(path, title),
              ),
            )
          }
          disabled={busy}
          description={t("manualInstall.importDescription")}
        >
          {t("manualInstall.importButton")}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
