# Prospective comparison and broker reconciliation

The `/invest` workspace includes a separately registered daily-close SPY study and isolated broker-record intake. Neither establishes audited account, trader or copy-trading profitability.

## Daily-close study

Registration freezes `prospective-daily-close-spy-v1` and selects the first scheduled NYSE close strictly after registration. The calendar covers 2026, including the November 27 and December 24 early closes. A future calendar needs a new policy version. Weekday collection runs at 09:05 and 18:05 New York time while enabled; failures pause collection and require explicit resume. Collection preserves original HTTP bytes and SHA-256 fingerprints for the issuer's SPY identity/closing price, issuer distribution workbook and Yahoo chart responses. It never replaces the original portfolio's long-range normalized histories.

Issuer closing prices are matched with vendor closes within the issuer's half-cent rounding bound. SPY ticker, currency, CUSIP, ISIN and listing must agree. Declared cash distributions are compared with original vendor dividends and capital-gain events. Blank issuer components remain blank. An anomalous issuer date ordering is retained and blocks any affected interval; no date is corrected by inference.

Every common close requires an immutable observation. Missing baseline/daily closes, changed or backdated ledger records, external funding without boundary valuations, unreconciled actions and source conflicts withhold comparisons. Portfolio NAV uses recorded simulated cash and shares at that same close, including native virtual fill costs. SPY is a declared-distribution reference with hypothetical ex-date reinvestment, gross of execution costs and taxes. Complete economic action coverage remains unverified. Two complete observations can produce reference ratios; cash-only data cannot produce a strategy excess-return claim. Verified account/benchmark fields remain null.

Observation export reparses original issuer facts, validates raw hashes, matches frozen native costs/source inputs, and independently recomputes cash, shares and NAV. Existing forward checkpoint v1 and earlier audit editions remain intact. Performance audit v7 additionally freezes native Flex source bundles as well as the benchmark and normalized broker evidence tables. Prior audit editions remain immutable.

## Broker records

`GET /api/broker-reconciliation/schema` supplies the normalized JSON contract. The initial model covers non-margin USD common equities/ETFs, explicit opening/closing cash, holdings and NAV, and ordered funding/fill/fee/tax/interest/dividend/split records. Date precision, currency, price rounding and source references are explicit. Unknown transfers, corporate actions, currencies or derivatives fail rather than being guessed.

Preview performs no writes. Commit requires the exact preview fingerprint, retains immutable source assertions and deduplicates the file. Imported balances never fund or change the virtual ledger. Both listing and export verify hashes and replay arithmetic; corrupted results are withheld. A statement NAV ratio is shown only for reconciled, asserted-complete intervals with positive opening NAV and no external flows, and is labelled a statement reference. Broker authenticity/completeness and verified account return remain unverified.

## Broker target, researched October 14, 2026

Assuming Canadian residency, IBKR is the preferred integration target for international coverage and API control, with trading/FX/data charges. Its Canadian API restrictions prohibit programmatic submission for Canadian-listed products. Questrade offers zero-commission online US/Canadian stock/ETF trades and a free account-data API, but individual users cannot execute through the API and posted CAD/USD conversion costs are 1.5%. Alpaca Paper Only is globally available with IEX data; Canadian live-account eligibility needs confirmation. Webull Canada advertises zero commissions, but Canadian OpenAPI eligibility has not been established. There is no verified single free option meeting every requirement.

The first native source-review profile now accepts paired IBKR Activity Flex XML exports: an opening cash/holdings snapshot and the subsequent activity range. It remains specification-based and awaits real broker exports for mapping validation; no authenticated broker connection exists. Existing CIBC investments need not be transferred to reconcile records. No account opening, paid subscription or live order is created by this release.

