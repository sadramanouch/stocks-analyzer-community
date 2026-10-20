# Stock Analyzer → evidence-based trade intelligence

**2026-10-13 update:** the original application remains the foundation, with trade evidence embedded in rankings and company detail. Financial-period selection, missing inputs and session/API security are repaired. An independent cash/share paper ledger, read-only split reconciliation, conditional equity scenarios with sensitivities, instrument/sector exclusions and consistent rankings are implemented. Legacy automatic allocation is disabled by default. Verification: 67 backend tests, 6 frontend tests, 3 built-app integration suites, production build, full lint and a preserved-data restore drill. See `APPLICATION_AUDIT.md` and `LAUNCH_AND_TEST.md` for evidence and limits. Both existing Fly apps were deployed after explicit build-context/deployment approval. The API/session credentials were rotated, the website password preserved and original production rows verified unchanged. SEC refresh failures retain clearly dated saved filings. Complete trader histories and follower-performance validation below remain planned.

Reviewed 2026-10-09. Initial implementation: `feat/trade-evidence`.

## Product objective

Find public trades worth researching, identify actors whose signals remain useful **after we can observe and execute them**, and use financial/market evidence to reject weak opportunities. Public filings and on-chain activity can reveal behavior; they do not prove that someone possessed private information, or that their historical profit is reproducible. This system must establish an edge rather than assume one.

Initial scope is US stocks (Congress and corporate insiders) plus Ethereum DEX wallets. “Buyouts” is interpreted here as actionable buying opportunities; acquisition/merger arbitrage is a distinct strategy requiring deal terms and completion-risk analysis.

## What the existing system actually does

- `backend/main.py`: FastAPI, SQLite, SEC filings and XBRL, market data, financial/forensic models, deterministic 1–99 scores, and an Ollama narrative.
- `frontend/app/page.tsx`: company screener and detailed analyzer. Dedicated leaderboard and paper-portfolio routes already exist.
- `calc_quant_signal`: a hand-weighted fundamental score mapped to BUY/HOLD/AVOID. Some missing inputs receive neutral points. These are model rules, not empirically established probabilities of profit.
- SEC Form 4 handling supplies filing counts and links. Counts alone do not identify transaction direction, owner, size, derivative exposure or discretion. The prompt previously encouraged an inference about selling; this change removes that inference.
- `_open_portfolio_positions`: disabled by default; explicit opt-in opens fixed-dollar BUY/STRONG_BUY simulated positions using saved current prices. It is not a simulator for following an actor after disclosure. The new independent ledger separately records explicit cash, shares and manual paper events.
- `run_learning_analysis`: descriptive correlations/OLS on open and closed positions. The repaired queries apply `min_days`; they do not establish out-of-sample trader alpha.
- `_pick_annual_value`: prefers the most recently filed restatement for each period. Appropriate for present-day review, but historical testing needs an as-of filing cutoff to prevent hindsight.
- Provider credentials now stay server-side behind signed sessions and an authenticated Next proxy. The hardcoded market key was removed. Previously published browser bundles and Git history still require production credential rotation when the new configuration is released. No credentials are reproduced in this plan.

## Provider decision

