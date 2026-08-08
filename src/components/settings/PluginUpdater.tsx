import { FC, useState, useEffect, useMemo, useRef } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Dropdown,
  ProgressBarWithInfo,
  showModal,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { call } from "@decky/api";
import { useRPCQuery, useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";
import { ReleaseNotesModal } from "../modals/ReleaseNotesModal";

interface ReleaseInfo {
  tag: string;
  version: string;
  prerelease: boolean;
  asset_url: string;
  asset_name: string;
  sha256: string;
  body: string;
}

// Decky PluginInstallType (backend enums.py / browser.py PluginInstallType)
const INSTALL_TYPE_REINSTALL = 1;
const INSTALL_TYPE_UPDATE = 2;
const INSTALL_TYPE_DOWNGRADE = 3;

// window.DeckyBackend lives on whichever window actually created this
// document. In Desktop Mode's full-page Decky Settings route, that's
// this window directly. In Gaming Mode, this panel renders inside the
// Quick Access Menu's own popup window (opened via window.open by
// Big Picture Mode) — DeckyBackend is undefined on that popup's own
// `window`, but reachable via `window.opener`. Falling back silently
// means every install/update button appears to do nothing in Gaming
// Mode's QAM while working fine in Desktop Mode, which is how this
// went unnoticed.
const getDeckyBackend = (): Window["DeckyBackend"] | null =>
  window.DeckyBackend ?? window.opener?.DeckyBackend ?? null;

// If Decky's own loader install dies silently (e.g. a 404 on a rotated
// dev-build asset — confirmed in journalctl: the browser CRITICAL "Could
// not fetch from URL" is followed by zero further progress/finish events),
// downloadActive would otherwise stay true forever. This is an inactivity
// timeout reset on every progress tick, not a single fixed deadline, so a
// legitimately slow ~40-50MB download over Wi-Fi isn't falsely flagged.
const INSTALL_WATCHDOG_TIMEOUT_MS = 45_000;

// Parses the maintainer's dev-build filename convention (e.g.
// "unifideck.dev.0.7.1.g3f9a1c2.zip", or the legacy
// "unifideck.dev.v524.zip") into a display-friendly build id. Returns
// null when asset_name is absent or doesn't match — callers fall back
// to the generic "vDev" label in that case (e.g. a release built
// before this feature shipped, or a malformed manual upload).
const DEV_ASSET_NAME_RE = /^unifideck\.dev\.(.+)\.zip$/i;
const extractDevBuildId = (assetName: string | undefined): string | null => {
  if (!assetName) return null;
  const m = assetName.match(DEV_ASSET_NAME_RE);
  return m ? m[1] : null;
};

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

interface InstallAction {
  installType: number;
  displayVersion: string;
}

// Single source of truth for "is installing this release an update,
// downgrade, or reinstall relative to what's currently running" — used
// by both the install button's label and the actual install trigger,
// which used to each compute this independently and could disagree.
//
// Dev/prerelease releases are handled separately from stable ones:
// dev builds are deliberately cut BEFORE package.json's version bumps
// (see build-plugin.sh), so their parsed `version` is always the raw
// non-semver tag ("Dev-<date>-<time>-<sha>") — running that through the
// numeric compareVersions() above always parses to 0, which is <= any real
// release and would misreport EVERY dev install as a "downgrade"
// regardless of how new the underlying code actually is. There is no
// meaningful downgrade concept for a prerelease: it's a "Reinstall"
// only when its build id matches what's already running (currentBuildId),
// and an "Update" otherwise.
const resolveInstallAction = (
  release: ReleaseInfo,
  currentVersion: string,
  currentBuildId: string | null,
): InstallAction => {
  if (release.prerelease) {
    const devBuildId = extractDevBuildId(release.asset_name);
    return {
      installType:
        devBuildId !== null && devBuildId === currentBuildId
          ? INSTALL_TYPE_REINSTALL
          : INSTALL_TYPE_UPDATE,
      displayVersion: devBuildId ?? release.version,
    };
  }
  const cmp = compareVersions(release.version, currentVersion);
  return {
    installType:
      cmp === 0
        ? INSTALL_TYPE_REINSTALL
        : cmp < 0
        ? INSTALL_TYPE_DOWNGRADE
        : INSTALL_TYPE_UPDATE,
    displayVersion: release.version,
  };
};

// Map Decky's download_progress_info.* keys to short, human status text.
const stageLabel = (key: string | undefined, t: TFunction): string => {
  const suffix = (key ?? "").split(".").pop() ?? "";
  switch (suffix) {
    case "start":
      return t("updater.stageStart", { defaultValue: "Starting…" });
    case "download_zip":
    case "increment_count":
      return t("updater.stageDownload", { defaultValue: "Downloading…" });
    case "open_zip":
      return t("updater.stageOpen", { defaultValue: "Reading package…" });
    case "parse_zip":
      return t("updater.stageParse", { defaultValue: "Verifying…" });
    case "uninstalling_previous":
      return t("updater.stageRemove", {
        defaultValue: "Removing old version…",
      });
    case "installing_plugin":
      return t("updater.stageInstall", { defaultValue: "Installing…" });
    case "download_remote":
      return t("updater.stageFinish", { defaultValue: "Finishing…" });
    default:
      return t("updater.stageWorking", { defaultValue: "Installing…" });
  }
};

// Best-effort lifecycle logging into the Unifideck log dir (per-session).
// The backend may be mid-reload near the end of an install, so failures are ignored.
const logEvent = (stage: string, detail: string) => {
  void call<[string, string], unknown>(
    rpcRoutes.logUpdateEvent,
    stage,
    detail,
  ).catch(() => {});
};

// Selected release tag, persisted across QAM mount/unmount — the Quick-Access
// panel dismounts when closed (and when the Dropdown overlay opens), which would
// otherwise reset the selection back to the default. Mirrors `persistentActiveTab`
// in QuickAccessPanel.tsx. Cleared naturally when the plugin reloads after install.
let persistentSelectedTag: string | null = null;

export const PluginUpdater: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  // The selected release is identified by its unique tag (not the parsed
  // version, which could collide between a stable and a prerelease).
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [installing, setInstalling] = useState(false);
  const [checking, setChecking] = useState(false);

  // Live install progress mirrored from Decky's loader events.
  const [downloadActive, setDownloadActive] = useState(false);
  const [downloadPercent, setDownloadPercent] = useState(0);
  const [downloadStatus, setDownloadStatus] = useState("");
  const downloadActiveRef = useRef(false);
  const watchdogTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch updates status
  const {
    data: updateData,
    loading: checkingOnMount,
    refetch: checkUpdate,
  } = useRPCQuery<
    [],
    {
      available: boolean;
      current: string;
      current_build_id: string | null;
      latest: ReleaseInfo | null;
    }
  >(rpcRoutes.checkPluginUpdate, []);

  // Fetch available versions
  const {
    data: versionsData,
    loading: loadingVersions,
    refetch: refetchVersions,
  } = useRPCQuery<[], ReleaseInfo[]>(rpcRoutes.getAvailableVersions, []);

  // Cache-bypassing variants — used by the explicit "Check for Updates"
  // action and before installing a prerelease, whose single GitHub asset
  // gets deleted and re-uploaded under a new name/URL on every dev build.
  // The plain queries above stay on the 1-hour cache (mount-time auto-check,
  // background poller) so we don't hammer GitHub's unauthenticated rate limit.
  const forceCheckMut = useRPCMutation<
    [],
    {
      available: boolean;
      current: string;
      current_build_id: string | null;
      latest: ReleaseInfo | null;
    }
  >(rpcRoutes.forceCheckPluginUpdate);
  const forceVersionsMut = useRPCMutation<[], ReleaseInfo[]>(
    rpcRoutes.forceGetAvailableVersions,
  );

  const currentVersion = updateData?.current ?? "0.0.0";
  const currentBuildId = updateData?.current_build_id ?? null;
  const initializedRef = useRef(false);

  // Seed the selection when data loads. Restore a prior selection (persisted
  // across QAM remounts) if it still resolves to an available release; otherwise
  // default to the installed version, then latest, then newest.
  useEffect(() => {
    if (updateData && versionsData && !initializedRef.current) {
      if (
        persistentSelectedTag &&
        versionsData.some((v) => v.tag === persistentSelectedTag)
      ) {
        setSelectedTag(persistentSelectedTag);
      } else {
        // A prerelease row's `version` is always its raw non-semver tag
        // ("Dev-<date>-<time>-<sha>" — no semver can be parsed from it),
        // so it can never equal currentVersion. Match it via the build id
        // baked into its asset filename instead.
        //
        // currentBuildId !== null is also the deciding factor for the
        // stable-release branch below: dev builds are deliberately cut
        // BEFORE package.json's version gets bumped for release, so a
        // genuinely-installed dev build's `current` is identical to the
        // officially-tagged stable release sharing that same frozen
        // base version (e.g. both read "0.7.0"). Without this guard,
        // a real dev install would match the stable release's `version
        // === currentVersion` check too, seeding the selection (and
        // "(installed)" tag below) onto the wrong row.
        const current = versionsData.find((v) =>
          v.prerelease
            ? currentBuildId !== null &&
              extractDevBuildId(v.asset_name) === currentBuildId
            : currentBuildId === null && v.version === currentVersion,
        );
        const tag =
          current?.tag ?? updateData.latest?.tag ?? versionsData[0]?.tag ?? "";
        persistentSelectedTag = tag || null;
        setSelectedTag(tag);
      }
      initializedRef.current = true;
    }
  }, [updateData, versionsData, currentVersion, currentBuildId]);

  // Subscribe to Decky's loader install events to mirror progress in-panel.
  useEffect(() => {
    const backend = getDeckyBackend();
    if (!backend) return;

    const clearWatchdog = () => {
      if (watchdogTimerRef.current !== null) {
        clearTimeout(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
    };
    // Decky's own install code can die silently (confirmed: a 404 on a
    // rotated dev-build asset logs one CRITICAL line in journalctl and then
    // never fires plugin_download_info/finish again) — without this, the
    // panel freezes forever with downloadActive stuck true.
    const armWatchdog = () => {
      clearWatchdog();
      watchdogTimerRef.current = setTimeout(() => {
        if (!downloadActiveRef.current) return;
        downloadActiveRef.current = false;
        setDownloadActive(false);
        logEvent("error", "watchdog_timeout after 45s of inactivity");
        toast.error(
          t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
          t("updater.installTimeoutMessage", {
            defaultValue:
              "No response from Decky Loader — the install may have stalled or failed. Please try again.",
          }),
        );
      }, INSTALL_WATCHDOG_TIMEOUT_MS);
    };

    const onStart = (name: string) => {
      if (name !== "Unifideck") return;
      downloadActiveRef.current = true;
      setDownloadActive(true);
      setDownloadPercent(0);
      setDownloadStatus(stageLabel("start", t));
      logEvent("download_start", name);
      armWatchdog();
    };
    const onInfo = (percent: number, key?: string) => {
      if (!downloadActiveRef.current) return;
      setDownloadPercent(percent);
      setDownloadStatus(stageLabel(key, t));
      logEvent("progress", `${percent}% ${key ?? ""}`.trim());
      armWatchdog();
    };
    const onFinish = (name: string) => {
      if (name !== "Unifideck") return;
      downloadActiveRef.current = false;
      setDownloadPercent(100);
      setDownloadActive(false);
      logEvent("download_finish", name);
      clearWatchdog();
    };

    backend.addEventListener("loader/plugin_download_start", onStart);
    backend.addEventListener("loader/plugin_download_info", onInfo);
    backend.addEventListener("loader/plugin_download_finish", onFinish);
    return () => {
      backend.removeEventListener("loader/plugin_download_start", onStart);
      backend.removeEventListener("loader/plugin_download_info", onInfo);
      backend.removeEventListener("loader/plugin_download_finish", onFinish);
      // Avoid a false-positive error toast firing after the QAM panel
      // unmounts while a download legitimately continues server-side.
      clearWatchdog();
    };
    // toast.error is useCallback-memoized in useToast(), so this is stable
    // and won't cause the effect (and its addEventListener subscriptions)
    // to re-run on every render the way depending on `toast` itself would
    // (useToast() returns a new object literal every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, toast.error]);

  const selectedRelease = useMemo(() => {
    if (!versionsData) return null;
    return versionsData.find((v) => v.tag === selectedTag) || null;
  }, [versionsData, selectedTag]);

  const selectedVersion = selectedRelease?.version ?? "";

  // Format dropdown options
  const versionOptions = useMemo(() => {
    if (!versionsData) return [];
    return versionsData.map((v) => {
      // A dev release's asset filename carries the real build identity
      // (branch + short SHA) — its parsed `version` is only ever the raw
      // non-semver tag, which says nothing about which build it is.
      const devBuildId = v.prerelease ? extractDevBuildId(v.asset_name) : null;
      let label = devBuildId ?? `v${v.version}`;

      // currentBuildId === null gates the stable branch: dev builds are
      // cut before package.json's version bumps, so a genuinely-installed
      // dev build's `currentVersion` is identical to the officially
      // tagged stable release sharing that frozen base version. Without
      // this guard both rows would show "(installed)" at once whenever a
      // dev build happens to share its base version with a real release.
      const isInstalled = v.prerelease
        ? devBuildId !== null && devBuildId === currentBuildId
        : currentBuildId === null && v.version === currentVersion;

      if (isInstalled) {
        label += ` (${t("updater.installedLabel", {
          defaultValue: "installed",
        })})`;
      } else if (updateData?.latest?.version === v.version) {
        label += ` (${t("updater.latestLabel", { defaultValue: "latest" })})`;
      }

      if (v.prerelease) {
        label += " [DEV]";
      }

      return {
        data: v.tag,
        label,
      };
    });
  }, [versionsData, currentVersion, currentBuildId, updateData, t]);

  const handleVersionSelect = (opt: { data: string }) => {
    const tag = String(opt.data);
    persistentSelectedTag = tag; // survive the QAM dismount that follows selection
    setSelectedTag(tag);
  };

  const handleCheckUpdate = async () => {
    setChecking(true);
    try {
      // Force GitHub to be re-queried (bypasses the 1-hour cache), then
      // pull the now-warm result into the displayed query state — this
      // second pair only hits the in-process cache, no extra GitHub call.
      await Promise.all([forceCheckMut.mutate(), forceVersionsMut.mutate()]);
      await Promise.all([checkUpdate(), refetchVersions()]);
      toast.success(
        t("updater.checkCompleteTitle", {
          defaultValue: "Update Check Complete",
        }),
        t("updater.checkCompleteMessage", {
          defaultValue: "Successfully fetched latest version info.",
        }),
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : undefined;
      toast.error(
        t("updater.checkFailedTitle", { defaultValue: "Check Failed" }),
        message ?? t("errors.unknown"),
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
      />,
    );
  };

  const handleInstall = async () => {
    if (!selectedRelease) return;

    const backend = getDeckyBackend();
    if (!backend) {
      toast.error(
        t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
        t("updater.noBackend", {
          defaultValue: "Decky backend is unavailable.",
        }),
      );
      return;
    }

    setInstalling(true);
    try {
      // Prerelease/dev tags are mutable — their single GitHub asset gets
      // deleted and re-uploaded under a new name every time a new dev
      // build is cut, so a URL sitting in React state can already be
      // dead. Force a fresh fetch and re-resolve by tag (the stable
      // identifier) before ever handing a URL to Decky's installer.
      let release = selectedRelease;
      if (release.prerelease) {
        const fresh = await forceVersionsMut.mutate();
        // `mutate()` resolves `null` ONLY when the refresh call itself
        // failed (network hiccup, GitHub rate limit, backend error) —
        // a successful call always resolves an array, even an empty
        // one. Treating that failure the same as "genuinely gone" is
        // misleading: it tells the user their release vanished when in
        // fact we simply couldn't check, which is confusing when GitHub
        // shows the release is right there.
        if (fresh === null) {
          toast.error(
            t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
            t("updater.refreshFailedMessage", {
              defaultValue:
                "Could not verify the latest release info. Check your connection and try again.",
            }),
          );
          setInstalling(false);
          return;
        }
        const match = fresh.find((v) => v.tag === release.tag);
        if (!match) {
          toast.error(
            t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
            t("updater.releaseGoneMessage", {
              defaultValue:
                "This release is no longer available. Please Check for Updates and select again.",
            }),
          );
          setInstalling(false);
          return;
        }
        release = match;
      }

      const { installType, displayVersion } = resolveInstallAction(
        release,
        currentVersion,
        currentBuildId,
      );
      const typeLabel =
        installType === INSTALL_TYPE_REINSTALL
          ? t("updater.typeReinstall", { defaultValue: "Reinstalling" })
          : installType === INSTALL_TYPE_DOWNGRADE
          ? t("updater.typeDowngrade", { defaultValue: "Downgrading to" })
          : t("updater.typeUpdate", { defaultValue: "Updating to" });

      logEvent(
        "triggered",
        `${typeLabel} v${displayVersion} (type=${installType}) url=${release.asset_url}`,
      );
      toast.info(
        t("updater.installingTitle", { defaultValue: "Installing Plugin" }),
        `${typeLabel} v${displayVersion}...`,
      );

      // Hand off to Decky Loader's installer via the GLOBAL ws router.
      // (`call` from @decky/api is plugin-scoped and cannot reach utilities/*.)
      // This only registers the request and pops Decky's native confirm modal
      // (which has its own progress bar); it returns immediately. Decky's modal
      // calls confirm_plugin_install on OK; our listeners mirror the progress.
      await backend.call(
        "utilities/install_plugin",
        release.asset_url,
        "Unifideck",
        displayVersion,
        release.sha256 || "",
        installType,
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : undefined;
      logEvent("error", message ?? String(e));
      toast.error(
        t("updater.installFailedTitle", { defaultValue: "Install Failed" }),
        message ?? t("errors.unknown"),
      );
    } finally {
      // The call returns before the install runs; never leave the panel locked.
      // Decky's confirm modal + the loader progress events own the rest.
      setInstalling(false);
    }
  };

  const isLoading = checkingOnMount || loadingVersions;
  const busy = installing || downloadActive || checking;

  // Render header title
  const sectionTitle = useMemo(() => {
    if (isLoading) {
      return `${t("updater.titleLoading", {
        defaultValue: "Checking version",
      })}...`;
    }
    const buildSuffix = currentBuildId ? ` (${currentBuildId})` : "";
    return `${t("updater.currentTitle", {
      defaultValue: "Current",
    })} - v${currentVersion}${buildSuffix}`;
  }, [currentVersion, currentBuildId, isLoading, t]);

  // Same resolveInstallAction used by handleInstall — previously this
  // button independently re-derived update/downgrade/reinstall via its
  // own compareVersions() call, which could disagree with (and, for
  // prerelease rows, was as wrong as) the logic actually driving the
  // install request itself.
  const installButtonLabel = useMemo(() => {
    if (downloadActive) {
      return t("updater.installingButton", { defaultValue: "Installing..." });
    }
    if (!selectedRelease) return "";
    const { installType, displayVersion } = resolveInstallAction(
      selectedRelease,
      currentVersion,
      currentBuildId,
    );
    if (installType === INSTALL_TYPE_REINSTALL) {
      return t("updater.reinstallButton", {
        version: displayVersion,
        defaultValue: `Reinstall v${displayVersion}`,
      });
    }
    if (installType === INSTALL_TYPE_DOWNGRADE) {
      return t("updater.downgradeButton", {
        version: displayVersion,
        defaultValue: `Downgrade to v${displayVersion}`,
      });
    }
    return t("updater.updateButton", {
      version: displayVersion,
      defaultValue: `Update to v${displayVersion}`,
    });
  }, [downloadActive, selectedRelease, currentVersion, currentBuildId, t]);

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
                selectedOption={selectedTag}
                onChange={handleVersionSelect}
                disabled={downloadActive || checking}
              />
            </PanelSectionRow>
          )}

          {downloadActive && (
            <PanelSectionRow>
              <ProgressBarWithInfo
                layout="inline"
                bottomSeparator="none"
                nProgress={downloadPercent}
                sOperationText={downloadStatus}
              />
            </PanelSectionRow>
          )}

          {selectedRelease && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={handleInstall}
                disabled={busy}
              >
                {installButtonLabel}
              </ButtonItem>
            </PanelSectionRow>
          )}

          {selectedRelease && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={handleShowReleaseNotes}
                disabled={busy}
              >
                {t("updater.releaseNotesButton", {
                  defaultValue: "Release Notes",
                })}
              </ButtonItem>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleCheckUpdate}
              disabled={busy}
            >
              {checking
                ? t("updater.checkingButton", { defaultValue: "Checking..." })
                : t("updater.checkButton", {
                    defaultValue: "Check for Updates",
                  })}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
};
