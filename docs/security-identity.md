# Registered share-class evidence

`sec-registered-share-class-v1` supplements an unspecified Nasdaq Trader listing
class with original SEC evidence. It does not infer a share class from a familiar
name, a quote provider's EQUITY label, an issuer's shares outstanding or a CIK.

The archived SEC ticker directory must map the exact, case-sensitive ticker to
one issuer. That issuer's original submissions response selects its latest
supported periodic filing, including detecting amendments. The primary filing
must contain one unambiguous visible registered-security table row matching the
exact ticker, an explicitly common/ordinary share title, and the listing exchange.
All three facts must share a context identifying the same CIK and report end.
Context alone is insufficient: an issuer can use one context for common shares,
preferred shares and bonds. Standard hidden issuer metadata is accepted; hidden
security rows, continuation chains, unknown exchanges and ambiguous classes fail
closed. Registered shares, capital stock and Class A shares without an explicit
common/ordinary designation require further review.

Every report retains original mapping, submissions and filing bytes, their
metadata and SHA-256 hashes, and the original immutable directory revision.
Replay verifies the directory batch and both original directory files as well as
all SEC source bytes. Appended qualified revisions refer to the report. Previous
identities, saved editions, source calculations and prospective studies remain
unchanged. A changed listing cannot reuse a prior class report. New editions use
saved-research-v5 to distinguish this supplemental classification.

Evidence expires at the earliest of seven days after mapping observation, seven
days after capture, and filing-age limits (140 days for 10-Q, 550 for annual
reports). Report-end age must also be at most 550 days. A fresh directory is
required separately. Current research eligibility and quote comparisons reject
expired supplemental evidence. Frozen report replay uses its original cutoff.

The existing weekday 16:50 New York maintenance job checks at most five original
portfolio symbols with unspecified listing classes, oldest checks first, with a
three-day repeat interval. Successful evidence is reapplied after a directory
refresh. This is bounded maintenance, not a full-market identity guarantee.

Authenticated routes:

- `POST /api/security-identity/{ticker}/collect` accepts only `{}` and collects one
  exact unresolved listing. It does not rebuild the saved list or change trades.
  A recent identical report is reused for one hour without a new capture time.
- `GET /api/security-identity/reports/{id}` reparses original sources before return.
- `GET /api/security-identity/sources/{id}` exports exact original bytes as an inert
  attachment with `X-Content-SHA256`.

Requests use the configured SEC contact header, disable redirects, use bounded
timeouts and 12 MB response limits. Collection stops on a failed source and pauses
15 minutes after 403/429/503. The collector serializes requests and requires a
256 MiB reserve plus 36 MB source headroom. No source access workaround is used.

This establishes only a registered class at the recorded cutoff. Financial
inputs, historical identity continuity, quotes, broker fills, corporate actions,
political/wallet attribution and returns require their own evidence. It cannot
establish profitability or certify a real-money allocation strategy.
