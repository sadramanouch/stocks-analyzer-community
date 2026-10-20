# Research workspace usability release

The default experience puts company data, filters and actions before audit tools.
Original financial fields and research records remain available. Verification
limits stay visible as a short note; detailed explanations open on request.

- Companies opens in a compact table, with all original columns available through
  All columns. Model estimates are named as estimates. Company selection is
  keyboard accessible, opens at the overview, and restores list scroll on return.
- Company descriptions expand on request; a compact annual-period label retains
  the age at the top. A company has an explicit Sources & checks tab. The number-check bundle and
  public-trade bundle load on demand. Original filings and their analysis start
  when a filing tab is requested. Financial calculations and source gates do not
  change.
- Financials starts with sourced annual inputs, retains filters for all saved
  companies, and opens company details in an accessible drawer. Saved editions,
  refresh actions, source campaigns and historical evidence remain expandable.
- Insider signals separates Opportunities, Paper account and Study results.
  Detailed source tools are expandable; current qualification blockers remain
  visible. Paper policies, costs, preview and confirmation endpoints are unchanged.
- Public trades puts disclosures and filters before provider setup and source
  methodology. Selecting a trade opens a drawer rather than requiring a long scroll.
- Portfolio starts with holdings. Positions and summary are the two initial
  requests; charts and diagnostics load when requested. Reconciliation and legacy
  recorded figures remain available in Records & checks.
- Shared navigation and route skeletons provide consistent orientation. Technical
  disclosures mount after first expansion and preserve their state on collapse.
  Drawers support Escape, focus restoration and scroll locking. Leaderboard and
  Guide content are unchanged.

References informed the interaction patterns; no competitor code or branding is
copied. Koyfin's [G2 page](https://www.g2.com/products/koyfin/reviews) showed 4.8/5
from 134 reviews when checked on October 16, 2026; this is a review-platform sample,
not a universal satisfaction ranking. Its [watchlist documentation](https://www.koyfin.com/features/watchlists/)
supports a customizable data-first workflow. TradingView documents a
[minimal watchlist](https://www.tradingview.com/blog/en/minimalistic-display-mode-for-watchlist-41721/)
and [on-demand symbol details](https://www.tradingview.com/support/solutions/43000745825-mastering-the-tradingview-watchlists/).
Stock Rover documents [linked table, chart and insight panels](https://www.stockrover.com/help/introduction/navigating-stock-rover/).
These are design references, not evidence that our data or strategy is profitable.

QA uses a local GET-only proxy. Its upstream authentication stays in process
memory; it blocks browser POST/PUT/PATCH/DELETE requests. Existing upstream GET
routes may refresh market caches, so this is not a claim that all read endpoints
are free of server-side cache updates. Production paper trades are not created.

The company overview also supports direct `/?ticker=...` links. Two SQLite JSON
lookup indexes match the existing purchase and amendment predicates. An isolated
in-memory snapshot returned identical decision evidence for all 319 companies:
the indexed lookup phase took 0.033 seconds versus 2.259 seconds before indexing.
This measures that query phase, not full page loading. The migration test checks
unchanged source and ledger rows and indexed query plans.
