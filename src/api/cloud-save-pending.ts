/**
 * Tracks manual cloud-save operations awaiting their completion event.
 *
 * Manual Download/Upload are fire-and-forget on the backend (they return
 * immediately and finish via a CLOUD_SYNC_* event). The modal that triggers
 * the op closes right away, so the success/failure toast is shown by the
 * still-mounted CloudSaveButton when the event arrives. This module lets the
 * two communicate: the modal MARKS an op pending; the button CONSUMES it so it
 * only toasts for user-initiated syncs (never the automatic on-launch pull).
 */
type Direction = "down" | "up";

const pending = new Set<string>();

const key = (store: string, gameId: string, dir: Direction) =>
  `${dir}:${store}:${gameId}`;

export function markCloudOpPending(
  store: string,
  gameId: string,
  dir: Direction,
): void {
  pending.add(key(store, gameId, dir));
}

/** Returns true (and clears) iff a manual op was pending for this game/dir. */
export function consumeCloudOpPending(
  store: string,
  gameId: string,
  dir: Direction,
): boolean {
  const k = key(store, gameId, dir);
  const had = pending.has(k);
  pending.delete(k);
  return had;
}