| Source | Role | Recommendation and limitation |
|---|---|---|
| [Quiver API](https://www.quiverquant.com/api-setup/) | Structured congressional and Form 4 activity | First stocks integration. Existing endpoints expose trade/report dates and transaction types. API entitlement is separate from simply browsing the website; verify coverage and license for your use. |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Filing provenance, issuer facts | Retain as company-data foundation. Add original ownership XML parsing, reporting-owner CIK, accession and line IDs, amendment handling and trading-plan flags. |
| [House disclosures](https://disclosures-clerk.house.gov/FinancialDisclosure) and [House rules](https://ethics.house.gov/financial-disclosure/) | Congressional source verification | Reports may be filed substantially after the transaction. Amounts are ranges. Treat estimated portfolios as reconstructions, not complete audited accounts. |
| [Nansen DEX API](https://docs.nansen.ai/api/smart-money/dex-trades) | Wallet discovery and observed swaps | First crypto integration, Ethereum initially. Current endpoint exposes only the trailing 24 hours. Its labels are an input for discovery, not independently verified skill. |
| [Nansen wallet PnL](https://docs.nansen.ai/api/profiler/address-pnl-and-trade-performance) | Wallet-history enrichment | Candidate for phase 2; examine basis, transfers, historical coverage and completeness before accepting displayed PnL. Not integrated in this change. |
| [Unusual Whales API](https://unusualwhales.com/public-api) | Alternative Congress/market-flow feed | Potential alternative or cross-check. Avoid paying for overlapping feeds until a coverage comparison justifies it. Options flow is not automatically an identified investor's directional trade. |
| [Capitol Trades](https://www.capitoltrades.com/about-us) | Human research cross-check | Useful research UI; an API entitlement/integration has not been established here. |

No subscriptions purchased. No live API calls were made to paid feeds. Provider schemas were inspected; authenticated adapter behavior still needs a live smoke test with entitled credentials. No vendor pricing assumptions are built into the plan.

## Phase 1 — implemented in this change

**Trade evidence dashboard at `/trade-evidence`** linked from the existing dashboard:

- Provider configuration and last-sync states, explicit coverage limits, manual bounded syncs.
- Congress purchase/sale records with ranges; corporate insider P/S distinguished from grants/exercises/withholding; crypto swaps retain both asset legs and Ethereum contract identity.
- Separate trade time, reported-public time, first app observation and earliest app availability. First observation survives repeat imports.
- SQLite event ledger, raw provider observation history, exact-row deduplication and persistent research bookmarks.
- Category/search/watch filters, activity counts, source links, raw-record inspector, current fundamental scores/FCF yield/risk flags.
- Transparent **research triage**, not a profitability leaderboard: recent common-stock purchases with limited disclosure delay and supportive existing company data can receive a research flag. Unknown evidence is not converted to zero risk or fabricated performance.
- Crypto remains “needs evidence” pending token-risk/execution/history work. A swap is not reduced to a stock-like BUY.
- Date inconsistencies and unsupported identities are rejected. Provider failures retain saved data, return sanitized errors and expose a retry cooldown.

### Exact initial triage rules

A purchase may become a research candidate if public age ≤7 days, disclosure lag ≤14 days, saved analysis age ≤7 days, FCF yield >0, quant score ≥53 and high-severity flag count exactly zero. Congressional asset type must be recognized as common stock. These thresholds are editable research policy, not optimized alpha. A recent analysis timestamp does not prove that its underlying financial statements are recent; display and validate filing vintage in phase 2.

### Known boundaries of this slice

- Recent samples, not historical backfills or guaranteed complete capture. Congress processes at most 1,000 returned rows; insiders and crypto request the first 100. Pagination and gap recovery are not implemented.
- Manual refresh only; no background feed scheduling or notifications. These avoid unapproved recurring provider costs until a plan is selected.
- Date-only/timezone-less timestamps are preserved as a limitation; availability is conservative from first app observation. No historical fill prices are inferred.
- Identical rows collapse. These endpoint schemas do not provide sufficient stable filing-line IDs to prove economic deduplication across corrections and co-filers. Raw changes are retained when the identity fingerprint matches; changed identity fields may create separate events.
- Congress/corporate links go to provider dashboards, explicitly labeled; they are not fabricated original-filing links. Crypto links resolve actual transaction hashes.
- At most the latest 500 loaded rows are searchable in the UI. Watchlist filters apply to that loaded sample; backend pagination/global watch queries should precede large history imports.
- No assertion of verified trader skill, no backtested ROI, no automatic position opening and no broker execution in this module. Existing fundamental paper portfolio remains separate.

## Phase 2 — reliable histories and provenance

The accounting/model foundation is now implemented, but source-history work remains. The new ledger handles cash, unadjusted prices, splits, dividend entitlement and costs; the historical report identifies two confirmed split overlaps without claiming complete action coverage. Valuation assumptions are exposed, current shares are reconciled to market cap, and known instrument/sector exclusions apply. These controls support future history work; they do not supply missing histories or prove actor profitability.

1. Select the stocks API tier, obtain a backend key, sync a small sample and compare 20 records to original filings. Document coverage, latency and licensing.
2. Add paginated backfills and incremental watermarks. Persist raw snapshots before normalization, ingest time, provider publish/upload time, original public timestamp and timestamp precision/timezone. Never retroactively make a correction available at the original date.
3. Build canonical identities: Congress member + household owner; reporting-owner CIK + accession + table + line; blockchain + wallet + transaction + log/trade index. Keep identity confidence; do not equate a wallet label with a verified human.
4. Parse SEC ownership XML: P/S vs A/M/F/G, non-derivative vs derivative, direct/indirect ownership, 10b5-1 and amended filings. Review private transactions separately from market-copyable trades.
5. Store market bars, splits/dividends, delistings, original filings and financial facts as-of publication. Current fundamentals must never leak into old backtests.
6. Wallet-history accounting: buys/sells, deposits/withdrawals, bridge movement, airdrops and related-wallet transfers; flag unknown cost basis and unmatched inventory. Account for unsold illiquid tokens and failed exits.

**Acceptance:** replaying a historical date cannot see later disclosures, amended facts, future actor rankings or today’s surviving assets. Each normalized event links to its original evidence and revision history.

## Phase 3 — find who is actually worth following

Use two separate views: **observed actor performance** and **replicable follower performance**. Congressional disclosure ranges and incomplete holdings generally support estimates, not exact personal PnL.

- Prespecify a strategy (e.g. equity purchase signals, equal notional allocation, fixed 20/60/120-trading-day holds). Do not invent exits when a sale/holding record is absent.
- Entry is the first executable quote/bar strictly after availability + processing delay. Date-only disclosures enter no earlier than the next valid market session after that disclosure date; new forward signals start after first app observation.
- Apply transaction costs, spread, slippage, gas, liquidity/capacity and turnover. Stress results with doubled costs and realistic delays. Exclude trades that could not be filled or unwound.
- Compare against SPY and sector-matched exposure for equities, and suitable ETH/BTC/chain benchmarks for crypto. Report excess return, net return, drawdown, downside risk, sample count, history length, confidence intervals and profit concentration.
- Discover/select actors only using the training window. Freeze choices before a later test window; walk forward without reusing holdout results to pick winners. Include losers, inactive actors and delisted tokens.
- Address correlated observations, clustered disclosures, repeated trades in the same asset and selection across many wallets. Use block-bootstrap uncertainty and shrink small-sample results toward the baseline.
- Show “insufficient evidence” unless a predeclared minimum history and independent-event count is met. A starting research requirement could be 12 months and 30 independent events, but this is not statistical proof and must be evaluated for each strategy.
- A single exceptional winner, fast illiquid token flipping, airdrops or privileged allocations should not qualify someone for a broadly copyable strategy.

**Acceptance:** a frozen actor-selection rule beats its chosen benchmark on unseen periods net of stressed costs, with uncertainty and drawdown visible. If it does not, the system says no edge was established.

## Phase 4 — opportunity dashboard and paper copying

Once evidence qualifies an actor, the dashboard should connect:

**Actor track record → fresh trade → asset quality → executable price/liquidity → sized paper position → measured outcome.**

Stocks retain existing cash-flow, leverage, valuation and earnings-quality checks; qualify sector applicability and missing-data coverage. Crypto gets its own risk model: exit liquidity, holder concentration, token controls, unlocks/dilution, protocol usage and fees where meaningful, and unknown contract risks. Do not apply company accounting ratios to tokens.

Add price movement since disclosure, stale-signal rejection, thesis, expiry, benchmark, risk budget and reasons to decline. Separate observation from inference. An LLM may explain verified calculations; it should not invent dates, PnL, causal private-information claims or conviction scores.

Introduce an isolated signal-paper portfolio with signal version, actor and event IDs, decision/quote times, simulated fills, fees, rejection reasons and benchmark path. Preserve the existing fundamental paper portfolio for an honest comparison. Enforce exposure and liquidity constraints; do not count correlated actors as independent confirmation.

**Acceptance:** a prospective paper run reproduces the chosen policy from an immutable decision log. Only after that evidence exists should alerts or any broker integration be considered.

## Run and validate this slice

The existing backend startup and Docker image now include `trade_evidence.py`. It lazily creates three additional tables in the existing database. No existing position or score tables are rewritten.

Set `QUIVER_API_KEY` and/or `NANSEN_API_KEY` as backend environment secrets using the chosen provider account. The checked-in `.env.example` is documentation; the app does not automatically load dotenv files. Do not paste credentials in chat or put them in frontend variables.

```sh
cd backend
python -m unittest discover -s tests -v
python -m uvicorn main:app --port 8000
```

```sh
cd frontend
npm ci
npx tsc --noEmit
npx eslint app/trade-evidence/page.tsx
npm run dev
```

Visit `/trade-evidence`. Without provider keys the correct result is a clear unconnected state and an empty ledger. Tests use invented fixture identities, temporary databases and mocked provider responses, never production/demo trades in the user's ledger.
