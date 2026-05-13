/**
 * QuickAccessPanel — top-level Decky tab content.
 *
 * Replaces the legacy `<Content>` component (1305 LOC)
 * which mixed sync state, account switch logic, downloads,
 * settings, language selection, and a custom tab switcher.
 * The new panel reads `useSync()` to know whether to show
 * the downloads tab, then maps each section to its
 * dedicated component (StoreConnections, LibrarySync,
 * StorageSettings, LanguageSelector, DownloadsTab).
 *
 * The local "active tab" state is the only state this
 * component owns ; everything else flows from contexts.
 * That's the cleanup payoff of the F2 / F3 / F4 work.
 */
import React, { FC, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSync } from "../contexts/SyncContext";
import {
  StoreConnections, LibrarySync, StorageSettings, LanguageSelector,
} from "../components/settings";
import { DownloadsTab } from "../components/downloads";

type ActiveTab = "settings" | "downloads";

/** Tab button props. */
interface TabButtonProps {
  active: boolean;
  label: string;
  onClick: () => void;
}

/** Tab button. */
const TabButton: FC<TabButtonProps> = ({ active, label, onClick }) => (
  <button
    onClick={onClick}
    style={{
      flex: 1,
      padding: "8px 12px",
      background: active ? "#2563eb" : "transparent",
      color: active ? "#fff" : "#94a3b8",
      border: "none",
      borderRadius: 4,
      cursor: "pointer",
      fontSize: 13,
    }}
  >
    {label}
  </button>
);

/**
 * Root component of the Decky Loader Quick Access menu.
 * Composes the four tabs (Stores, Library, Downloads,
 * Settings) and lets users swipe between them with the
 * trackpad / R1+L1 controller bindings.
 */
export const QuickAccessPanel: FC = () => {
  const { t } = useTranslation();
  const sync = useSync();
  const [tab, setTab] = useState<ActiveTab>("settings");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 4, padding: "0 4px" }}>
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
      </div>
      {tab === "settings" && (
        <>
          <StoreConnections />
          <LibrarySync />
          <StorageSettings />
          <LanguageSelector />
        </>
      )}
      {tab === "downloads" && <DownloadsTab />}
    </div>
  );
};
