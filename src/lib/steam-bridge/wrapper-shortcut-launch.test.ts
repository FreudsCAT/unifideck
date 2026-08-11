// @vitest-environment jsdom
/**
 * Shared wrapper-store shortcut launching.
 *
 * This module was extracted from `ubisoftShortcutLaunch.ts`, which is a
 * shipped auth path. These tests exist to retire the risk of that
 * extraction: they pin the behaviours that were previously only verifiable
 * on a real device, and they exercise the Ubisoft *and* Battle.net configs
 * through the same code so a future divergence shows up here.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@decky/api", () => ({ call: vi.fn() }));

// The real createTemporaryShortcut polls Steam for a gameid, which a stub
// never supplies. These tests are about the wrapper logic, not that poll.
vi.mock("./temp-shortcut", () => ({
  createTemporaryShortcut: vi.fn().mockResolvedValue(4242),
  scheduleTemporaryShortcutCleanup: vi.fn(),
}));

import { call } from "@decky/api";
import { createTemporaryShortcut } from "./temp-shortcut";
import {
  buildTemporaryLaunchOptions,
  extractUserParams,
  launchWrapperAuthViaShortcut,
  launchWrapperViaShortcut,
  wrapperActionEnv,
  type WrapperShortcutConfig,
} from "./wrapper-shortcut-launch";

const UBISOFT: WrapperShortcutConfig = {
  storeId: "ubisoft",
  displayName: "Ubisoft Connect",
  logTag: "[U]",
  authShortcutStoreId: "ubisoft:upc-auth",
  authContextRoute: "get_ubisoft_auth_shortcut_context",
  actionEnvVar: "UNIFIDECK_UBISOFT_ACTION",
  prefixEnvVar: "UNIFIDECK_UBISOFT_PREFIX_NAME",
  authPrefixName: ".upc-auth",
};

const BATTLENET: WrapperShortcutConfig = {
  storeId: "battlenet",
  displayName: "Battle.net",
  logTag: "[B]",
  authShortcutStoreId: "battlenet:bnet-auth",
  authContextRoute: "get_battlenet_auth_shortcut_context",
  actionEnvVar: "UNIFIDECK_BATTLENET_ACTION",
};

interface SteamStub {
  RunGame: ReturnType<typeof vi.fn>;
  SetShortcutLaunchOptions: ReturnType<typeof vi.fn>;
  SpecifyCompatTool: ReturnType<typeof vi.fn>;
  AddShortcut: ReturnType<typeof vi.fn>;
  TerminateApp: ReturnType<typeof vi.fn>;
}

let steam: SteamStub;

function registerApp(appId: number): void {
  (window as unknown as { appStore: unknown }).appStore = {
    m_mapApps: { get: (id: number) => (id === appId ? { display_name: "x" } : undefined) },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  steam = {
    RunGame: vi.fn(),
    SetShortcutLaunchOptions: vi.fn(),
    SpecifyCompatTool: vi.fn(),
    AddShortcut: vi.fn().mockResolvedValue(4242),
    TerminateApp: vi.fn(),
  };
  (window as unknown as { SteamClient: unknown }).SteamClient = { Apps: steam };
  (window as unknown as { appStore: unknown }).appStore = { m_mapApps: { get: () => undefined } };
});

// ---------------------------------------------------------------------------
// launch-option composition — the part users notice when it breaks
// ---------------------------------------------------------------------------

describe("extractUserParams", () => {
  it("keeps the user's wrappers and drops our tokens", () => {
    const result = extractUserParams(
      'mangohud UNIFIDECK_UBISOFT_ACTION=auth "ubisoft:123" /path/launcher gamemoderun #%command%',
      "ubisoft:123",
      "/path/launcher",
    );
    expect(result).toBe("mangohud gamemoderun");
  });

  it("strips every UNIFIDECK_ token regardless of store", () => {
    expect(
      extractUserParams("UNIFIDECK_BATTLENET_ACTION=install mangohud", "battlenet:hs_beta"),
    ).toBe("mangohud");
  });

  it("handles quoted values", () => {
    expect(extractUserParams('UNIFIDECK_X="a b" mangohud', "s:1")).toBe("mangohud");
  });

  it("returns empty when there is nothing of the user's", () => {
    expect(extractUserParams("ubisoft:123", "ubisoft:123")).toBe("");
  });
});

describe("buildTemporaryLaunchOptions", () => {
  it("puts the store id first, then env, then user params", () => {
    const options = buildTemporaryLaunchOptions(
      {
        success: true,
        store_game_id: "ubisoft:123",
        current_launch_options: "ubisoft:123 mangohud",
        launcher_path: "/l",
      },
      { UNIFIDECK_UBISOFT_ACTION: "auth" },
      "ubisoft:upc-auth",
    );
    expect(options).toBe("ubisoft:upc-auth UNIFIDECK_UBISOFT_ACTION=auth mangohud");
  });

  it("omits empty env values", () => {
    expect(
      buildTemporaryLaunchOptions({ success: true, store_game_id: "s:1" }, { A: "", B: "b" }),
    ).toBe("s:1 B=b");
  });
});

describe("wrapperActionEnv", () => {
  it("threads the prefix hint only for Ubisoft auth", () => {
    expect(wrapperActionEnv(UBISOFT, "auth")).toEqual({
      UNIFIDECK_UBISOFT_ACTION: "auth",
      UNIFIDECK_UBISOFT_PREFIX_NAME: ".upc-auth",
    });
    expect(wrapperActionEnv(UBISOFT, "install")).toEqual({
      UNIFIDECK_UBISOFT_ACTION: "install",
    });
  });

  it("omits it entirely for Battle.net, which has no prefix env var", () => {
    expect(wrapperActionEnv(BATTLENET, "auth")).toEqual({
      UNIFIDECK_BATTLENET_ACTION: "auth",
    });
  });
});

// ---------------------------------------------------------------------------
// launching
// ---------------------------------------------------------------------------

describe("launchWrapperViaShortcut", () => {
  it("reports a structured failure when no appid resolves", async () => {
    vi.mocked(call).mockResolvedValue({ success: true, data: {} });
    const result = await launchWrapperViaShortcut(UBISOFT, "ubisoft:1");
    expect(result.success).toBe(false);
    expect(result.error).toBe("Context unavailable");
  });

  it("runs a registered shortcut directly", async () => {
    registerApp(99);
    vi.mocked(call).mockResolvedValue({
      success: true,
      data: { appid_unsigned: 99, current_launch_options: "ubisoft:1" },
    });
    const result = await launchWrapperViaShortcut(UBISOFT, "ubisoft:1");
    expect(result.success).toBe(true);
    expect(steam.RunGame).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createTemporaryShortcut)).not.toHaveBeenCalled();
  });

  it("falls back to a temporary shortcut when Steam has not registered it", async () => {
    // Steam only loads shortcuts.vdf at startup, so a shortcut written this
    // session is invisible and RunGame on its appid fails.
    vi.mocked(call).mockResolvedValue({
      success: true,
      data: { appid_unsigned: 77, launcher_path: "/l" },
    });
    const result = await launchWrapperViaShortcut(BATTLENET, "battlenet:hs_beta");
    expect(result.success).toBe(true);
    expect(vi.mocked(createTemporaryShortcut)).toHaveBeenCalledTimes(1);
  });

  it("does not restore launch options when skipStateRestore is set", async () => {
    // The auth shortcut's temp options ARE its canonical options; restoring
    // the empty originals produced a tile that opened then instantly closed.
    registerApp(55);
    vi.mocked(call).mockResolvedValue({
      success: true,
      data: { appid_unsigned: 55, current_launch_options: "" },
    });
    vi.useFakeTimers();
    try {
      await launchWrapperViaShortcut(UBISOFT, "ubisoft:upc-auth", {}, undefined, true);
      vi.advanceTimersByTime(10_000);
    } finally {
      vi.useRealTimers();
    }
    // Only the pre-launch set, never a restore back to "".
    expect(steam.SetShortcutLaunchOptions).toHaveBeenCalledTimes(1);
    expect(steam.SetShortcutLaunchOptions.mock.calls[0][1]).not.toBe("");
  });

  it("contextOverride wins over the compat-tool lookup", async () => {
    registerApp(12);
    vi.mocked(call).mockResolvedValue({ success: true, data: { appid_unsigned: 999 } });
    const result = await launchWrapperViaShortcut(
      UBISOFT,
      "ubisoft:upc-auth",
      {},
      { appid_unsigned: 12 },
    );
    expect(result.success).toBe(true);
  });

  it("surfaces missing Steam APIs rather than throwing", async () => {
    registerApp(3);
    (window as unknown as { SteamClient: unknown }).SteamClient = { Apps: {} };
    vi.mocked(call).mockResolvedValue({ success: true, data: { appid_unsigned: 3 } });
    const result = await launchWrapperViaShortcut(UBISOFT, "ubisoft:1");
    expect(result.success).toBe(false);
    expect(result.error).toContain("Steam launch APIs");
  });
});

describe("launchWrapperAuthViaShortcut", () => {
  it("uses the store's own auth context route", async () => {
    registerApp(21);
    vi.mocked(call).mockResolvedValue({ success: true, data: { appid_unsigned: 21 } });
    await launchWrapperAuthViaShortcut(BATTLENET);
    expect(vi.mocked(call).mock.calls[0][0]).toBe("get_battlenet_auth_shortcut_context");
  });

  it("re-supplies the action env var, which options-building strips", async () => {
    registerApp(31);
    vi.mocked(call).mockResolvedValue({ success: true, data: { appid_unsigned: 31 } });
    await launchWrapperAuthViaShortcut(UBISOFT);
    const options = steam.SetShortcutLaunchOptions.mock.calls[0][1] as string;
    expect(options).toContain("UNIFIDECK_UBISOFT_ACTION=auth");
    expect(options).toContain("ubisoft:upc-auth");
  });

  it("fails cleanly when the auth shortcut cannot be resolved", async () => {
    vi.mocked(call).mockResolvedValue({ success: true, data: { error: "nope" } });
    const result = await launchWrapperAuthViaShortcut(BATTLENET);
    expect(result.success).toBe(false);
    expect(result.error).toBe("nope");
  });
});

// ---------------------------------------------------------------------------
// the configs themselves
// ---------------------------------------------------------------------------

describe("store configs stay distinct", () => {
  it("ubisoft and battlenet differ in every identifying field", async () => {
    const { UBISOFT_SHORTCUT_CONFIG } = await import("../../utils/ubisoftShortcutLaunch");
    const { BATTLENET_SHORTCUT_CONFIG } = await import("../../utils/battlenetShortcutLaunch");
    expect(UBISOFT_SHORTCUT_CONFIG.storeId).not.toBe(BATTLENET_SHORTCUT_CONFIG.storeId);
    expect(UBISOFT_SHORTCUT_CONFIG.actionEnvVar).not.toBe(BATTLENET_SHORTCUT_CONFIG.actionEnvVar);
    expect(UBISOFT_SHORTCUT_CONFIG.authContextRoute).not.toBe(
      BATTLENET_SHORTCUT_CONFIG.authContextRoute,
    );
    // Both wrapper stores thread a prefix name, and each names its own.
    // Battle.net's is load-bearing: without it the launcher falls back to
    // ``ctx.game_id`` and signs the user into an empty ``bnet-auth`` while
    // the client lives in ``.bnet-auth``.
    expect(UBISOFT_SHORTCUT_CONFIG.prefixEnvVar).toBeDefined();
    expect(BATTLENET_SHORTCUT_CONFIG.prefixEnvVar).toBeDefined();
    expect(UBISOFT_SHORTCUT_CONFIG.prefixEnvVar).not.toBe(BATTLENET_SHORTCUT_CONFIG.prefixEnvVar);
    expect(UBISOFT_SHORTCUT_CONFIG.authPrefixName).not.toBe(
      BATTLENET_SHORTCUT_CONFIG.authPrefixName,
    );
  });
});
