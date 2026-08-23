// @vitest-environment jsdom
/**
 * Tests for the Force-Compat capture that runs on the Play press.
 *
 * The behaviour that matters is the ordering: the pin must be persisted
 * before Steam's setting is cleared, and the two "leave it alone" cases
 * (Steam Linux Runtime, Steam's global default) must clear nothing.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@decky/api", () => ({ call: vi.fn() }));
// useRPC pulls in React hooks, which this transform can't load; the only
// thing needed here is the pure envelope unwrapper. The stub mirrors the
// real guard — an envelope needs BOTH keys, so the flat
// `{success, tool_name, …}` payload this RPC returns passes through.
vi.mock("../api/useRPC", () => ({
  unwrapRpcEnvelope: (raw: unknown) =>
    raw && typeof raw === "object" && "success" in raw && "data" in raw
      ? (raw as Record<string, unknown>).data
      : raw,
}));

import { call } from "@decky/api";
import { captureForceCompatPin } from "./protonPin";

const mockCall = call as unknown as ReturnType<typeof vi.fn>;

const GAME_KEY = "epic:0a2d9f0e";
const APPID = 2780953100;

/** Install a SpecifyCompatTool spy and return it. */
function stubSteamClient(): ReturnType<typeof vi.fn> {
  const specify = vi.fn();
  (window as unknown as { SteamClient?: unknown }).SteamClient = {
    Apps: { SpecifyCompatTool: specify },
  };
  return specify;
}

/** Route a `call` mock by RPC name so ordering assertions stay readable. */
function routeCalls(ctx: unknown): void {
  mockCall.mockImplementation(async (route: string) => {
    if (route === "get_compat_tool_for_game") return ctx;
    if (route === "save_proton_setting") return { success: true };
    throw new Error(`unexpected route ${route}`);
  });
}

describe("captureForceCompatPin", () => {
  beforeEach(() => {
    mockCall.mockReset();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("pins a forced Proton and clears it in Steam", async () => {
    const specify = stubSteamClient();
    routeCalls({
      success: true,
      tool_name: "proton_9",
      appid_unsigned: APPID,
    });

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: "proton_9" });
    expect(mockCall).toHaveBeenCalledWith("save_proton_setting", GAME_KEY, "proton_9");
    expect(specify).toHaveBeenCalledWith(APPID, "");
  });

  it("saves the pin before clearing Steam's setting", async () => {
    const order: string[] = [];
    const specify = vi.fn(() => {
      order.push("clear");
    });
    (window as unknown as { SteamClient?: unknown }).SteamClient = {
      Apps: { SpecifyCompatTool: specify },
    };
    mockCall.mockImplementation(async (route: string) => {
      if (route === "get_compat_tool_for_game") {
        return { success: true, tool_name: "proton_9", appid_unsigned: APPID };
      }
      order.push("save");
      return { success: true };
    });

    await captureForceCompatPin(GAME_KEY);

    expect(order).toEqual(["save", "clear"]);
  });

  it("does nothing when no compat tool is forced", async () => {
    const specify = stubSteamClient();
    routeCalls({ success: true, appid_unsigned: APPID });

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: null, reason: "none-set" });
    expect(mockCall).toHaveBeenCalledTimes(1);
    expect(specify).not.toHaveBeenCalled();
  });

  it("leaves a forced Steam Linux Runtime alone", async () => {
    const specify = stubSteamClient();
    routeCalls({
      success: true,
      tool_name: "steamlinuxruntime_sniper",
      is_linux_runtime: true,
      appid_unsigned: APPID,
    });

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: null, reason: "linux-runtime" });
    expect(mockCall).toHaveBeenCalledTimes(1);
    expect(specify).not.toHaveBeenCalled();
  });

  it("does not adopt Steam's global default as a per-game pin", async () => {
    const specify = stubSteamClient();
    routeCalls({
      success: true,
      tool_name: "Proton-CachyOS Latest",
      is_global_default: true,
      appid_unsigned: APPID,
    });

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: null, reason: "global-default" });
    expect(mockCall).toHaveBeenCalledTimes(1);
    expect(specify).not.toHaveBeenCalled();
  });

  it("swallows RPC failures so the launch still proceeds", async () => {
    const specify = stubSteamClient();
    mockCall.mockRejectedValue(new Error("backend down"));

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: null, reason: "failed" });
    expect(specify).not.toHaveBeenCalled();
  });

  it("still reports the pin when Steam exposes no appid to clear", async () => {
    (window as unknown as { SteamClient?: unknown }).SteamClient = undefined;
    routeCalls({ success: true, tool_name: "proton_9" });

    const outcome = await captureForceCompatPin(GAME_KEY);

    expect(outcome).toEqual({ pinned: "proton_9" });
    expect(mockCall).toHaveBeenCalledWith("save_proton_setting", GAME_KEY, "proton_9");
  });
});
