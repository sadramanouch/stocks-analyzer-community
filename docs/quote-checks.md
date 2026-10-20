# Cross-provider regular-market quote observations

`cross-provider-regular-quotes-v1` supplements the original company dashboard and prospective studies. It never edits saved company inputs, numerical reports, historical prices, paper trades or frozen trial results.

## What a check establishes

A capture separately requests Yahoo's `regularMarketPrice` from its original chart response and CNBC's public quote response. Both exact response bodies are archived in the existing immutable source store, including HTTP errors. A check freezes the current authoritative listing revision and source IDs, retains its input/report hashes, and replays both parsers and the comparison on read. Raw source exports verify their SHA-256 before returning bytes. Viewing a company only reads retained checks; it does not send another source request.

A comparison requires all of the following:

- Exact symbol and a current common-share identity, at most seven days old; source directory date within its existing bounded freshness window. No warrant, ETF, symbol alias, underlying share or historical issuer continuity is inferred.
- Yahoo EQUITY/New York metadata and CNBC US/Common Stock metadata, both explicitly USD. Listing exchanges must agree with the authoritative directory. Exchange codes follow [Nasdaq Trader's directory definitions](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs).
- A Yahoo regular-market price and CNBC last with exactly matching regular-market timestamp. CNBC's offset-aware timestamp must agree with its epoch milliseconds. No extended-hours substitution.
- Supported scheduled NYSE 2026 session, including November 27 and December 24 early closes. Quote instants must fall between 09:30 and that day's scheduled close, be no more than 20 minutes old at capture, and not lie in the future at capture. A five-second observation-clock allowance applies to the source receipt timestamp. Outside supported calendar years, no calendar inference is made.
- Source timestamps no more than 60 seconds apart. Decimal price difference within the greater of USD 0.01 and 0.1% of the lower price.

Outcomes are `within_declared_tolerance`, `price_disagreement`, or `not_comparable`. Missing/invalid/old values remain unavailable. A time-window tolerance does not imply the two prices represent the same trade. `upstream_independence_verified`, `executable_price_verified` and `account_return_verified` remain false even on agreement. Yahoo's upstream feed is unspecified; CNBC reports a Nasdaq last-sale/CTA feed in the observed GME response. Distinct websites do not prove independent upstream data.

The public web endpoints are not contracted data services. No availability guarantee is claimed. HTTP 403/429/503 stops further source requests in that capture; no proxy, alternate endpoint or identity bypass is attempted. Responses are capped at two megabytes each, requests time out after 20 seconds, and a 256 MiB storage reserve plus response headroom applies. An unsuccessful capture retains explicit failure states. Requests within five minutes reuse and replay the existing capture without refreshing its timestamp.

## Endpoints and collection

All endpoints use the application's existing backend authentication and the web proxy's session/origin protections.

- `GET /api/quote-checks/{ticker}`: read/replay latest retained capture or an explicit empty state.
- `POST /api/quote-checks/{ticker}/capture` with `{}`: bounded source capture; client-supplied prices are rejected.
- `GET /api/quote-checks/reports/{id}`: replayed report, exact report JSON, input hash and report hash.
- `GET /api/quote-checks/sources/{id}`: original response bytes with `X-Content-SHA256`.

While the existing prospective-trial collection setting is enabled, the scheduler checks up to five distinct registered, unexpired study symbols on New York weekdays at 10:25–15:25, 13:10 and 16:10. Actual scheduled-session validation still applies on holidays and early closes. Pausing study collection pauses these supplemental requests. The collector does not enroll new trials, alter their fixed price policy, extend their exit dates, fill missed observations, or create orders. Frozen GME trial observations remain a separate measurement.

Source URLs:

- [Yahoo GME quote](https://finance.yahoo.com/quote/GME/), chart metadata from `https://query2.finance.yahoo.com/v8/finance/chart/{ticker}`.
- [CNBC GME quote](https://www.cnbc.com/quotes/GME), response from `https://quote.cnbc.com/quote-html-webservice/quote.htm`.
- [NYSE scheduled hours and calendars](https://www.nyse.com/trade/hours-calendars).

Keep the v1 parser and policy available for existing frozen reports when introducing another method version. Passing a quote comparison does not promote a separately retained, older dashboard quote to a verified current price.
