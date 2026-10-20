# Read-only IBKR reporting connection

The direct `/broker` page and `/invest` advanced tools include **IBKR connection · reporting only**.
This completes the code path from a configured reporting account to archived
broker-issued records and the existing native cash/share review. It creates no
orders, accounts, funding events or paper-ledger trades. It does not move CIBC
investments or require a market-data subscription.

An IBKR account with reporting permissions is an external prerequisite. Create
one Activity Flex query in XML format with the fields and sections specified by
the existing native import panel. Use YYYYMMDD dates, YYYYMMDD;HHMMSS timestamps,
and the actual export timezone. Enable Flex Web Service and configure
`IBKR_FLEX_TOKEN` and `IBKR_FLEX_QUERY_ID` as secrets on the existing backend Fly
app. Do not put values in chat, source code, public build arguments or uploaded
files. The UI accepts a nickname, dates and timezone, never credentials.

Choose an opening statement day and a later completed closing day within the
preceding 365 days. The app requests the opening day and the subsequent activity
interval using explicit `fd`/`td` overrides of the configured query. Returned
native statement dates must match exactly. Timezone, single-account identity,
complete supported sections and monetary mappings are reviewed using the
unchanged `ibkr-flex-trade-date-review-v1` profile. Its legacy review flags and
limits remain unchanged; the separate transport receipt records service delivery.

`POST /api/broker-connection/downloads` starts an asynchronous bounded job.
`GET /api/broker-connection` is local status only; it sends no broker request.
`GET /api/broker-connection/receipts/{id}` verifies hashes, original XML custody
and frozen native replay before returning records. Both downloaded originals,
request dates, approved delivery endpoints and response hashes are archived in
an immutable receipt. A source can be retained with reconciliation gaps; the
interface displays those gaps rather than assuming missing activity is zero.
No downloaded bundles are automatically merged into virtual or real returns.

Outbound access is restricted to the documented SendRequest endpoint and an
exact allowlist of IBKR statement pickup endpoints. The token is used only in
the broker request; it is omitted from receipts, job records and API responses.
Redirects, response-provided arbitrary URLs, credential echoes and unsupported
XML are rejected. Transport exceptions and schema errors do not echo submitted
values, URLs with credentials or account details. TLS validation stays enabled
and environment proxy settings are not inherited.

Each job allows at most eight requests, spaced at least seven seconds apart,
within a 120-second processing deadline. Only GetStatement error 1019 is polled,
up to three pickups per report. HTTP errors, other service errors and malformed
responses stop the job. A two-minute cooldown follows the last job's completion
or failure; persistent state blocks parallel requests. Restarted jobs are marked
interrupted and are never silently resumed. Storage reserve and a combined
640 KB supported-source limit apply. Failed/partial runs save no source receipt.

Tests exercise the full protocol with explicitly synthetic native XML, exact
cash/share reconciliation, generation delays, service errors, rate/size/time
limits, redirects, hostile XML, credential handling, date/account mismatches,
receipt replay, restart behavior and unchanged paper records. Until a genuine
configured download succeeds, this is a tested connection implementation, not
a claim of live broker access or real-export validation. Even successful
transport is not an independent digital signature, proof of completeness,
NBBO/execution-quality validation or profitability.

Primary references: [SendRequest](https://www.interactivebrokers.com/docs/web-api/api-reference/send-request),
[GetStatement](https://www.interactivebrokers.com/docs/web-api/api-reference/get-statement),
[service error codes](https://www.ibkrguides.com/orgportal/performanceandstatements/flex3error.htm).
