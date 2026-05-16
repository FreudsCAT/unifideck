/**
 * Tab definitions and per-tab collection container for the custom
 * Unifideck library tabs. Ported from staging:src/tabs/TabContainer.ts.
 *
 * `UNIFIDECK_TABS` declares the 10 tabs spliced into Steam's library.
 * `UnifideckTabContainer` wraps Steam's collection shape so each tab
 * is treated as a first-class collection (count, sort, filter).
 * `tabManager` is the singleton that the library-patch hook reads to
 * know which tabs to inject.
 */
import React, { ReactElement } from "react";
import { gamepadTabbedPageClasses } from "@decky/ui";
import i18n from "i18next";
import { runFilters, type TabFilter } from "../library-filters";
import type { SteamAppOverview } from "../../types/steam";

const t = (key: string): string => i18n.t(key);

export interface UnifideckTab {
  id: string;
  title: string;
  position: number;
  filters: TabFilter[];
  icon?: string;
}

export function getUnifideckTabs(): UnifideckTab[] {
  return [
    { id: "unifideck-deck",      title: t("deckTabs.greatOnDeck"), position: 0, filters: [{ type: "deckCompat", params: {} }] },
    { id: "unifideck-all",       title: t("deckTabs.allGames"),    position: 1, filters: [{ type: "all", params: {} }] },
    { id: "unifideck-installed", title: t("deckTabs.installed"),   position: 2, filters: [{ type: "installed", params: { installed: true } }] },
    { id: "unifideck-steam",     title: t("deckTabs.steam"),       position: 3, filters: [{ type: "store", params: { store: "steam" } }] },
    { id: "unifideck-epic",      title: t("deckTabs.epic"),        position: 4, filters: [{ type: "store", params: { store: "epic" } }] },
    { id: "unifideck-gog",       title: t("deckTabs.gog"),         position: 5, filters: [{ type: "store", params: { store: "gog" } }] },
    { id: "unifideck-amazon",    title: t("deckTabs.amazon"),      position: 6, filters: [{ type: "store", params: { store: "amazon" } }] },
    { id: "unifideck-ubisoft",   title: t("deckTabs.ubisoft"),     position: 7, filters: [{ type: "store", params: { store: "ubisoft" } }] },
    { id: "unifideck-microsoft", title: t("deckTabs.microsoft"),   position: 8, filters: [{ type: "store", params: { store: "microsoft" } }] },
    { id: "unifideck-nonsteam",  title: t("deckTabs.nonSteam"),    position: 9, filters: [{ type: "nonSteam", params: {} }] },
  ];
}

const DEFAULT_TABS_TO_HIDE = ["GreatOnDeck", "AllGames", "Installed", "DesktopApps"];

export function isTabMasterInstalled(): boolean {
  try {
    const plugins = (window as unknown as {
      DeckyPluginLoader?: { plugins?: Array<{ name?: string }> };
    }).DeckyPluginLoader?.plugins ?? [];
    return plugins.some((p) => p.name === "TabMaster" || p.name === "Tab Master");
  } catch { return false; }
}

export function getHiddenDefaultTabs(): string[] {
  if (isTabMasterInstalled()) {
    console.log("[Unifideck] TabMaster detected — keeping default tabs visible");
    return [];
  }
  return DEFAULT_TABS_TO_HIDE;
}

export const HIDDEN_DEFAULT_TABS = DEFAULT_TABS_TO_HIDE;

interface SteamCollectionLike {
  AsDeletableCollection: () => null;
  AsDragDropCollection: () => null;
  AsEditableCollection: () => null;
  GetAppCountWithToolsFilter: (
    appFilter: { Matches: (a: SteamAppOverview) => boolean },
  ) => number;
  bAllowsDragAndDrop: boolean;
  bIsDeletable: boolean;
  bIsDynamic: boolean;
  bIsEditable: boolean;
  displayName: string;
  id: string;
  allApps: SteamAppOverview[];
  visibleApps: SteamAppOverview[];
  apps: Map<number, SteamAppOverview>;
}

interface CollectionStoreLike {
  GetCollection: (id: string) => {
    allApps?: SteamAppOverview[];
  } | null;
}

/** Steam tab shape consumed by the library-patch hook. */
export interface SteamTab {
  title: string;
  id: string;
  content: ReactElement;
  footer: Record<string, unknown>;
  renderTabAddon?: () => ReactElement;
}

