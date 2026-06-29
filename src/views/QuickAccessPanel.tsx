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
import { CSSProperties, FC, useState } from "react";
import { DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useSync } from "../contexts/SyncContext";
import {
  StoreConnections,
  LibrarySync,
  LanguageSelector,
  GameDetailsViewModeToggle,
  CollectionsToggle,
  CleanupSection,
  PluginUpdater,
} from "../components/settings";
import { DownloadsTab } from "../components/downloads";

type ActiveTab = "settings" | "downloads";

/** Last-viewed tab persisted across QAM mount/unmount. */
let persistentActiveTab: ActiveTab = "settings";

/**
 * Shared style for the two tab buttons.
 *
 * The QAM panel is narrow and each button is a fixed 50% (`flex: 1`), so the
 * longest labels — French "Téléchargements" (15 chars), and longer still with
 * the live "(NN%)" sync suffix — overran the button's rounded boundary. Tight
 * horizontal padding plus a slightly smaller, *zoom-relative* font (`em`, so it
 * scales with Steam's global UI scale at every resolution) gives the text room
 * to fit; `nowrap` + `overflow: hidden` + `ellipsis` is the safety net so text
 * is clipped *inside* the button (never spills past it) in the extreme case.
 */
const tabButtonStyle = (active: boolean): CSSProperties => ({
  flex: 1,
  minWidth: 0,
  padding: "10px 6px",
  fontSize: "0.9em",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  fontWeight: active ? 600 : 400,
  opacity: active ? 1 : 0.7,
});

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
          style={tabButtonStyle(tab === "settings")}
        >
          {t("tabs.settings")}
        </DialogButton>
        <DialogButton
          onClick={() => setTab("downloads")}
          style={tabButtonStyle(tab === "downloads")}
        >
          {downloadsLabel}
        </DialogButton>
      </div>
      {/* Spacer wrapper: Steam's PanelSection title carries a negative top
          margin (it assumes it is the first child of the scroll container),
          which otherwise pulls the first section header up into the tab
          buttons above. Padding here pushes the content clear of the row. */}
      <div style={{ paddingBlockStart: 12 }}>
        {tab === "settings" && (
          <>
            <StoreConnections />
            <LibrarySync />
            <LanguageSelector />
            <GameDetailsViewModeToggle />
            <CollectionsToggle />
            <PluginUpdater />
            <CleanupSection />
          </>
        )}
        {tab === "downloads" && <DownloadsTab />}
      </div>
    </div>
  );
};
