/**
 * Regression: a successful auth must trigger request_auth_sync.
 *
 * The backend's `request_auth_sync` (SyncService.request_auth_sync)
 * exists specifically so a store becomes available for sync the
 * moment login completes, instead of only after the next restart
 * forces a fresh boot-time availability check. Its own docstring says
 * "Called by AuthDispatcher after store auth" — but nothing actually
 * called it, so a sync run right after signing in silently skipped
 * the just-authenticated store. These tests pin that AuthDispatcher
 * calls it on success and does NOT call it on failure.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockCall = vi.fn();
vi.mock("@decky/api", () => ({
  call: (...args: unknown[]) => mockCall(...args),
}));

type Handler = (payload: unknown) => void;
const subscribers = new Map<string, Set<Handler>>();
vi.mock("../../api/event-bus-client", () => ({
  EventBusClient: {
    bumpToFast: vi.fn(),
    subscribe: (name: string, handler: Handler) => {
      const set = subscribers.get(name) ?? new Set<Handler>();
      set.add(handler);
      subscribers.set(name, set);
      return () => set.delete(handler);
    },
  },
}));

vi.mock("../../api/useRPC", () => ({
  unwrapRpcEnvelope: (raw: unknown) => raw,
}));

vi.mock("../../api/rpc-routes", () => ({
  rpcRoutes: {
    storeAuth: "store_auth",
    requestAuthSync: "request_auth_sync",
  },
}));

vi.mock("../../types/events", () => ({
  Events: {
    STORE_AUTH_COMPLETE: "store_auth_complete",
    STORE_AUTH_FAILED: "store_auth_failed",
  },
}));

const shortcutLaunched = { success: true };
vi.mock("../../utils/authShortcutLaunch", () => ({
  launchEpicAuthViaShortcut: vi.fn(() => Promise.resolve(shortcutLaunched)),
  launchGogAuthViaShortcut: vi.fn(() => Promise.resolve(shortcutLaunched)),
  launchAmazonAuthViaShortcut: vi.fn(() => Promise.resolve(shortcutLaunched)),
  launchMicrosoftAuthViaShortcut: vi.fn(() => Promise.resolve(shortcutLaunched)),
}));

vi.mock("../../utils/ubisoftShortcutLaunch", () => ({
  launchUbisoftAuthViaShortcut: vi.fn(() => Promise.resolve(shortcutLaunched)),
}));

function emit(event: string, payload: unknown): void {
  for (const handler of subscribers.get(event) ?? []) handler(payload);
}

describe("AuthDispatcher", () => {
  beforeEach(() => {
    vi.resetModules();
    mockCall.mockReset();
    mockCall.mockResolvedValue({ success: true }); // default: any later RPC (requestAuthSync) resolves fine
    subscribers.clear();
  });

  it("calls request_auth_sync when auth completes successfully", async () => {
    // store_auth("start") resolves to the slow path (false) so the flow
    // waits for the STORE_AUTH_COMPLETE event rather than the fast path.
    mockCall.mockResolvedValueOnce({ success: false });
    const { AuthDispatcher } = await import("./AuthDispatcher");

    const promise = AuthDispatcher.start("microsoft");
    await Promise.resolve(); // let kickAndLaunch's microtasks settle
    await Promise.resolve();
    emit("store_auth_complete", { store: "microsoft" });
    const result = await promise;

    expect(result.success).toBe(true);
    expect(mockCall).toHaveBeenCalledWith("request_auth_sync", "microsoft");
  });

  it("does not call request_auth_sync when auth fails", async () => {
    mockCall.mockResolvedValueOnce({ success: false });
    const { AuthDispatcher } = await import("./AuthDispatcher");

    const promise = AuthDispatcher.start("epic");
    await Promise.resolve();
    await Promise.resolve();
    emit("store_auth_failed", { store: "epic", error: "denied" });
    const result = await promise;

    expect(result.success).toBe(false);
    expect(mockCall).not.toHaveBeenCalledWith("request_auth_sync", "epic");
  });
});
