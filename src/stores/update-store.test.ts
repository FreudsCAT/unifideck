/**
 * Tests for the shared update-state store.
 *
 * This is what every Update affordance reads — the App-Details Play
 * section, the QAM Installed rows, and the pre-launch prompt — so a bug
 * here shows up as the three disagreeing about whether a game is stale.
 *
 * The two behaviours worth pinning are both about the *disappearance*
 * case, which is easy to get wrong by merging instead of replacing:
 * the backend sweep emits a store's COMPLETE current set, so an id that
 * is no longer in it has had its update applied and must stop showing.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

const mockCall = vi.fn();
vi.mock("@decky/api", () => ({ call: (...args: unknown[]) => mockCall(...args) }));

// The store subscribes on first use; hand back a controllable handler.
let busHandler: ((payload: unknown) => void) | null = null;
const mockUnsub = vi.fn();
vi.mock("../api/event-bus-client", () => ({
  EventBusClient: {
    subscribe: (_name: string, handler: (payload: unknown) => void) => {
      busHandler = handler;
      return mockUnsub;
    },
  },
}));

import { UpdateStore } from "./update-store";

/** Envelope the backend wrapper would produce for a plain dict return. */
function envelope(data: unknown) {
  return { success: true, error: null, data };
}

// The store is a process-wide singleton that subscribes to the bus ONCE,
// on first use — so capture the handler once here rather than per test.
// (Re-subscribing later is a no-op, which is exactly the production
// behaviour: one subscription however many components mount.)
UpdateStore.subscribe(() => {});

describe("UpdateStore", () => {
  beforeEach(() => {
    mockCall.mockReset();
  });

  it("hydrates from get_available_updates", async () => {
    mockCall.mockResolvedValue(envelope({ epic: ["Sugar"] }));

    await UpdateStore.refresh();

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(true);
    expect(UpdateStore.hasUpdate("epic", "Potoo")).toBe(false);
  });

  it("treats an unknown store as 'no update', not as an error", async () => {
    mockCall.mockResolvedValue(envelope({}));

    await UpdateStore.refresh();

    expect(UpdateStore.hasUpdate("gog", "1549126051")).toBe(false);
  });

  it("survives a failed RPC without wiping what it knows", async () => {
    mockCall.mockResolvedValue(envelope({ epic: ["Sugar"] }));
    await UpdateStore.refresh();

    mockCall.mockRejectedValue(new Error("backend down"));
    await UpdateStore.refresh();

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(true);
  });

  it("notifies subscribers and applies a live event", async () => {
    mockCall.mockResolvedValue(envelope({}));
    const listener = vi.fn();
    const unsub = UpdateStore.subscribe(listener);
    await Promise.resolve();

    busHandler?.({ store: "epic", game_ids: ["Sugar"] });

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(true);
    expect(listener).toHaveBeenCalled();
    unsub();
  });

  it("REPLACES a store's list on an event rather than merging", async () => {
    mockCall.mockResolvedValue(envelope({ epic: ["Sugar", "Potoo"] }));
    await UpdateStore.refresh();
    UpdateStore.subscribe(() => {});
    await Promise.resolve();

    // Sugar was updated; the sweep now reports only Potoo.
    busHandler?.({ store: "epic", game_ids: ["Potoo"] });

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(false);
    expect(UpdateStore.hasUpdate("epic", "Potoo")).toBe(true);
  });

  it("ignores a malformed event instead of clearing state", async () => {
    mockCall.mockResolvedValue(envelope({ epic: ["Sugar"] }));
    await UpdateStore.refresh();
    UpdateStore.subscribe(() => {});
    await Promise.resolve();

    busHandler?.({ store: "epic" });
    busHandler?.(null);
    busHandler?.({ game_ids: ["Sugar"] });

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(true);
  });

  it("clears a game locally the moment its update is queued", async () => {
    mockCall.mockResolvedValue(envelope({ epic: ["Sugar", "Potoo"] }));
    await UpdateStore.refresh();

    UpdateStore.clearGame("epic", "Sugar");

    expect(UpdateStore.hasUpdate("epic", "Sugar")).toBe(false);
    expect(UpdateStore.hasUpdate("epic", "Potoo")).toBe(true);
  });

  it("returns false for a missing store or game id", () => {
    expect(UpdateStore.hasUpdate(undefined, "Sugar")).toBe(false);
    expect(UpdateStore.hasUpdate("epic", undefined)).toBe(false);
  });
});
