/**
 * Tests for `unwrapRpcEnvelope`, pinning the shape mismatch that made
 * game updates undetectable.
 *
 * Every backend method is wrapped by `@auto_wrap_rpc_methods`, whose
 * `_to_envelope` puts any return value WITHOUT a top-level `success` key
 * inside `data`:
 *
 *   {"has_update": true} -> {success: true, error: null, data: {has_update: true}}
 *
 * `InstalledButtons` called `@decky/api`'s raw `call()` — which does no
 * unwrapping — and then read `res.has_update` straight off the envelope.
 * That is always `undefined`, so `hasUpdate` was permanently false and
 * the Update button could never render, for Epic, GOG or Amazon. Rocket
 * League sat on `++Prime+Update59-CL-520762` with
 * `++Prime+Update59.1-CL-523543` available and still showed "Play".
 *
 * The contrast case matters just as much: a handler that returns a dict
 * ALREADY carrying `success` (like `update_game`) keeps its keys at the
 * top level, which is why the sibling call in the same component worked
 * by accident and hid the bug.
 */
import { describe, it, expect, vi } from "vitest";

// `call` is the only @decky/api binding useRPC pulls in, and the real
// package imports the build-time virtual `@decky/manifest`. These tests
// exercise the pure unwrap helper, which never calls it.
vi.mock("@decky/api", () => ({ call: vi.fn() }));

import { unwrapRpcEnvelope } from "./useRPC";
import { RpcError } from "./rpc-errors";

interface UpdateCheckResponse {
  has_update?: boolean;
}

describe("unwrapRpcEnvelope", () => {
  it("returns the payload nested under data, not the envelope", () => {
    const wire = { success: true, error: null, data: { has_update: true } };

    const res = unwrapRpcEnvelope<UpdateCheckResponse>(wire, {
      route: "check_game_update",
    });

    expect(res.has_update).toBe(true);
    // The exact regression: reading the field off the envelope.
    expect((wire as unknown as UpdateCheckResponse).has_update).toBeUndefined();
  });

  it("reports no update when the backend says so", () => {
    const res = unwrapRpcEnvelope<UpdateCheckResponse>({
      success: true,
      error: null,
      data: { has_update: false },
    });

    expect(Boolean(res.has_update)).toBe(false);
  });

  it("throws on a failed envelope so the caller can fail open", () => {
    expect(() =>
      unwrapRpcEnvelope({
        success: false,
        error: "store_not_registered",
        data: null,
      }),
    ).toThrow(RpcError);
  });

  it("can be told not to throw, for callers that inspect the failure", () => {
    const res = unwrapRpcEnvelope<UpdateCheckResponse | null>(
      { success: false, error: "store_not_registered", data: null },
      { throwing: false },
    );

    expect(res).toBeNull();
  });

  it("passes a non-enveloped response straight through", () => {
    expect(unwrapRpcEnvelope<number[]>([1, 2, 3])).toEqual([1, 2, 3]);
  });
});
