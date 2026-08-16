/**
 * infoLine — the one grey line under the game-info action row.
 *
 * It carries the genre tags and, when the user picked a Proton for this
 * game, which build it will run on. Both are optional and either can be
 * absent, so the separator has to come from joining rather than from
 * concatenation: a game with no genres must not open with a stray "•",
 * and a game with no Proton note must not end with one.
 */

/** Join the parts of the info line, dropping the ones that are empty. */
export function buildInfoLine(
  genres: readonly string[],
  protonNote: string,
): string {
  return [...genres, protonNote].filter((part) => part !== "").join(" • ");
}
