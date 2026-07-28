/**
 * QuickAccessPanel — top-level Decky tab content.
 *
 * Replaces the legacy `<Content>` component (1305 LOC)
 * which mixed sync state, account switch logic, downloads,
 * settings, language selection, and a custom tab switcher.
 * Maps each section to its dedicated component
 * (StoreConnections, LibrarySync, StorageSettings,
 * LanguageSelector, DownloadsTab).
 *
 * Tab state is held in a module-level `persistentActiveTab`
 * so the last-viewed tab survives Quick-Access dismount /
 * remount (legacy behaviour from staging index.tsx).
 *
 * Tab buttons are `Focusable`s carrying Steam's own `Tab` /
 * `Selected` classes, inside a `flow-children="row"` row.
 * They are NOT `DialogButton`s: Steam's tab styling assumes a
 * bare element, and DialogButton's own chrome fights it.
 *
 * Each tab needs its own `onActivate` — a `Focusable` with no
 * interactive children is not a focus target without one. The
 * older warning here (that wrapping in an extra `Focusable`
 * swallows focus) applied to wrapping a *DialogButton*; a
 * Focusable-as-tab with `onActivate` is a different construct
 * and does take focus. Verified on-device.
 */
import { CSSProperties, FC, useState } from "react";
import { Focusable, findClassModule } from "@decky/ui";
import { useTranslation } from "react-i18next";
import {
  StoreConnections,
  LibrarySync,
  LanguageSelector,
  GameDetailsViewModeToggle,
  CollectionsToggle,
  CleanupSection,
  CaptureLogsSection,
  PluginUpdater,
} from "../components/settings";
import { DownloadsTab } from "../components/downloads";

type ActiveTab = "settings" | "downloads";

/** Last-viewed tab persisted across QAM mount/unmount. */
let persistentActiveTab: ActiveTab = "settings";

/**
 * Steam's own tab-row CSS module (`TabRow` / `Tab` / `Selected`), looked up at
 * runtime the same way `@decky/ui` resolves Steam internals. Using Steam's
 * real classes means the active tab is highlighted with Steam's styling rather
 * than something we invented, and it tracks Valve's changes for free.
 */
const steamTabClasses = findClassModule(
  (m) => m.TabRowTabs && m.Tab && m.Selected,
) as { Tab?: string; Selected?: string } | undefined;

/**
 * Literal fallback if Steam ever renames that module — these are the values
 * Steam's own `.Tab` / `.Tab.Selected` rules compute to, so the look is
 * identical either way.
 */
const FALLBACK_TAB: CSSProperties = {
  fontSize: 12,
  fontWeight: "bold",
  letterSpacing: "0.5px",
  textTransform: "uppercase",
  background: "transparent",
  color: "#dcdedf",
  borderRadius: 3,
};
const FALLBACK_TAB_SELECTED: CSSProperties = {
  background: "rgba(255, 255, 255, 0.15)",
  color: "#ffffff",
};

/**
 * Geometry for the two tab buttons.
 *
 * The QAM panel is narrow and each button is a fixed 50% (`flex: 1`), so the
 * longest labels — French "Téléchargements" (15 chars) — overran the button's
 * rounded boundary. Tight
 * horizontal padding plus a slightly smaller, *zoom-relative* font (`em`, so it
 * scales with Steam's global UI scale at every resolution) gives the text room
 * to fit; `nowrap` + `overflow: hidden` + `ellipsis` is the safety net so text
 * is clipped *inside* the button (never spills past it) in the extreme case.
 *
 * Previously the active tab was signalled by `fontWeight` + `opacity` alone,
 * which read as "slightly brighter text" rather than "you are on this tab".
 */
const tabButtonStyle = (active: boolean): CSSProperties => ({
  flex: 1,
  minWidth: 0,
  padding: "10px 6px",
  // Steam's `Tab` class is `display: flex` with `text-align: start`, so
  // `textAlign: center` alone does nothing — a flex container positions its
  // children with justify-content (which computes to `normal`, i.e. start),
  // leaving the label hard against the left edge of the pill. Centre it the
  // way the box model actually works, and keep textAlign for the fallback
  // path where the element is not a flex container.
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  textAlign: "center",
  cursor: "pointer",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  ...(steamTabClasses?.Tab
    ? { fontSize: "0.9em" }
    : {
        ...FALLBACK_TAB,
        ...(active ? FALLBACK_TAB_SELECTED : {}),
      }),
});

/** Steam's `Tab` (+ `Selected`) class pair for the active state. */
const tabClassName = (active: boolean): string =>
  [steamTabClasses?.Tab, active ? steamTabClasses?.Selected : null]
    .filter(Boolean)
    .join(" ");

/**
 * Root component of the Decky Loader Quick Access menu.
 * Composes the two tabs (Settings, Downloads) and persists
 * the active tab across QAM open/close.
 */
export const QuickAccessPanel: FC = () => {
  const { t } = useTranslation();
  const [tab, setTabState] = useState<ActiveTab>(persistentActiveTab);

  const setTab = (next: ActiveTab): void => {
    persistentActiveTab = next;
    setTabState(next);
  };

  // The tab label carries NO percentage. It used to show the library-SYNC
  // progress, which is a different operation from downloading a game — a tab
  // reading "Downloads (90%)" while nothing is downloading is just wrong, and
  // it also overran the pill. Sync progress belongs to the Library Sync
  // section on the Settings tab, which already reports it.
  const downloadsLabel = t("tabs.downloads");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Focusable (not DialogButton) so Steam's `Tab` class styles a bare tab
          rather than fighting DialogButton's own button chrome. The row is a
          `flow-children="row"` Focusable so the pair navigates left/right. */}
      <Focusable
        flow-children="row"
        style={{ display: "flex", gap: 6, padding: "4px 8px 0" }}
      >
        <Focusable
          onActivate={() => setTab("settings")}
          className={tabClassName(tab === "settings")}
          style={tabButtonStyle(tab === "settings")}
        >
          {t("tabs.settings")}
        </Focusable>
        <Focusable
          onActivate={() => setTab("downloads")}
          className={tabClassName(tab === "downloads")}
          style={tabButtonStyle(tab === "downloads")}
        >
          {downloadsLabel}
        </Focusable>
      </Focusable>
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
            <CaptureLogsSection />
          </>
        )}
        {tab === "downloads" && <DownloadsTab />}
      </div>
    </div>
  );
};
