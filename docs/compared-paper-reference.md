# Compared references for forward paper decisions

On `/invest`, **Preview without recording** captures (or reuses the existing
five-minute capture of) Yahoo and CNBC regular-market observations. It then
requests a virtual preview using the latest retained comparison. A failed
comparison has no fallback to an older passing report or the cached dashboard
price. This operation saves evidence, but does not add paper-ledger trades.
**Record simulated paper entry** is a separate, explicit action.

`POST /api/equity-workspace/paper/compared-preview` accepts ticker, side, shares,
strategy and `quote_report_id`. Caller-supplied prices and timestamps are
rejected. The existing `/paper/preview` v1 endpoint and its frozen policies
remain available for reproducibility.

The new `forward-equity-paper-compared-reference-v2` policy requires replay of
the original response bytes and identity custody. The financial snapshot,
comparison and current authoritative listing must match in ticker, title,
exchange, common-share classification and any supplemental class evidence.
Both source observations must still be within 20 minutes, within 60 seconds
of each other, and agree within the existing one-cent/0.1% comparison policy.
The selected reference is Yahoo's original `regularMarketPrice`, not an average
or an executable bid/ask. The saved financial inputs, their hash and dated FCF
yield are preserved exactly; the new reference has its own frozen receipt.

Each preview freezes that receipt, policy, financial edition, purchase evidence,
corporate-action references and ledger token. Expiry is the earlier of ten
minutes or either source observation's twenty-minute deadline. Recording
replays and revalidates the latest report, current identity, financial and
purchase eligibility, actions and ledger state. Changed or expired evidence
requires another preview. A later failing comparison invalidates a pending
preview rather than selecting an older agreement.

Simulation retains the existing cash-only, 20% position cap and modeled 10 bps
fee plus 10 bps slippage per side. Six-decimal fills and cent-rounded fees use
ROUND_HALF_UP. Trades and any immediate reference mark post in one transaction;
an identical retry cannot duplicate them. Cached previews and mark refreshes
cannot overwrite a newer paper quote with an earlier reference timestamp.

The live account is not used for posting deployment test trades. Isolated tests
cover buys, sells, costs, accounting, atomic rollback, duplicate recording,
source corruption, staleness, changed identities/policies/ledger, latest-report
replacement and stale mark prevention. A hosted preview can be inspected
without altering the cash/share ledger.

Website agreement does not prove upstream independence. These are dated
regular-market references, not NBBO, guaranteed execution prices or broker
records. Actual fill quality and profitability require separate evidence and
observed results after real costs. No subscription or broker account is
created by this feature. A paid/entitled quote feed, even if connected later,
would still not prove an execution or a profitable strategy.
