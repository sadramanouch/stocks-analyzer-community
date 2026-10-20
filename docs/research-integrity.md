# Versioned research and original trade evidence

`/research` preserves the original Stock Analyzer and adds immutable saved-list
editions. Each edition audits every saved ticker. A quote refresh does not
recertify old annual financials or establish an actor's investment performance.

## Identity

The official Nasdaq Trader Nasdaq-listed and other-listed directories supply
the exact listing symbol, exchange code, security title, source generation date,
raw record and file checksum. Classification tests non-common instruments before
common-share wording. Case-sensitive CQS suffixes are not uppercased into ordinary
tickers. Missing, ambiguous, stale, renamed and delisted identities require review.
Financial and real-estate companies still require specialized models.

## Editions and financial evidence

All legacy insert/update/delete paths are captured by database triggers. Legacy
rows remain unversioned rather than inheriting the running code's model version.
Saved-list editions and their contents, identities and reviewed annual statement
references reject updates and deletes at the database level.

The research model recomputes annual FCF as operating cash flow minus absolute
capex, without the legacy 100% clip. USD equity market cap is required for yield;
missing amounts remain unknown. Required annual fields must either match SEC
period/value/accession evidence or match the independently reviewed primary
statements in `backend/reviewed_statements.json`. That starting set covers only
AAPL, MSFT, HRMY and SIG and only four annual fields. Microsoft’s referenced
earnings statements are labeled unaudited. Reported precision is retained.
Apple’s issuer fiscal date is retained separately from the provider's label.

`documented_inputs` also requires a recent input observation, actual quote time,
common-share identity, identified sector/industry and shares/price/cap consistency.
The tolerance is 25%; it detects gross basis errors, not independent exchange
quote verification. Readiness expires even when viewing an immutable old edition.
This status does not validate every framework, a price forecast or expected return.
The original leaderboard remains explicitly labeled as legacy diagnostics; known
non-common securities and extreme clipped FCF records are excluded.

## Refreshing

Use the official identity refresh, full-list audit and bounded source refresh
controls. Up to 100 saved tickers can be explicitly refreshed per job; automatic
selection processes the oldest observation/attempt first. Failed attempts retain
old data and dates. Jobs persist progress, failures and cancellation; interrupted
jobs can resume the remaining tickers. Each batch creates a new full-list edition.
Weekday 21:50 UTC maintenance refreshes official directories and the independently
reviewed starting set, then audits all saved entries. Existing legacy portfolio
allocation and closing remain off unless explicitly enabled.

## Trade histories

The SEC connector uses the existing Form 4 filing index, removes the XSL renderer
segment to obtain original XML, and checks issuer CIK/ticker, accession, dates,
amounts, transaction direction and footnote references. It stores original XML
and hashes plus stable accession/owner-CIK/table/line IDs. Exact repeat imports are
idempotent; differing content under one accession is rejected for reconciliation.
Derivatives, grants/exercises, footnotes, plans and amendments remain contextual or
require review. Amendments are retained separately; affected original lines are
quarantined when their original submission date can be identified.

An indexed filing is not a parsed transaction. Import runs report selected,
attempted, imported and failed documents and never claim complete actor coverage.
HTTP 403/429/503 stops the bounded import instead of hammering the source. Dates
have day precision; `available_at` cannot precede first observation. Source
documents, observations and parsed event identity are immutable; watch flags can
change. Provider-only Congress/insider rows cannot earn source-verified flags.
Crypto remains unassessed until complete wallet flows, token identities, holdings,
liquidity, costs and executable post-observation prices are validated.

Set `EDGAR_USER_AGENT` on the backend with a real application name and contact
email. `QUIVER_API_KEY` and `NANSEN_API_KEY` remain server-only and require the
appropriate provider access; never put them in browser variables. No API
subscription, broker connection, order placement, trader-PnL ranking or hindsight
copy backtest is implied by these connectors.

Each SEC import now refreshes the issuer's current submissions index by its known
CIK before selecting unparsed documents. Existing references are retained;
amended Form 4 references are included. The index's source hash, observation time,
older-history file pointers and added-reference count are archived. Import
progress updates during the run, and individual document failures retain their
accession and validation reason. Original XML is decoded as UTF-8 and its stored
hash matches the retrieved bytes. Actor coverage groups stable reporting-owner
CIKs and reports transaction codes, date ranges, plans and required review. Net
and follower returns remain unknown until economic reconciliation and execution
data are sufficient; activity counts never rank profitability.

