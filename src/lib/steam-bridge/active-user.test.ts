import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("@decky/api", () => ({ call: vi.fn() }));

import { call } from "@decky/api";
import { rpcRoutes } from "../../api/rpc-routes";
import { steam64ToAccountId, readLiveAccountId, uploadActiveSteamUser } from "./active-user";

const mockCall = vi.mocked(call);

describe("steam64ToAccountId (BigInt masking)", () => {
  it("converts the real reporter steam64 to the correct account id", () => {
    // The whole bug hinges on this: naive Number()&mask loses precision above
    // 2^53 and yields 225630048 — WRONG. BigInt masking gives 225630054.
    expect(steam64ToAccountId("76561198185895782")).toBe("225630054");
  });

  it("does NOT match the precision-losing naive result", () => {
    expect(steam64ToAccountId("76561198185895782")).not.toBe("225630048");
  });

  it("returns null for malformed / empty input", () => {
    expect(steam64ToAccountId("")).toBeNull();
    expect(steam64ToAccountId(null)).toBeNull();
    expect(steam64ToAccountId(undefined)).toBeNull();
    expect(steam64ToAccountId("not-a-number")).toBeNull();
    expect(steam64ToAccountId("123")).toBeNull(); // too short
  });
});

describe("readLiveAccountId", () => {
  const w = globalThis as unknown as { App?: unknown; loginStore?: unknown };
  afterEach(() => {
    delete w.App;
    delete w.loginStore;
  });

  it("reads window.App.m_CurrentUser.strSteamID first", () => {
    w.App = { m_CurrentUser: { strSteamID: "76561198185895782" } };
    expect(readLiveAccountId()).toBe("225630054");
  });

  it("falls back to loginStore.m_strSteamID", () => {
    w.loginStore = { m_strSteamID: "76561198185895782" };
    expect(readLiveAccountId()).toBe("225630054");
  });

  it("returns null when no global is present", () => {
    expect(readLiveAccountId()).toBeNull();
  });
});

describe("uploadActiveSteamUser", () => {
  const w = globalThis as unknown as { App?: unknown };
  beforeEach(() => mockCall.mockReset());
  afterEach(() => {
    delete w.App;
  });

  it("calls the set_active_steam_user route with the account id", async () => {
    w.App = { m_CurrentUser: { strSteamID: "76561198185895782" } };
    await uploadActiveSteamUser();
    expect(mockCall).toHaveBeenCalledWith(rpcRoutes.setActiveSteamUser, "225630054");
  });

  it("is a silent no-op when the global is absent", async () => {
    await uploadActiveSteamUser();
    expect(mockCall).not.toHaveBeenCalled();
  });

  it("swallows a rejected call (best-effort)", async () => {
    w.App = { m_CurrentUser: { strSteamID: "76561198185895782" } };
    mockCall.mockRejectedValueOnce(new Error("rpc down"));
    await expect(uploadActiveSteamUser()).resolves.toBeUndefined();
  });
});
