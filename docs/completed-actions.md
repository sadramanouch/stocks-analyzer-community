# Completed corporate actions and historical symbol evidence

`issuer-completed-action-notices-v1` adds immutable, retrospective source reviews
for three legacy records that remain open in the original book:

| Symbol | Reported conversion | Reported contractual consideration |
| --- | --- | --- |
| AMWD | 2026-05-28 | 5.150 MasterBrand common shares, plus cash for fractional shares |
| TBRG | 2026-07-09 | USD 26.25 per common share in cash, without interest |
| TMHC | 2026-07-24 | USD 72.50 per common share in cash, without interest and subject to withholding |

Every review retains the original 8-K and issuer submissions index. Replay
checks the exact CIK, accession, primary document, filing/report dates, visible
completion and consideration text, registered common-share row and exchange.
The action policy explicitly recognizes two uppercase Nasdaq exchange labels;
the older registered-share-class policy's default mappings remain unchanged.

TMHC's filing has a July 20 earliest event/report date. Its visible merger text
states July 24. The latter is the conversion date; report dates are not assumed
to be completion dates. Dates remain calendar-date observations, not certified
intraday or final market-trading times.

The original portfolio banner identifies affected open records. Reconciliation
also flags a closed holding that spans the reported conversion. A same-day or
post-conversion entry requires review. Holdings ending before the conversion
are not flagged by that event. Invalid evidence yields an unknown affected count
and an integrity warning, rather than a zero-event claim.

The old price ratio remains labelled unverified, and split-only scenarios are
withheld for an affected interval. No payment, successor-share balance, fractional
proceeds, tax, broker execution, cash flow or return is inferred. Original
positions, book amounts, ledger events, financial eligibility, decision evidence
and frozen prospective trials are not rewritten. This bounded catalog does not
establish complete corporate-action coverage.

Manual `POST /api/completed-actions/{ticker}/capture` accepts only `{}` and only
the three catalog cases. It archives at most two bounded source responses,
honors recent access-denial cooldowns and the 256 MiB storage reserve plus
headroom, and stops after a denied request. Repeat capture replays the saved
report without network. Reads are authenticated:

- `GET /api/completed-actions` lists supported cases and replayed captures.
- `GET /api/completed-actions/reports/{id}` exports the exact review.
- `GET /api/market-history/sources/{id}` exports original bytes as hex with
  their source fingerprint and metadata.

## Additional symbol policy

`unchanged-cusip-symbol-history-v2` adds LSH → QNME, effective August 4, 2026.
In addition to the unchanged-CUSIP notice, current issuer index and original
Yahoo response, it requires original SEC cover rows for both former and current
registered common-share classes. The former filing must precede the recorded
holding. Both rows bind exact symbols, issuer CIK, exchange and filing contexts.
The earlier v1 catalog and reports retain their exact definitions and replay.

The QNME response retains its actual provider symbol and adjustment basis. It
must cover the exact legacy entry and exit dates; no nearest dates are substituted.
This is retrospective vendor history for inspection. Independent price accuracy,
complete actions and account performance remain unverified. Linked series remain
excluded from trading decisions and copy studies by default.

FDP's original OCC notice request returned HTTP 403 during preflight. Cached
search excerpts were not substituted for archived original evidence, and no
access-control workaround was attempted. FDP remains unresolved under these
policies. Government/crypto credentials, genuine broker exports and future
observed strategy results remain separate dependencies.
