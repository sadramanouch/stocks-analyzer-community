/** Existing FCF policy, with missing and non-finite inputs treated explicitly. */
export function fcfScore(input: number | null | undefined) {
  const value = typeof input === "number" && Number.isFinite(input) ? input : null;
  const points = value === null ? 3 : value < 0 ? 0 : value < .02 ? 1
    : value < .04 ? 3 : value < .06 ? 5 : value < .08 ? 7 : 8;
  return { value, points };
}
