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
 */
import { FC, useState } from "react";
import {
  DialogButton, Focusable, PanelSection, PanelSectionRow,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useSync } from "../contexts/SyncContext";
import {
  StoreConnections, LibrarySync, StorageSettings, LanguageSelector,
} from "../components/settings";
import { DownloadsTab } from "../components/downloads";

type ActiveTab = "settings" | "downloads";

/** Last-viewed tab persisted across QAM mount/unmount. */
let persistentActiveTab: ActiveTab = "settings";

/** Tab button props. */
interface TabButtonProps {
  active: boolean;
  label: string;
  onClick: () => void;
}

/** Tab button — uses DialogButton so gamepad focus halo
 *  appears and Decky's theme applies. */
const TabButton: FC<TabButtonProps> = ({ active, label, onClick }) => (
  <DialogButton
    onClick={onClick}
    style={{
      flex: 1,
      minWidth: 0,
      padding: "6px 10px",
      fontSize: 13,
      background: active ? "#2563eb" : "transparent",
      color: active ? "#fff" : "#cbd5e1",
    }}
  >
    {label}
  </DialogButton>
);

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

  return (
    <PanelSection>
      <PanelSectionRow>
        <Focusable
          flow-children="row"
          onActivate={() => {}}
          style={{ display: "flex", gap: 4, width: "100%" }}
        >
          <TabButton
            active={tab === "settings"}
            label={t("tabs.settings")}
            onClick={() => setTab("settings")}
          />
          <TabButton
            active={tab === "downloads"}
            label={
              sync.isSyncing
                ? `${t("tabs.downloads")} (${sync.progress?.progress_percent ?? 0}%)`
                : t("tabs.downloads")
            }
            onClick={() => setTab("downloads")}
          />
        </Focusable>
      </PanelSectionRow>
      {tab === "settings" && (
        <>
          <StoreConnections />
          <LibrarySync />
          <StorageSettings />
          <LanguageSelector />
        </>
      )}
      {tab === "downloads" && <DownloadsTab />}
    </PanelSection>
  );
};
