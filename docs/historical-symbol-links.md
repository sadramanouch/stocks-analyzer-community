# Historical symbol continuity

`unchanged-cusip-symbol-history-v1` supports two bounded legacy records:

| Saved symbol | Provider symbol | Effective date | Issuer CIK |
| --- | --- | --- | --- |
| SCVL | SHOE | 2026-06-12 | 0000895447 |
| VSCO | VSXY | 2026-06-02 | 0001856437 |

Both original issuer notices explicitly state that the common stock's CUSIP
remains unchanged. The notices are archived alongside a matching SEC submissions
index and the original Yahoo chart response. The catalog is frozen with this
methodology version; future mappings need their own reviewed definitions.

Every read reproduces the result from the original bytes. Fingerprints bind
request parameters, URLs, source kind, exact symbol, response status and body.
The SEC CIK, current symbol/exchange, original notice accession/date and exact
continuity sentences must agree. Script and hidden content cannot supply the
sentences. Source observations must precede capture; the current index must be
no more than seven days old and the price response no more than one day old at
capture. Historical replay uses the original capture time.

The price response retains its actual provider symbol. It must pass the existing
USD, equity, exchange timezone, OHLC, adjustment-basis and ordered-date checks,
plus the exact chart exchange and original request interval. The exact saved
entry and any closing date must have bars. No nearest-date substitution is made.
Capital-gain events need separate review. Session completeness is not certified.

Vendor history can be revised after a session. For example, SHOE's October 15
opening price differed between the preflight and final capture while its closing
price was unchanged. Both original responses remain retained; replay validates
each capture against its own bytes instead of requiring later vendor responses
to reproduce an earlier observation.

The one-time captures live in immutable `market_lineage_histories`. Original
failed downloads and direct-symbol histories remain unchanged. Retrying the
capture returns the same fully replayed result without another source request.
The capture endpoint accepts only an empty object, never client-supplied prices,
symbols, dates or continuity assertions. Sources remain available through the
existing authenticated market-source export endpoint.

The Broker and benchmark panel shows the archived links beside the remaining
price gaps. Portfolio reference comparisons explicitly label the provider symbol
and link to its proof. `latest_histories` excludes linked series by default:
only the historical inspection opts in. Trading decisions, copy studies, frozen
prospective trials, financial eligibility and account balances are unaffected.

An unchanged-CUSIP notice supports a security identity link. It does not
independently prove Yahoo's historical prices or adjustment factors, the original
recorded fills, cash flows, fees or an account return. These captures are dated,
retrospective vendor references. They must not be presented as information known
when the legacy position was selected.

FDP's issuer announced the FDP-to-DMC name/symbol change, but the captured notice
does not state unchanged CUSIP; it is not included in this v1 catalog. APM's
domestication/merger, cash or stock acquisitions, preferred redemptions and
warrants require different evidence and accounting. Their underlying/successor
shares are not price substitutes.

Authenticated routes:

- `GET /api/market-history/lineage`: supported links and replayed captures.
- `POST /api/market-history/lineage/{ticker}/capture` with `{}`: bounded capture or
  idempotent replay. Recent access denials pause collection; source storage keeps
  a 256 MiB reserve plus capture headroom.
- `GET /api/market-history/lineage/{id}`: exact immutable report.
- `GET /api/market-history/documents/{id}` also resolves a linked comparison's
  document ID, with the normalized history's own checksum and separate link proof.
- `GET /api/market-history/sources/{id}`: original response bytes encoded as hex,
  with source metadata and SHA-256.
