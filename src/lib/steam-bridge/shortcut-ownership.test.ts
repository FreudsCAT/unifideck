// @vitest-environment jsdom
/**
 * Ownership gate for the App-Details patch.
 *
 * The bug these pin: before the game cache loaded, the patch treated EVERY
 * non-Steam shortcut as ours and hid Steam's native Play row plus the entire
 * lower tabbed section. On the device that surfaced it, 27 of 719 shortcuts
 * belonged to EmulationStationDE and friends, so opening one of those right
 * after a plugin_loader restart showed a blanked page.
 *
 * The fix must not regress the reason the optimism existed — our own games
 * must still patch immediately, with no flash of Steam's native UI — so the
 * truth table below is the actual contract, not just examples.
 *
 * Field names verified against the live Steam client over CDP:
 * `GetAppDetails(id).strShortcutExe`, quoted, and `null` for an app whose
 * details are not loaded.
 */
import { describe, it, expect, afterEach } from "vitest";
import { isUnifideckShortcut, shouldPatchShortcut } from "./shortcut-ownership";

const OURS = 3013071580;
const FOREIGN = 2458351848;

const LAUNCHER = '"/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"';
const ES_DE = '"/home/deck/Emulation/tools/launchers/es-de/es-de.sh"';

/** Install a fake `appDetailsStore` holding the given exes. */
function withStore(exes: Record<number, unknown> | null): void {
  const win = window as unknown as { appDetailsStore?: unknown };
  if (exes === null) {
    delete win.appDetailsStore;
    return;
  }
  win.appDetailsStore = {
    GetAppDetails: (id: number) => (id in exes ? { strShortcutExe: exes[id] } : null),
  };
}

afterEach(() => withStore(null));

describe("isUnifideckShortcut", () => {
  it("recognises our launcher through Steam's surrounding quotes", () => {
    withStore({ [OURS]: LAUNCHER });
    expect(isUnifideckShortcut(OURS)).toBe(true);
  });

  it("recognises a relocated homebrew root", () => {
    withStore({ [OURS]: '"/mnt/ssd/homebrew/plugins/Unifideck/bin/unifideck-launcher"' });
    expect(isUnifideckShortcut(OURS)).toBe(true);
  });

  it("rejects someone else's shortcut", () => {
    withStore({ [FOREIGN]: ES_DE });
    expect(isUnifideckShortcut(FOREIGN)).toBe(false);
  });

  it("says nothing when details are not loaded for this app", () => {
    // Steam returns null rather than an empty object — this is why "unknown"
    // has to be a distinct answer from "not ours".
    withStore({ [OURS]: LAUNCHER });
    expect(isUnifideckShortcut(FOREIGN)).toBeNull();
  });

  it("says nothing when there is no store at all", () => {
    withStore(null);
    expect(isUnifideckShortcut(OURS)).toBeNull();
  });

  it("says nothing when the exe field is missing or not a string", () => {
    withStore({ [OURS]: undefined, [FOREIGN]: 42 });
    expect(isUnifideckShortcut(OURS)).toBeNull();
    expect(isUnifideckShortcut(FOREIGN)).toBeNull();
  });

  it("survives a store that throws for unknown ids", () => {
    (window as unknown as { appDetailsStore?: unknown }).appDetailsStore = {
      GetAppDetails: () => {
        throw new Error("no such app");
      },
    };
    expect(isUnifideckShortcut(OURS)).toBeNull();
  });
});

describe("shouldPatchShortcut", () => {
  it("skips a foreign shortcut before the cache loads", () => {
    // The regression itself: this returned true, blanking their page.
    withStore({ [FOREIGN]: ES_DE });
    expect(shouldPatchShortcut(FOREIGN, false, false)).toBe(false);
  });

  it("patches our game before the cache loads, so nothing flashes", () => {
    withStore({ [OURS]: LAUNCHER });
    expect(shouldPatchShortcut(OURS, false, false)).toBe(true);
  });

  it("stays optimistic when neither signal has an opinion", () => {
    withStore(null);
    expect(shouldPatchShortcut(OURS, false, false)).toBe(true);
  });

  it("defers to the cache once it is authoritative", () => {
    // Even against a contradicting exe probe: the cache is the source of
    // truth, and a game removed from our library must stop being patched.
    withStore({ [OURS]: LAUNCHER });
    expect(shouldPatchShortcut(OURS, true, false)).toBe(false);
    withStore({ [FOREIGN]: ES_DE });
    expect(shouldPatchShortcut(FOREIGN, true, true)).toBe(true);
  });
});