## Verification

Backend tests cover primary-value/period/currency checks, missing/invalid inputs,
immutable history, exact XML decimals and stable identities, amendments, access
failures, job interruption and real API routes. Frontend build/type/lint and
authentication/proxy tests cover the shipped application. A production-copy
rehearsal must preserve the original analysis, position and filing rows; production
rollout requires a consistent recovery backup and authenticated hosted smoke tests.


## Original price responses and reconciliation

Historical price refreshes can archive the original Yahoo chart response bytes.
The immutable custody record retains the request bounds, response status, retrieval
time and SHA-256. Denials and unavailable symbols are archived as failures, not
converted to prices or replacement symbols. A missing current timezone lookup
can fall back to a historical chart request, but its response must independently
match the requested symbol, USD, New York timezone and supported instrument type.
Array lengths, dates, OHLC ranges and adjustment values must validate. An action
without a usable price bar is rejected rather than silently dropped.

Original responses are distinct from older SDK-normalized archives. Both remain
available; capturing original bytes does not prove the publisher's prices or
broker fills. The performance audit v3 freezes exact original bytes as hex alongside
all prior input records. Prior audit editions and policies remain immutable.

Reviewed split notices distinguish legal effectiveness from split-adjusted market
trading dates. Later actions rebase both endpoints; an action after a position
closed must not change its stored-price holding ratio. Vendor Stock Splits fields
can contain spin-off factors, so unmatched actions, distributions and conflicting
primary dates block share-only scenarios. Fractional-share treatment, dividends,
fees, quote times and complete economic ownership still require evidence.

The bounded NYSE calendar identifies literal 2026 record dates on scheduled
closures. Those dates are not asserted to be exchange-local quote or execution
dates. No adjacent-day price or date is substituted. Unsupported calendar years
remain unknown. Reconciliation supports all original rows, unavailable-history
search, missing-endpoint and closure-date filters; raw original portfolio figures
and model estimates retain explicit unverified labels.

The independent paper ledger accepts chronological normalized USD events and
previews imports without recording them. Reviewed imports commit atomically,
deduplicate identical IDs and reject conflicts, stale previews, overspending,
short positions and unsupported price bases. Source descriptions remain assertions
until independently verified. Disposable snapshot rehearsals use synthetic events
and never establish actual investor profitability or fund the production ledger.

## Current equity decisions and forward paper testing

The `/invest` workspace joins one immutable financial edition with original SEC
purchase evidence. Qualification requires documented, currently fresh financial
inputs and positive annual FCF. Legacy quant scores are not part of this filter.
This validates required evidence, not fair value, liquidity or all financial risks.
Purchase availability is evaluated both at first observation and now. Footnotes,
plans, unresolved amendments and missing original XML block a fresh purchase flag;
stored fields must reproduce from the original XML receipt.

The focused watchlist starts with up to 25 documented issuers. Sequential SEC
checks run hourly at minute 10, weekdays 07:00–22:00 America/New_York. A pass reads
the last seven calendar days and at most ten unparsed documents per issuer. This
is bounded coverage. Source blocks pause scheduled checks, preserve prior evidence
and expose the failure; a busy shared worker defers the pass. Existing manual SEC
imports retain their original history behavior. Exchange-local scheduling also
keeps daily source refreshes consistent through daylight saving changes.

The separate paper ledger can start with explicitly simulated capital. A preview
freezes the financial edition, policy, primary purchase evidence and quote; it
records no fill. The cash-only policy uses 10 bps fee and 10 bps slippage per side,
a 20% per-position cap, a maximum 24-hour quote age and ten-minute preview expiry.
Both costs appear immediately through an atomic reference mark. Commit rejects
changed evidence or ledger state, and replay is idempotent. A financial-input
strategy and a strategy additionally requiring a fresh purchase remain distinct.
Known unreconciled splits/distributions block crossing share-basis quotes.

Paper marks and decisions retain immutable source snapshots; audit v4 freezes
their exact records alongside all previous inputs. Marked P&L remains a provisional
ledger calculation. Missing action coverage, full benchmark valuations and broker
executions cannot be inferred. No real money moves, no orders are sent, and old
portfolio figures do not become a funded account history.
