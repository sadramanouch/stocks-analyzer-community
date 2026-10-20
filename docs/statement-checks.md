# Original annual statement cross-checks

`annual-filed-statement-cross-checks-v1` checks annual cash-flow facts in the
original SEC filing against the original SEC company-facts response. These are
two representations of issuer-reported data, not independent upstream audits.
The report does not change legacy capex/FCF, financial eligibility, rankings,
copy rules, orders, trial inputs or account returns.

The initial four-company review uses the exact annual accession already attached
to operating cash flow in research edition 35. Original SEC submissions identify
its primary document. The report binds the complete saved-item hash and unchanged
v1 financial-context hash, and retains original submissions and filing bytes,
URLs, observation times, HTTP status and SHA-256. Source and report tables are
immutable. Replay reconstructs the context and every comparison from originals.
Capture cannot predate the saved inputs or any source response.

## Supported parsing

- Strict XML with external entities/network disabled; entity declarations and
  bodies over 32 MB are rejected. The cover must match the exact ticker, issuer
  CIK, registered common/ordinary class, exchange, annual form and report end.
- Annual contexts span 335–395 days, identify the issuer and contain no entity
  segment/scenario. Units must be a plain ISO4217 USD measure, not a compound
  unit. Quarters, segment values and other currencies do not substitute.
- One cash-flow table must contain operating, investing and financing totals and
  a cash-change reconciliation. Facts outside that table cannot fill a gap.
- Numeric values retain the rendered number, scale, explicit sign, decimals,
  reported rounding unit, context, fact ID, issuer row label and taxonomy concept.
  Supported dot-decimal/fixed-zero transformations are namespace-checked. Unknown
  transforms, continuations, nested inline facts and conflicting contexts require
  review. A missing fact is never a zero. Negative payments require review.
- Comparisons require exact amount, USD, start/end, accession, filing date and
  form in both originals. Contradictory company-facts values cannot be reduced to
  any convenient match. A filed-only or differing value cannot drive subtraction.

Separate measures subtract PP&E, productive assets, other productive assets or
oil/gas payments from reported total OCF in the same annual period and filing.
Payment categories are never added together. Continuing OCF cannot replace a
missing total. If reported total, continuing and discontinued components disagree,
all subtractions are withheld. These amounts are not issuer-adjusted free cash
flow or cash available to shareholders.

The four filings demonstrate why scope matters: GPK tags its row “Capital
Spending” as other productive-asset payments; NEM, T and TT use productive-asset
payments. TT also reports distinct total and continuing OCF. The method retains
both the row label and taxonomy choice without calling them PP&E-only capex.
Their original research evidence gaps remain visible.

## Routes and user flow

The original Number checks panel and selected Research company expose a lazy
“Original filed statement cross-check” panel. Opening it makes no external request.
The explicit capture button collects at most the exact index and annual filing.

- `GET /api/statement-checks/{ticker}?version=N`: replay an existing edition report
  or return its capture scope. For later editions, dated older evidence can be
  displayed with an explicit archived-edition warning and a separate capture
  action. Evidence from a later edition never substitutes for an earlier one.
- `POST /api/statement-checks/{ticker}/capture`: accepts only research version,
  expected scope hash and a 32-hex request ID. Stale scopes/edition changes return
  409, arbitrary numbers return 422, retries reuse the original immutable report.
- `GET /api/statement-checks/reports/{id}`: verify original custody and replay.
- `GET /api/statement-checks/sources/{id}`: exact original bytes as an inert
  attachment with `X-Content-SHA256`, also forwarded by the authenticated web proxy.

Collection uses the configured SEC contact header, no redirects, bounded response
sizes/timeouts, one process worker, a 256 MiB reserve plus 68 MB headroom, and a
15-minute pause after 403/429/503. Successful static filings can be reused with
their original observation times. A dated index can be reused for at most seven
days. Source collection is manual and bounded; the existing scheduled collectors
are unchanged. No subscriptions or credentials are created.

The v1 parser, definitions, scope and calculation rules are frozen for saved
report replay. Future semantics require a separately versioned method.

References: [XBRL Transformation Registry 4](https://www.xbrl.org/Specification/inlineXBRL-transformationRegistry/REC-2020-02-12/inlineXBRL-transformationRegistry-REC-2020-02-12.html),
[Inline XBRL 1.0](https://www.xbrl.org/Specification/inlineXBRL-part1/rec-2010-04-20/inlinexbrl-part1-rec-2010-04-20.html),
and the original SEC filings linked in each report.
