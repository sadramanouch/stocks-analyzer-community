# Prospective paper measurement and financial source coverage

The `/invest` workspace combines current required annual financial inputs with
fresh original public purchase disclosures. A documented company is a research
candidate, not a validated profitable trader or an instruction to buy. The
separate ledger is virtual USD capital and simulated reference fills. No broker
connection, real order placement or verified historical account return exists.

## Measurement contract

`POST /api/forward-performance/capture` observes the ledger at server time. It
does not backdate, fund, fill or fetch prices. Checkpoints are immutable and freeze
every ledger payload/hash and the supporting decision/quote evidence. Reading a
checkpoint replays its exact Decimal cash/share arithmetic and checks hashes.
Authenticated exports are available at `/api/forward-performance/checkpoints/{id}`.

Checkpoints are also captured after new workspace funding, fills and reference
marks. Weekdays at 17:20 America/New_York the scheduler attempts source-reference
marks for holdings, then records a checkpoint. Failed source/action checks remain
blocked. This is a scheduled observation, not a guarantee of complete data.

For an interval without external cash flows, reference return is
`ending reference NAV / starting reference NAV - 1`. Cash-only external flow
boundaries can be reconstructed exactly from the ledger: multiply each subperiod's
NAV ratio, reset the denominator to pre-flow NAV plus the signed external flow,
and geometrically link the subperiods. Deposits do not become profit; withdrawals
do not become loss. Fees/slippage from frozen fills stay in NAV.

The calculation blocks an interval when its ledger is not an exact extension,
an added event predates the earlier observation, its initial capital is zero,
a held position lacks newer dated quotes, or a cash flow while shares are held
lacks a separately observed boundary valuation. Imported assertions cannot
impersonate source-linked workspace fills. Missing inputs produce `null` returns.
One checkpoint provides no interval return. A cash-only series provides no
strategy performance evidence. No annualized, benchmark, verified account,
trader-profitability or future-profit claim is derived from these checkpoints.

This is **paper reference arithmetic**, not GIPS compliance or an audited total
economic return. Undiscovered actions, delayed prices, real execution costs,
taxes and broker reconciliation require additional evidence. Method reference:
[CFA Institute's performance standards overview](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-of-the-global-investment-performance-standards).

## Financial source custody and expansion

Company-facts responses and the official SEC ticker-to-issuer mapping are archived
as original compressed bytes, including denial responses, with observation dates
and SHA-256 hashes. Certification through this path requires four exact annual
USD facts (revenue, net income, operating cash flow, capex), their original
periods, forms, accessions, issuer mapping and metadata. Provider-only values
remain unverified. The independently reviewed statement path remains explicit
and applies only to its four checked annual fields.

`GET /api/research/coverage` reports the original portfolio symbol universe,
readiness and the next oldest attempted eligible common equities. The expansion
queue excludes other securities and symbols outside that approved universe.
`POST /api/research/coverage/refresh` processes at most 25 symbols, using the
existing resumable/cancellable research worker. Each pass rebuilds an immutable
saved-list edition. HTTP success is not equivalent to documented financials.
The daily 16:50 New York refresh maintains the reviewed starter set and up to 25 documented original-portfolio
symbols, and adds 10
rotating eligible original-portfolio symbols. SEC company-facts 403/429 responses
pause additional company-facts requests for 15 minutes.

Original receipts can be downloaded through the authenticated
`/api/research/coverage/sources/{id}` endpoint. The response fingerprint is checked
before export. [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
describes the source and access policy. Original saved rows, source documents,
prior editions and audit inputs are preserved.

## Remaining external inputs

Quiver and Nansen API keys must be configured as server secrets through the
existing deployment account; do not paste them into chat, commit them, or put them
in browser bundles. A configured key still needs live schema, coverage and history
validation. Existing sample/latest feeds are not complete trader histories.

A real-account workflow needs the broker name, complete funding/fill/fee/action
records and dated account statements, followed by independent reconciliation.
The normalized paper-ledger import is a reviewed assertion until source records
corroborate it. A comparable benchmark needs synchronized prices and reconciled
actions; prospective strategy evaluation needs actual observed paper decisions
over time. Neither can be manufactured from an empty strategy portfolio.
