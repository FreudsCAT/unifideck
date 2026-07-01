// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("@decky/api", () => ({ call: vi.fn() }));
vi.mock("../../api/useRPC", () => ({
  unwrapRpcEnvelope: (raw: unknown) => raw,
}));
vi.mock("../protondb-cache", () => ({
  meetsGreatOnDeckCriteria: vi.fn(),
  getCachedCompatByTitle: vi.fn(),
  getCachedRating: vi.fn(),
  loadCompatCacheFromBackend: vi.fn(),
}));
vi.mock("../library-facets", () => ({
  getCompatByShortcutAppId: vi.fn(),
  loadFacets: vi.fn(),
}));
vi.mock("../../api/event-bus-client", () => ({
  EventBusClient: {
    subscribe: vi.fn(),
  },
}));

import { runFilter, unifideckGameCache, validThirdPartyCache } from "./index";
import type { SteamAppOverview } from "../../types/steam";

const NON_STEAM_APP_TYPE = 1073741824;

describe("library-filters/index.ts installed filter", () => {
  beforeEach(() => {
    unifideckGameCache.clear();
    validThirdPartyCache.clear();
  });

  it("includes an installed Steam game", () => {
    const app = {
      appid: 12345,
      app_type: 1, // Native Steam Game
      installed: true,
      display_name: "Steam Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(true);
  });

  it("excludes an uninstalled Steam game", () => {
    const app = {
      appid: 12345,
      app_type: 1,
      installed: false,
      display_name: "Steam Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });

  it("includes an installed Unified game", () => {
    unifideckGameCache.set(999, {
      store: "epic",
      isInstalled: true,
    });

    const app = {
      appid: 999,
      app_type: NON_STEAM_APP_TYPE,
      installed: true,
      display_name: "Unified Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(true);
  });

  it("excludes an uninstalled Unified game", () => {
    unifideckGameCache.set(999, {
      store: "epic",
      isInstalled: false,
    });

    const app = {
      appid: 999,
      app_type: NON_STEAM_APP_TYPE,
      installed: true, // Steam might report it as installed because it's a shortcut
      display_name: "Unified Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });

  it("excludes non-Unifideck third-party shortcuts", () => {
    const app = {
      appid: 777,
      app_type: NON_STEAM_APP_TYPE,
      installed: true,
      display_name: "Custom Shortcut",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });
});
