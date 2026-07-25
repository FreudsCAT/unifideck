/**
 * Read the live logged-in Steam user and push it to the backend.
 *
 * The frontend runs *inside* the Steam client, so `window.App.m_CurrentUser`
 * is the ONLY 100%-correct source of the active account. The backend
 * otherwise falls back to disk heuristics that can pick the wrong
 * `userdata/<id>` on multi-account decks — writing `shortcuts.vdf` where the
 * running client never reads it ("synced N games, Steam shows 0"). Pushing the
 * live id here lets the backend resolve the correct per-user paths.
 *
 * The pure `steam64ToAccountId` conversion is exported separately (no
 * `@decky/api` dependency) so it is unit-testable under vitest, mirroring how
 * `collection-manager.ts` stays runtime-free for its tests.
 */
import { call } from "@decky/api";
import { rpcRoutes } from "../../api/rpc-routes";

/**
 * Convert a SteamID64 string to the 32-bit account id Steam uses as the
 * `userdata/<id>` folder name (`steamID64 & 0xFFFFFFFF`).
 *
 * MUST use BigInt: a SteamID64 exceeds 2^53, so `Number(s) & 0xFFFFFFFF`
 * loses precision and yields a WRONG id (e.g. 225630048 instead of
 * 225630054). Returns `null` for malformed input.
 */
export function steam64ToAccountId(
  steam64: string | null | undefined,
): string | null {
  if (!steam64) return null;
  const trimmed = String(steam64).trim();
  if (!/^\d{6,}$/.test(trimmed)) return null;
  try {
    return String(BigInt(trimmed) & 0xffffffffn);
  } catch {
    return null;
  }
}

/**
 * Read the live logged-in account id from Steam's globals.
 * Tries `window.App.m_CurrentUser.strSteamID` first (confirmed on-device),
 * then `loginStore` as a fallback. Returns `null` if none resolve.
 */
export function readLiveAccountId(): string | null {
  // Use ``globalThis`` (not a bare ``window``) so this never throws a
  // ReferenceError in a non-window context — it just resolves to null.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = globalThis as any;
  const steam64: string | undefined =
    w?.App?.m_CurrentUser?.strSteamID ?? w?.loginStore?.m_strSteamID;
  return steam64ToAccountId(steam64);
}

/**
 * Best effort — on failure the backend just falls back to its disk
 * heuristics, so a missing global or a rejected `call` is a silent no-op.
 */
export async function uploadActiveSteamUser(): Promise<void> {
  try {
    const accountId = readLiveAccountId();
    if (!accountId || accountId === "0") return;
    await call(rpcRoutes.setActiveSteamUser, accountId);
  } catch (e) {
    console.warn("[Unifideck] uploadActiveSteamUser failed", e);
  }
}
