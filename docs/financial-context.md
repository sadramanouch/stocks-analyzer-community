# Primary financial context

Select a company in **Research** or **Equity decisions** to open **Annual history & balance-sheet evidence**. The view uses existing archived SEC company-facts responses. It makes no new provider requests and does not create trades. New single-company saved research decisions and paper previews include the complete context and its content SHA-256; older records remain unchanged. Portfolio mark records retain their existing format.

`GET /api/financial-context/{ticker}?version={saved_edition}` returns the same context for an explicit saved edition. Omitting the version uses the latest edition. The endpoint is protected by the existing backend authentication and website proxy. A missing ticker/edition is 404; unavailable or conflicting evidence is represented explicitly in the returned context.

The financial receipt must match the saved source metadata, original bytes, issuer CIK, official SEC URL and observation boundary. An archived SEC ticker directory available at source observation must identify exactly the requested ticker and issuer. No underlying stock, renamed ticker or adjacent date is substituted. Both source receipts and the derivation methodology are included in the content hash.

Annual facts require an annual filing form, an accession, USD units and a 335–395 day period. The current saved annual end anchors the history; previous years require one unambiguous reported annual end within 30 days of the expected anniversary. Missing years have no invented report date. A core field retains its supported current concept across the three years; unsupported concepts are not treated as equivalent. Facts filed after the archived source's observation date are excluded. Conflicting values/durations on the latest filing day remain unresolved, even if accession sorting could select one. Equal numeric duplicates are compatible.

The history is the restated information present in this archived response. It does not reconstruct what a trader could have known in an earlier year. The content hash proves the derivation's inputs and output; it does not prove an issuer's economic assertions are true.

Ratios require every input to have the same period and filing. Derived amounts are Decimal strings; display formatting does not change the stored precision. The cash-flow measure is operating cash flow less payments to acquire PP&E. It may differ from issuer-defined free cash flow and does not deduct all investments, debt service or other required outflows. A negative reported PP&E payment needs separate review and is not changed with an absolute value.

Balance-sheet facts use instant USD entries in annual filings at the exact displayed end. Cash uses `CashAndCashEquivalentsAtCarryingValue`; short-term investments are not substituted. Debt components remain separate and missing components remain unknown. No total-debt or net-debt coverage claim is made. The balance equation uses equity **including noncontrolling interests**; parent equity is not substituted. A nonzero residual is displayed for review, not silently reconciled. Negative balances in normally nonnegative concepts remain visible as requiring review and do not enter ratios.

This supporting context does not change the existing four-field purchase policy, certify live quotes or market cap, or validate every legacy ratio/forecast. Eligibility, liquidity, corporate actions, broker execution, provider coverage and investment performance require their own evidence.

Primary reference: [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces). Issuer definitions of non-GAAP free cash flow vary; the platform states its own formula rather than treating it as distributable cash or expected return.
