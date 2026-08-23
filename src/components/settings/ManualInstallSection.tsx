/**
 * ManualInstallSection — add a locally-downloaded game to the library.
 *
 * The entry point of the Manual Install flow: the user picks an
 * installer .exe from disk, confirms the game's title (pre-filled from
 * the file name — it also drives unifiDB metadata and artwork lookup),
 * and the backend takes over: it registers the game in the manual
 * store, creates its Steam shortcut, and asks the frontend to RunGame
 * the installer inside a Proton prefix (see
 * `services/manual-install-listener`). Inside the wizard, drive `D:`
 * is the game's install folder on the real filesystem.
 *
 * The file picker call mirrors `ChangeExecutableModal.browse` — no
 * RegExp `filter` (it cannot cross the JS→Python bridge), extensions
 * array drives the dropdown + server-side filter.
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

interface ManualStartResult {
  game_id?: string;
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

/** Start directory for the installer picker — the user's home. */
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

export const ManualInstallSection: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const startFlow = async (installerPath: string, title: string) => {
    try {
      const raw = await call<[string, string], unknown>(
        rpcRoutes.manualInstallStart,
        installerPath,
        title,
      );
      unwrapRpcEnvelope<ManualStartResult>(raw, {
        route: rpcRoutes.manualInstallStart,
      });
      // The backend answers with MANUAL_INSTALL_LAUNCH_REQUESTED —
      // poll fast so the installer opens without a 2s lag.
      EventBusClient.bumpToFast();
      toast.success(t("manualInstall.starting"), title);
    } catch {
      toast.error(t("manualInstall.startFailed"), title);
    }
  };

  const selectInstaller = async () => {
    setBusy(true);
    try {
      const start = await pickerStartPath();
      const res = await openFilePicker(
        FileSelectionType.FILE,
        start,
        true, // includeFiles
        true, // includeFolders (navigate into subdirectories)
        undefined, // filter — RegExp cannot cross the JS→Python bridge
        ["exe", "msi"], // extensions: dropdown + server-side filter
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
          onConfirm={(title) => void startFlow(abs, title)}
        />,
      );
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
          onClick={() => void selectInstaller()}
          disabled={busy}
          description={t("manualInstall.description")}
        >
          {t("manualInstall.selectExe")}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
