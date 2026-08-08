// @vitest-environment jsdom
/**
 * Regression tests for the cleanup → collection-deletion flow:
 * Steam's `Delete()` mutates the live `userCollections` Map, which used
 * to skip entries mid-iteration, and the opt-in/opt-out collection manager.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

// React and the Decky runtime are peer-provided in the Steam webview
// and absent under vitest — stub the two imports that pull them in.
vi.mock("./tab-container", () => ({
  getUnifideckTabs: () => [{ id: "unifideck-alpha", title: "Alpha", position: 0, filters: [] }],
  isTabMasterInstalled: () => false,
}));
vi.mock("../library-filters", () => ({
  runFilters: () => true,
}));
// `call` is the only @decky/api binding reachable from here (via
// event-bus-client, which the manager subscribes to for install/uninstall).
// @decky/manifest is a build-time virtual module and unresolvable under vitest.
vi.mock("@decky/api", () => ({
  call: vi.fn(),
}));

import {
  deleteAllUnifideckCollections,
  syncUnifideckCollections,
  startCollectionManager,
} from "./collection-manager";

const COLLECTIONS_ENABLED_KEY = "unifideck:collections.enabled";
const COLLECTIONS_CLEANED_KEY = "unifideck:collections.cleaned";

interface MockCollection {
  id: string;
  displayName: string;
  allApps: unknown[];
  AsDragDropCollection: () => {
    AddApps: (o: unknown[]) => void;
    RemoveApps: (o: unknown[]) => void;
  };
  Save: () => Promise<void>;
  Delete: () => Promise<void>;
}

function makeStore(names: string[]) {
  const map = new Map<string, MockCollection>();
  let nextId = 1;
  const make = (name: string): MockCollection => {
    const id = `c${nextId++}`;
    const c: MockCollection = {
      id,
      displayName: name,
      allApps: [],
      AsDragDropCollection: () => ({ AddApps: () => {}, RemoveApps: () => {} }),
      Save: async () => {},
      // Mutates the backing Map mid-iteration — the exact behavior
      // that made the old live-iterator deletion skip entries.
      Delete: async () => {
        map.delete(id);
      },
    };
    map.set(id, c);
    return c;
  };
  names.forEach(make);
  const store = {
    userCollections: map,
    GetCollection: vi.fn((id: string) =>
      id === "type-games" ? { allApps: [{ appid: 1, display_name: "Game" }] } : map.get(id),
    ),
    GetCollectionIDByUserTag: vi.fn((tag: string) => {
      for (const c of map.values()) if (c.displayName === tag) return c.id;
      return null;
    }),
    NewUnsavedCollection: vi.fn((tag: string) => make(tag)),
  };
  (window as unknown as { collectionStore: unknown }).collectionStore = store;
  (window as unknown as { appStore: unknown }).appStore = {
    GetAppOverviewByAppID: () => ({ appid: 1, display_name: "Game" }),
  };
  return { map, store };
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("deleteAllUnifideckCollections", () => {
  it("deletes every [Unifideck] collection despite Map mutation during Delete()", async () => {
    const { map } = makeStore([
      "[Unifideck] Alpha",
      "[Unifideck] Beta",
      "Untouched",
      "[Unifideck] Gamma",
      "[Unifideck] Delta",
    ]);
    await deleteAllUnifideckCollections();
    const remaining = Array.from(map.values()).map((c) => c.displayName);
    expect(remaining).toEqual(["Untouched"]);
  });

  it("does not sync collections when disabled", async () => {
    const { store } = makeStore(["[Unifideck] Alpha"]);
    window.localStorage.setItem(COLLECTIONS_ENABLED_KEY, "0");

    store.GetCollection.mockClear();
    store.NewUnsavedCollection.mockClear();
    await syncUnifideckCollections();

    expect(store.GetCollection).not.toHaveBeenCalled();
    expect(store.NewUnsavedCollection).not.toHaveBeenCalled();
  });
});

describe("startCollectionManager", () => {
  it("runs cleanup once when collections are disabled on startup", async () => {
    const { map } = makeStore(["[Unifideck] Alpha"]);
    window.localStorage.setItem(COLLECTIONS_ENABLED_KEY, "0");

    const handle = startCollectionManager();

    // Wait for the async waitForCollections() promise chain to resolve
    await new Promise((r) => setTimeout(r, 10));

    expect(window.localStorage.getItem(COLLECTIONS_CLEANED_KEY)).toBe("1");
    expect(Array.from(map.values()).map((c) => c.displayName)).not.toContain("[Unifideck] Alpha");

    handle.remove();
  });
});
