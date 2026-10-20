import test from "node:test";
import assert from "node:assert/strict";
import { filterResearchCandidates, qualifiesForPurchaseReview } from "../lib/daily-research.ts";

const ready = { ticker: "READY", company_name: "Source Company", state: "fresh_purchase_and_documented_inputs", fresh_purchases: 2, financial_gate_reasons: [], watched: true };
const financial = { ...ready, ticker: "FIN", state: "documented_inputs_no_fresh_purchase", fresh_purchases: 0, watched: false };
const blocked = { ...ready, ticker: "OLD", state: "needs_evidence", financial_gate_reasons: ["Stale financial evidence."] };

test("purchase review never promotes a filing when financial evidence is blocked", () => {
  assert.deepEqual(filterResearchCandidates([blocked, financial, ready], "purchases"), [ready]);
  assert.equal(qualifiesForPurchaseReview({ ...blocked, state: ready.state }), false);
});

test("unknown states and invalid or empty purchase counts never become review candidates", () => {
  for (const state of ["needs_evidence", "unknown", "documented_inputs_no_fresh_purchase"])
    assert.equal(qualifiesForPurchaseReview({ ...ready, state }), false);
  for (const fresh_purchases of [0, -1, NaN, Infinity, 1.5])
    assert.equal(qualifiesForPurchaseReview({ ...ready, fresh_purchases }), false);
  assert.deepEqual(filterResearchCandidates([], "purchases"), []);
});

test("research and watched views keep their separate scopes without changing source records", () => {
  const original = structuredClone([ready, financial, blocked]);
  assert.deepEqual(filterResearchCandidates(original, "financial"), [ready, financial]);
  assert.deepEqual(filterResearchCandidates(original, "watched"), [ready, blocked]);
  assert.deepEqual(filterResearchCandidates(original, "all", "  source company  "), original);
  assert.deepEqual(filterResearchCandidates(original, "all", "fin"), [financial]);
  assert.deepEqual(original, [ready, financial, blocked]);
});
