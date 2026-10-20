# Price evidence in the daily research workflow

The selected company on `/invest` shows the price evidence for its displayed
reference, alongside financial evidence. A passing company or fresh purchase
does not imply that its displayed price was independently verified.

`GET /api/price-readiness/{ticker}?version=N&input_sha256=HASH` is read-only and
fetches no outside data. It binds the status to the saved research edition and
original input hash; the UI also compares the returned price, timestamp, source
and currency with the selected company. A changed input produces a reload
message rather than reusing a positive status.

The latest retained quote comparison must replay from its original sources and
identity evidence. Corrupt evidence never falls back to an earlier agreement.
The existing `cross-provider-regular-quotes-v1` reports and policy remain
unchanged. Current status uses `displayed-reference-price-context-v1`.

Displayed-reference corroboration requires a current supported common-share
identity and current research edition, a positive dated USD reference, a
retained comparison that agreed within its declared tolerance, and both source
prices and the displayed reference no older than 20 minutes. The displayed
reference must be within 60 seconds and the greater of one cent or 0.1% of the
lower price of each source. Listing exchanges must match. The UI expires a
positive observation conservatively and offers a read-only status reload.

The legacy 24-hour paper-reference check is displayed separately. Passing it alone
does not establish financial, signal, cash, position or corporate-action
eligibility. No order or paper trade is created by inspecting price status.
The default paper preview now uses a separate recent source-comparison receipt;
see [compared paper references](compared-paper-reference.md). It does not
replace or certify the older displayed financial reference.

The existing Yahoo/CNBC controls are available under the price panel. Outside
scheduled regular sessions plus the 20-minute closing window, the new panel
disables its capture button and shows the next supported opening. Calendar
coverage remains explicitly limited to the existing 2026 calendar. Reloading
status or opening retained originals does not download new market data.

Separately retrieved websites may share exchange or vendor inputs. Agreement
is bounded corroboration; upstream independence, executable prices and
profitability remain unverified. Genuine independent market-data or broker
evidence is still an external requirement for stronger capital-allocation
claims. Historical cleanup is not part of this milestone.
