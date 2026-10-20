import test from "node:test";
import assert from "node:assert/strict";
import { fcfScore } from "../lib/fcf-score.ts";

test("missing and non-finite FCF never receive maximum valuation points", () => {
  for (const input of [undefined, null, NaN, Infinity, -Infinity]) {
    assert.deepEqual(fcfScore(input), { value: null, points: 3 });
  }
});

test("known FCF values retain the declared score boundaries", () => {
  for (const [value, points] of [[-.01, 0], [0, 1], [.02, 3], [.04, 5], [.06, 7], [.08, 8]]) {
    assert.deepEqual(fcfScore(value), { value, points });
  }
});
