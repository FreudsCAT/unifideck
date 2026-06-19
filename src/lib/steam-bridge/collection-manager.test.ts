// @vitest-environment jsdom
/**
 * Regression tests for the cleanup → collection-deletion flow:
 * Steam's `Delete()` mutates the live `userCollections` Map, which used
 * to skip entries mid-iteration, and the plugin-init sync used to
 * recreate everything the user just wiped.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

// React and the Decky runtime are peer-provided in the Steam webview
// and absent under vitest — stub the two imports that pull them in.
vi.mock("./tab-container", () => ({
  getUnifideckTabs: () => [{ id: "unifideck-alpha", title: "Alpha", position: 0, filters: [] }],
}));
vi.mock("../library-filters", () => ({
  runFilters: () => true,
}));

import {
  deleteAllUnifideckCollections,
  syncUnifideckCollections,
  startCollectionManager,
} from "./collection-manager";

const SUPPRESSION_KEY = "unifideck.collections.suppressed";

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
    GetCollection: vi.fn((id: string) => (id === "type-games" ? { allApps: [] } : map.get(id))),
    GetCollectionIDByUserTag: vi.fn((tag: string) => {
      for (const c of map.values()) if (c.displayName === tag) return c.id;
      return null;
    }),
    NewUnsavedCollection: vi.fn((tag: string) => make(tag)),
  };
  (window as unknown as { collectionStore: unknown }).collectionStore = store;
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

  it("sets the suppression flag so post-cleanup syncs are no-ops", async () => {
    const { store } = makeStore(["[Unifideck] Alpha"]);
    await deleteAllUnifideckCollections();
    expect(window.localStorage.getItem(SUPPRESSION_KEY)).toBe("1");

    store.GetCollection.mockClear();
    store.NewUnsavedCollection.mockClear();
    await syncUnifideckCollections();
    // Suppressed sync bails before even probing the store.
    expect(store.GetCollection).not.toHaveBeenCalled();
    expect(store.NewUnsavedCollection).not.toHaveBeenCalled();
  });
});

describe("startCollectionManager", () => {
  it("clears the suppression flag when a library sync completes", async () => {
    makeStore([]);
    window.localStorage.setItem(SUPPRESSION_KEY, "1");
    const handle = startCollectionManager();
    try {
      window.dispatchEvent(new Event("unifideck-sync-completed"));
      expect(window.localStorage.getItem(SUPPRESSION_KEY)).toBeNull();
    } finally {
      handle.remove();
    }
  });
});
