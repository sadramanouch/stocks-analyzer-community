# First release: SEC insider research

## Product finish line

The first release is a personal research and paper-testing workflow. It is
usable when a user can see whether a company has passing financial evidence
and a fresh original SEC purchase, inspect the evidence, save a dated decision,
preview an explicitly virtual position, and follow prospectively collected
results against the same-cost benchmark. No signal is a valid outcome.

`/invest` is the entry point. It defaults to companies that pass both existing
gates, rather than the entire financial research list. A passing financial
record alone does not qualify as a public-purchase signal. Paper previews
also enforce their existing quote, cash, position and corporate-action checks.

## Included now

- Original SEC Form 4 corporate-insider purchases, their observation times,
  rejection reasons and bounded watched-issuer collection.
- Existing financial and instrument gates, with source links and dated inputs.
- Saved decision evidence and a separate cash/share paper ledger with modeled
  fees and slippage. The default preview policy requires a fresh purchase.
- Preregistered prospective filing studies, scheduled observations, matched SPY
  comparisons and explicit pending/blocked states.
- Separate research views for passing financial inputs, watched issuers and
  all candidates. These views do not relax the server's eligibility checks.

## Deferred from this release

These are separate expansion projects, not reasons to keep the research release
unfinished: certifying all 568 legacy companies; repairing every old position;
closing all delisted/warrant history gaps; reconstructing the old portfolio's
returns; politician and crypto feeds; ranking purportedly profitable traders;
and live brokerage execution. Existing evidence and tools remain available.
Advanced source campaigns, measurement tools and broker adapters are collapsed
on `/invest` and load when opened. Background collectors remain unchanged.

## Limits that development cannot manufacture

- Missing broker statements, funding/fill/settlement records and entitled API
  credentials require genuine external inputs.
- Independent price accuracy requires independently sourced, comparable prices.
  Hashes and two websites that may share upstream data cannot establish it.
- A prospective study needs future observations. A five-session study is one
  reference result, not evidence of a repeatable profitable strategy.
- No completion date, number of tests or amount of coding guarantees an edge.

## Finite next milestones and time estimates

1. Publish and verify the focused research workflow in the current build.
   Acceptance: filters preserve source eligibility, empty states are explicit,
   company evidence opens, virtual preview still enforces server checks,
   advanced tools remain available, and desktop/mobile checks pass.
2. Evaluate the existing prospective collection after its actual scheduled
   window. GME's first possible five-close result is after October 22, 2026's
   close, subject to timely successful collection. Do not backfill late data
   into a prospective result or treat one observation as strategy validation.
3. If proceeding toward capital allocation, choose and obtain the actual broker
   and independent market-data access, then validate genuine records and prices
   end to end. Allow roughly 1–3 focused implementation/validation sessions once
   compatible access and records are available; vendor/account setup can add
   time. This is a planning estimate, not a delivery or profitability promise.
4. Decide whether to continue the strategy after a predefined observation
   period and review of costs, coverage and outcomes. That is an empirical
   decision over weeks/months, with no guaranteed positive conclusion.

Do not reopen historical cleanup as a first-release dependency. New safety or
correctness failures in the active workflow remain release blockers.
