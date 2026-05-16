/**
 * QuickAccessPanel — top-level Decky tab content.
 *
 * Replaces the legacy `<Content>` component (1305 LOC)
 * which mixed sync state, account switch logic, downloads,
 * settings, language selection, and a custom tab switcher.
 * The new panel reads `useSync()` to know whether to show
 * the downloads progress badge, then maps each section to
 * its dedicated component (StoreConnections, LibrarySync,
 * StorageSettings, LanguageSelector, DownloadsTab).
 *
 * Tab state is held in a module-level `persistentActiveTab`
 * so the last-viewed tab survives Quick-Access dismount /
 * remount (legacy behaviour from staging index.tsx).
 *
 * Tab buttons are bare `DialogButton`s in a flex row (no
 * `PanelSection` wrapper, no extra `Focusable`). This is what
 * Steam's gamepad focus needs to land on them — wrapping
 * `DialogButton` in another `Focusable` swallows the focus
 * target on this build of Decky.
 */
import { FC, useState } from "react";
import { DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useSync } from "../contexts/SyncContext";
import {
  StoreConnections, LibrarySync, StorageSettings, LanguageSelector,
  GameDetailsViewModeToggle, CleanupSection,
} from "../components/settings";
import { DownloadsTab } from "../components/downloads";

type ActiveTab = "settings" | "downloads";

/** Last-viewed tab persisted across QAM mount/unmount. */
let persistentActiveTab: ActiveTab = "settings";

/**
 * Root component of the Decky Loader Quick Access menu.
 * Composes the two tabs (Settings, Downloads) and persists
 * the active tab across QAM open/close.
 */
export const QuickAccessPanel: FC = () => {
  const { t } = useTranslation();
  const sync = useSync();
  const [tab, setTabState] = useState<ActiveTab>(persistentActiveTab);

  const setTab = (next: ActiveTab): void => {
    persistentActiveTab = next;
    setTabState(next);
  };

  const downloadsLabel = sync.isSyncing
    ? `${t("tabs.downloads")} (${sync.progress?.progress_percent ?? 0}%)`
    : t("tabs.downloads");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 6, padding: "4px 8px 0" }}>
        <DialogButton
          onClick={() => setTab("settings")}
          style={{
            flex: 1,
            minWidth: 0,
            fontWeight: tab === "settings" ? 600 : 400,
            opacity: tab === "settings" ? 1 : 0.7,
          }}
        >
          {t("tabs.settings")}
        </DialogButton>
        <DialogButton
          onClick={() => setTab("downloads")}
          style={{
            flex: 1,
            minWidth: 0,
            fontWeight: tab === "downloads" ? 600 : 400,
            opacity: tab === "downloads" ? 1 : 0.7,
          }}
        >
          {downloadsLabel}
        </DialogButton>
      </div>
      {tab === "settings" && (
        <>
          <StoreConnections />
          <LibrarySync />
          <LanguageSelector />
          <GameDetailsViewModeToggle />
          <CleanupSection />
        </>
      )}
      {tab === "downloads" && (
        <>
          <DownloadsTab />
          <StorageSettings />
        </>
      )}
    </div>
  );
};
