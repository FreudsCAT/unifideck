import { FC, useState, useEffect, useMemo, useRef } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Dropdown,
  showModal,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { call } from "@decky/api";
import { useRPCQuery } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";
import { ReleaseNotesModal } from "../modals/ReleaseNotesModal";

interface ReleaseInfo {
  version: string;
  prerelease: boolean;
  asset_url: string;
  sha256: string;
  body: string;
}

const compareVersions = (a: string, b: string) => {
  const parse = (v: string) => v.split(".").map((x) => parseInt(x, 10) || 0);
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const na = pa[i] || 0;
    const nb = pb[i] || 0;
    if (na > nb) return 1;
    if (na < nb) return -1;
  }
  return 0;
};

export const PluginUpdater: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const [selectedVersion, setSelectedVersion] = useState<string>("");
  const [installing, setInstalling] = useState(false);
  const [checking, setChecking] = useState(false);

  // Fetch updates status
  const { data: updateData, loading: checkingOnMount, refetch: checkUpdate } = useRPCQuery<
    [],
    { available: boolean; current: string; latest: ReleaseInfo | null }
  >(rpcRoutes.checkPluginUpdate, []);

  // Fetch available versions
  const { data: versionsData, loading: loadingVersions, refetch: refetchVersions } = useRPCQuery<
    [],
    ReleaseInfo[]
  >(rpcRoutes.getAvailableVersions, []);

  const currentVersion = updateData?.current ?? "0.0.0";
  const initializedRef = useRef(false);

  // Set default selected version when data loads
  useEffect(() => {
    if (updateData && versionsData && !initializedRef.current) {
      const hasCurrent = versionsData.some((v) => v.version === currentVersion);
      if (hasCurrent) {
        setSelectedVersion(currentVersion);
      } else if (updateData.latest) {
        setSelectedVersion(updateData.latest.version);
      } else if (versionsData.length > 0) {
        setSelectedVersion(versionsData[0].version);
      }
      initializedRef.current = true;
    }
  }, [updateData, versionsData, currentVersion]);

  // Format dropdown options
  const versionOptions = useMemo(() => {
    if (!versionsData) return [];
    return versionsData.map((v) => {
      let label = `v${v.version}`;
      if (v.version === currentVersion) {
        label += ` (${t("updater.installedLabel", { defaultValue: "installed" })})`;
      } else if (updateData?.latest?.version === v.version) {
        label += ` (${t("updater.latestLabel", { defaultValue: "latest" })})`;
      }

      if (v.prerelease) {
        label += " [DEV]";
      }

      return {
        data: v.version,
        label,
      };
    });
  }, [versionsData, currentVersion, updateData, t]);

  const selectedRelease = useMemo(() => {
    if (!versionsData) return null;
    return versionsData.find((v) => v.version === selectedVersion) || null;
  }, [versionsData, selectedVersion]);

  const handleVersionSelect = (opt: any) => {
    setSelectedVersion(String(opt.data));
  };

  const handleCheckUpdate = async () => {
    setChecking(true);
    try {
      await Promise.all([checkUpdate(), refetchVersions()]);
      toast.success(
        t("updater.checkCompleteTitle", { defaultValue: "Update Check Complete" }),
        t("updater.checkCompleteMessage", { defaultValue: "Successfully fetched latest version info." })
      );
    } catch (e: any) {
      toast.error(
        t("updater.checkFailedTitle", { defaultValue: "Check Failed" }),
        e?.message ?? t("errors.unknown")
      );
    } finally {
      setChecking(false);
    }
  };

  const handleShowReleaseNotes = () => {
    if (!selectedRelease) return;
    showModal(
      <ReleaseNotesModal
        version={selectedVersion}
        body={selectedRelease.body}
      />
    );
  };

  const handleInstall = async () => {
    if (!selectedRelease) return;

    setInstalling(true);
    try {
      const cmp = compareVersions(selectedVersion, currentVersion);
      let installType = 2; // UPDATE
      let typeLabel = t("updater.typeUpdate", { defaultValue: "Updating to" });

      if (cmp === 0) {
        installType = 1; // REINSTALL
        typeLabel = t("updater.typeReinstall", { defaultValue: "Reinstalling" });
      } else if (cmp < 0) {
        installType = 3; // DOWNGRADE
        typeLabel = t("updater.typeDowngrade", { defaultValue: "Downgrading to" });
      }

      toast.info(
        t("updater.installingTitle", { defaultValue: "Installing Plugin" }),
        `${typeLabel} v${selectedVersion}...`
      );

      // Trigger Decky's native install helper
      try {
        await call<[string, string, string, string | boolean, number], unknown>(
          "utilities/install_plugin",
          selectedRelease.asset_url,
          "Unifideck",
          selectedRelease.version,
          selectedRelease.sha256 || false,
          installType
        );
      } catch (e) {
        // Ignored because reload will drop the websocket connection, throwing an error
        console.log("[Updater] Connection reset due to plugin reload/install:", e);
      }
    } catch (e: any) {
      toast.error(
        t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
        e?.message ?? t("errors.unknown")
      );
      setInstalling(false);
    }
  };

  const isLoading = checkingOnMount || loadingVersions;

  // Render header title
  const sectionTitle = useMemo(() => {
    if (isLoading) {
      return `${t("updater.titleLoading", { defaultValue: "Checking version" })}...`;
    }
    return `${t("updater.currentTitle", { defaultValue: "Current" })} - v${currentVersion}`;
  }, [currentVersion, isLoading, t]);

  return (
    <PanelSection title={sectionTitle}>
      {isLoading ? (
        <PanelSectionRow>
          <div style={{ textAlign: "center", padding: "10px", opacity: 0.6 }}>
            {t("common.loading", { defaultValue: "Loading..." })}
          </div>
        </PanelSectionRow>
      ) : (
        <>
          {versionOptions.length > 0 && (
            <PanelSectionRow>
              <Dropdown
                rgOptions={versionOptions}
                selectedOption={selectedVersion}
                onChange={handleVersionSelect}
                disabled={installing || checking}
              />
            </PanelSectionRow>
          )}

          {selectedVersion && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={handleInstall}
                disabled={installing || checking}
              >
                {installing
                  ? t("updater.installingButton", { defaultValue: "Installing..." })
                  : selectedVersion === currentVersion
                  ? t("updater.reinstallButton", { defaultValue: `Reinstall v${selectedVersion}` })
                  : compareVersions(selectedVersion, currentVersion) < 0
                  ? t("updater.downgradeButton", { defaultValue: `Downgrade to v${selectedVersion}` })
                  : t("updater.updateButton", { defaultValue: `Update to v${selectedVersion}` })}
              </ButtonItem>
            </PanelSectionRow>
          )}

          {selectedRelease && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={handleShowReleaseNotes}
                disabled={installing || checking}
              >
                {t("updater.releaseNotesButton", { defaultValue: "Release Notes" })}
              </ButtonItem>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleCheckUpdate}
              disabled={installing || checking}
            >
              {checking
                ? t("updater.checkingButton", { defaultValue: "Checking..." })
                : t("updater.checkButton", { defaultValue: "Check for Updates" })}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
};
