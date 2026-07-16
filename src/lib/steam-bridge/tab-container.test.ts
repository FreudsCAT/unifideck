// @vitest-environment jsdom
/**
 * Regression tests for the library-crash guard in the fake
 * collection's `GetAppCountWithToolsFilter` / `renderTabAddon`:
 * the 2026-07 Steam Beta reordered the tabs-useMemo deps and handed
 * the count path a collection object with no `Matches`, which threw
 * `appFilter.Matches is not a function` and error-boundaried the
 * entire Steam library. The count path must never throw, whatever
 * shape Steam passes.
 */
import { describe, it, expect, vi } from "vitest";

// The Decky runtime is peer-provided in the Steam webview and absent
// under vitest — stub the imports that pull it in. (React resolves to
// src/test-support/react-stub.ts via the vitest alias.)
vi.mock("@decky/ui", () => ({ gamepadTabbedPageClasses: undefined }));
vi.mock("i18next", () => ({ default: { t: (key: string) => key } }));
vi.mock("../library-filters", () => ({
  runFilters: () => true,
  setStoreCountSink: () => {},
}));

import { UnifideckTabContainer, type SteamAppFilter } from "./tab-container";
import type { SteamAppOverview } from "../../types/steam";

function makeContainer(appids: number[] = []): UnifideckTabContainer {
  const container = new UnifideckTabContainer({
    id: "unifideck-test",
    title: "Test",
    position: 0,
    filters: [],
  });
  container.collection.visibleApps = appids.map((appid) => ({ appid } as SteamAppOverview));
  return container;
}

describe("GetAppCountWithToolsFilter guard", () => {
  it("applies a well-formed filter", () => {
    const c = makeContainer([1, 2, 3]);
    const filter: SteamAppFilter = { Matches: (a) => a.appid !== 2 };
    expect(c.collection.GetAppCountWithToolsFilter(filter)).toBe(2);
  });

  it("falls back to the unfiltered count when the filter is undefined", () => {
    const c = makeContainer([1, 2]);
    expect(c.collection.GetAppCountWithToolsFilter(undefined)).toBe(2);
  });

  it("falls back when handed an object without a callable Matches (Steam Beta deps shift)", () => {
    const c = makeContainer([1, 2, 3]);
    // What deps[6] became on the 2026-07 beta: a collection, not a filter.
    const notAFilter = {
      GetAppCountWithToolsFilter: () => 0,
    } as unknown as SteamAppFilter;
    expect(c.collection.GetAppCountWithToolsFilter(notAFilter)).toBe(3);
  });

  it("falls back when Matches itself throws", () => {
    const c = makeContainer([1, 2]);
    const throwing: SteamAppFilter = {
      Matches: () => {
        throw new Error("internal Steam change");
      },
    };
    expect(c.collection.GetAppCountWithToolsFilter(throwing)).toBe(2);
  });
});

describe("renderTabAddon", () => {
  it("renders a count without throwing when no filter was resolved", () => {
    const c = makeContainer();
    const tab = c.getActualTab((() => null) as never, null, {}, undefined);
    expect(tab).not.toBeNull();
    expect(tab?.renderTabAddon).toBeDefined();
    expect(() => tab?.renderTabAddon?.()).not.toThrow();
  });
});