Primary sources are linked from the workspace broker comparison, including [IBKR commissions](https://www.interactivebrokers.ca/en/pricing/commissions-stocks.php), [API restrictions](https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/tws-api-limitations), [Questrade API](https://developer.questrade.com/), [Questrade costs](https://invest.questrade.com/pricing/self-directed-commissions-plans-fees/transaction), [Alpaca paper](https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading), and [Webull API scope](https://developer.webull.com/apis/docs/about-open-api/).

## Native Activity Flex review v1

`/api/broker-flex` provides preview, immutable separate-source commit and source export. Both original byte streams travel as bounded base64 and retain exact source SHA-256 hashes, including encoding/BOM. They never become virtual ledger entries. The two files must cover one matching account; the activity interval starts the day after the opening export ends. The selected export timezone is explicit, and ambiguous/nonexistent DST times are rejected. Broker account IDs are omitted from displayed summaries; only the user-provided account nickname is shown. Source bytes remain private behind existing authentication.

Cash Report uses starting/ending **trade-date** cash; ending settled cash is not used for reconciliation. Opening shares come from actual summary Open Positions, never prior-period price/MTM rows. Contract IDs and supplied CUSIP/ISIN fields are retained; symbol/contract collisions or identity changes block arithmetic qualification. Executions reconcile gross proceeds to side, shares and price, then net cash to signed commission and tax. Positive commission rebates remain positive. Date-only cash entries keep day precision, and execution rows sharing the same reported second are applied as a group; no trade-ID ordering is invented.

The initial profile maps USD, unit-multiplier STK executions and summary positions, plus explicit supported cash transaction types. It requires activity CashReport/OpenPositions/Trades/CashTransactions/CorporateActions/Transfers sections. Missing coverage, duplicate rows, order/lot summaries, corrected fills, short positions, multiple currencies and nonempty corporate actions/transfers/unknown economic sections block qualification. Such uploaded assertions can be preserved for review but never produce verified returns. No NAV/account return is calculated by this profile. Full economic completeness, broker authenticity and real-export validation remain outstanding.

`GET /api/market-history/gaps` distinguishes original portfolio histories that are absent from unsupported current instruments, unresolved historical identity and unavailable/invalid/corrupt source receipts. An HTTP404 is not classified as proof of delisting. No ticker or nearby-date substitution occurs. Existing dated failed receipts remain intact after retry attempts.

Portfolio source passes (`portfolio-source-pass-v1`) are available on Research
and Equity Decisions. The financial pass freezes up to 568 original-portfolio
symbols that need evidence and are eligible common equities in one saved edition.
It runs batches of 25 through the existing normalized source importer, appending
full saved-list editions and exact source receipts. Download success and annual
financial qualification are reported separately. Missing concepts, unmatched
periods/currencies or unconfirmed instruments remain unresolved; models and
forecasts are not certified by matching the four required annual inputs.

The insider pass freezes every currently documented, eligible original-portfolio
common equity, independently of the focused 25-issuer watchlist. It refreshes
current SEC issuer submissions and attempts up to 10 previously unparsed Form 4
XML references per issuer, within the seven-day filing window fixed when the pass
starts. This is bounded issuer discovery, not all-market coverage or complete
actor portfolios. Source observation timestamps are preserved. The existing
purchase, amendment, footnote, instrument, cash-flow and quote policies continue
to decide eligibility; a newly downloaded filing is not automatically actionable.

Only one source pass may be active or paused. Scope/version/universe hash and
ordered outcome receipts are retained and verified against the progress cursor.
Pause/cancel lets the current bounded child finish. Interrupted processes pause
for review and explicit resume; an unfinished batch can be reattempted without
changing earlier source receipts. SEC HTTP 403/429/503 responses pause financial
requests for at least 15 minutes; insider access/index failures stop the pass.
Less than 256 MB of available storage stops further source-pass children and financial source requests. A completed
pass means its frozen bounded requests finished, not complete coverage or verified
returns. Performance audit v8 additionally freezes source-pass state and receipts;
earlier audit editions remain unchanged.

Saved-research v4 names required fields whose supported annual SEC concept provenance is missing. It retains provider-only values as unverified and does not equate continuing-operations or broader productive-asset concepts with total operating cash flow or property/equipment capex. Original editions and numeric qualification rules remain unchanged.

The saved-list rebuild shares at most eight decoded official SEC ticker directories within its transaction. Every use checks the directory compressed-byte digest and source metadata; every company fact still undergoes its own original-byte, unit, accession, period and value checks. Market-provider rate/unavailability errors stop the remaining financial batch.

Completed primary fact comparisons can also be reused in bounded process memory during
subsequent rebuilds. The proof key covers the exact financial inputs/provenance,
original compressed source bytes, all source metadata, official issuer mapping
and filing cutoff day. A source observed after the requested time cannot reuse a
comparison. Changed amounts, bytes, issuer mappings, metadata or cutoff dates force a
full revalidation. Failed comparisons remain unverified; changed evidence forces another comparison. Results are copied, and
the cache holds at most 1,024 entries. Source freshness, instrument eligibility,
market value and quote checks are recomputed separately. Independent audits omit
this cache and reparse all original facts.

Rebuilds validate a consistent read snapshot and publish its full immutable edition in a short write transaction. One process lock serializes rebuild publication. Pause, cancel and source-progress writes can proceed while the snapshot is being validated; they do not alter the frozen inputs.