export class UnifideckTabContainer {
  id: string;
  title: string;
  position: number;
  filters: TabFilter[];
  collection: SteamCollectionLike;

  constructor(tab: UnifideckTab) {
    this.id = tab.id;
    this.title = tab.title;
    this.position = tab.position;
    this.filters = tab.filters;
    this.collection = this.makeEmptyCollection();
    this.buildCollection();
  }

  private makeEmptyCollection(): SteamCollectionLike {
    return {
      AsDeletableCollection: () => null,
      AsDragDropCollection: () => null,
      AsEditableCollection: () => null,
      GetAppCountWithToolsFilter: (appFilter) =>
        this.collection.visibleApps.filter((a) => appFilter.Matches(a)).length,
      bAllowsDragAndDrop: false,
      bIsDeletable: false,
      bIsDynamic: false,
      bIsEditable: false,
      displayName: this.title,
      id: this.id,
      allApps: [],
      visibleApps: [],
      apps: new Map(),
    };
  }

  buildCollection(): void {
    try {
      const cs = (window as unknown as { collectionStore?: CollectionStoreLike }).collectionStore;
      const all = cs?.GetCollection("type-games");
      if (!all) return;
      const filtered = (all.allApps ?? []).filter((app) =>
        runFilters(this.filters, app));
      this.collection.allApps = filtered;
      this.collection.visibleApps = [...filtered];
      const map = new Map<number, SteamAppOverview>();
      for (const a of filtered) map.set(a.appid, a);
      this.collection.apps = map;
    } catch (e) {
      console.error("[Unifideck] buildCollection failed", e);
    }
  }

  getActualTab(
    TabAppGrid: React.ComponentType<Record<string, unknown>>,
    TabContext: React.Context<{ label: string }> | null,
    sortingProps: Record<string, unknown>,
    collectionAppFilter: { Matches: (a: SteamAppOverview) => boolean },
  ): SteamTab | null {
    this.buildCollection();
    const inner = React.createElement(TabAppGrid, {
      collection: this.collection,
      ...sortingProps,
    });
    const content = TabContext
      ? React.createElement(TabContext.Provider, { value: { label: this.title } }, inner)
      : inner;
    return {
      title: this.title,
      id: this.id,
      footer: {},
      content,
      renderTabAddon: () => React.createElement(
        "span",
        { className: gamepadTabbedPageClasses?.TabCount ?? "" },
        this.collection.GetAppCountWithToolsFilter(collectionAppFilter),
      ),
    };
  }
}

type ConnectableStore = "epic" | "gog" | "amazon" | "ubisoft" | "microsoft";

class TabManager {
  private tabs: UnifideckTabContainer[] = [];
  private initialized = false;
  private connectedStores = new Set<ConnectableStore>();
  private storeCounts: Record<ConnectableStore, number> = {
    epic: 0, gog: 0, amazon: 0, ubisoft: 0, microsoft: 0,
  };

  initialize(): void {
    if (this.initialized) return;
    this.tabs = getUnifideckTabs().map((tab) => new UnifideckTabContainer(tab));
    this.initialized = true;
  }

  getTabs(): UnifideckTabContainer[] {
    return this.tabs.filter((t) => this.shouldShowTab(t.id));
  }

  setStoreCounts(counts: Partial<Record<ConnectableStore, number>>): void {
    this.storeCounts = { ...this.storeCounts, ...counts };
  }

  setConnectedStores(statuses: Partial<Record<ConnectableStore, string>>): void {
    const next = new Set<ConnectableStore>();
    (["epic", "gog", "amazon", "ubisoft", "microsoft"] as const).forEach((s) => {
      if (statuses[s] === "connected") next.add(s);
    });
    this.connectedStores = next;
    if (this.initialized) this.rebuildTabs();
  }

  private shouldShowTab(id: string): boolean {
    const m: Record<string, ConnectableStore> = {
      "unifideck-epic": "epic",
      "unifideck-gog": "gog",
      "unifideck-amazon": "amazon",
      "unifideck-ubisoft": "ubisoft",
      "unifideck-microsoft": "microsoft",
    };
    const store = m[id];
    if (!store) return true;
    return this.storeCounts[store] > 0 || this.connectedStores.has(store);
  }

  isInitialized(): boolean { return this.initialized; }

  rebuildTabs(): void {
    this.tabs = getUnifideckTabs().map((tab) => new UnifideckTabContainer(tab));
  }
}

export const tabManager = new TabManager();
