/**
 * The size caches must be able to forget.
 *
 * Reported as "the size only updates after a Steam restart". Both caches that
 * hold an install size live in module scope in Steam's SharedJSContext and
 * neither had any invalidation: `useGameSize` relied on its
 * `<appId>:<installed>` key changing, and `overview-enrichment` measured each
 * app at most once per session. A wrapper store reporting "installed" at
 * prefix-creation time wrote the prefix-only number under the very key the
 * finished install would read, and it stuck for the life of the JS context.
 */
import { describe, expect, it, vi } from "vitest";
import {
  invalidateGameSize,
  onGameSizeInvalidated,
  registerGameSizeCache,
} from "./game-size-cache";

describe("game size cache invalidation", () => {
  it("forgets the app in every registered cache", () => {
    const a = vi.fn();
    const b = vi.fn();
    const disposeA = registerGameSizeCache(a);
    const disposeB = registerGameSizeCache(b);

    invalidateGameSize(1234);

    expect(a).toHaveBeenCalledWith(1234);
    expect(b).toHaveBeenCalledWith(1234);
    disposeA();
    disposeB();
  });

  it("notifies subscribers so mounted views refetch", () => {
    const seen: number[] = [];
    const dispose = onGameSizeInvalidated((appId) => seen.push(appId));

    invalidateGameSize(7);
    invalidateGameSize(8);

    expect(seen).toEqual([7, 8]);
    dispose();
  });

  it("stops calling a cache once it is disposed", () => {
    const clear = vi.fn();
    registerGameSizeCache(clear)();

    invalidateGameSize(1);

    expect(clear).not.toHaveBeenCalled();
  });

  it("keeps going when one cache throws", () => {
    // One broken cache must not leave the others holding a stale number —
    // a half-invalidated state is how the two caches would start disagreeing.
    const good = vi.fn();
    const disposeBad = registerGameSizeCache(() => {
      throw new Error("boom");
    });
    const disposeGood = registerGameSizeCache(good);
    const listener = vi.fn();
    const disposeListener = onGameSizeInvalidated(listener);

    expect(() => invalidateGameSize(42)).not.toThrow();

    expect(good).toHaveBeenCalledWith(42);
    expect(listener).toHaveBeenCalledWith(42);
    disposeBad();
    disposeGood();
    disposeListener();
  });

  it("ignores a missing or nonsensical app id", () => {
    const clear = vi.fn();
    const dispose = registerGameSizeCache(clear);

    invalidateGameSize(NaN);
    invalidateGameSize(undefined as unknown as number);

    expect(clear).not.toHaveBeenCalled();
    dispose();
  });

  it("is harmless for an app nothing has measured", () => {
    expect(() => invalidateGameSize(999999)).not.toThrow();
  });
});
