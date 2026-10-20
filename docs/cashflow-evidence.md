# Filed cash-flow scopes

`filed-cashflow-scopes-v1` adds separately named annual cash-flow measures from already archived SEC responses. It does not change the existing PP&E-only capex definition, FCF values, research qualification, rankings, valuation models, trade policy or frozen studies. No new external provider is needed to read these reports.

The [SEC company-facts API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) contains standard-taxonomy facts for the filing entity. Issuers report different investment categories. `PaymentsToAcquireProductiveAssets` includes property, plant and equipment, software and other intangibles; it is broader than `PaymentsToAcquirePropertyPlantAndEquipment`. Both can provide useful evidence without being interchangeable. The report names each category and preserves its original concept, USD value, start/end dates, accession, filing date and form.

## Measures and operating scope

Each of three annual periods can expose:

- Total operating cash flow, continuing operating cash flow and discontinued operating cash flow as separate facts.
- PP&E, productive-asset, oil/gas property/equipment, software and intangible cash payments.
- Gross and net business-acquisition payments, kept separate from ordinary capital spending and from each other.
- Operating cash flow minus PP&E payments, minus productive-asset payments, or minus oil/gas property/equipment payments. These are distinct measures; no combined all-in capital-spending total is inferred.

Reported total operating cash flow is preferred when unambiguous. If it is missing, a total can be derived only from explicitly reported continuing **and** discontinued operating cash flows with exactly matching start, end, accession, filing date and form. Missing discontinued operations are never treated as zero. A conflicting total cannot be replaced by components. If all three aligned facts are present but fail exact reconciliation, all derived cash measures are withheld and the discrepancy is displayed. No precision tolerance is guessed without a reported precision basis.

Spending must be nonnegative to enter a derived cash measure; negative reported payments require review and are not automatically absolutized. Negative operating cash flow, negative cash remaining and explicitly reported zero values remain valid. No spending categories are summed together: software and intangible payments can already be included in productive assets. Different filing identities or durations block subtraction even when individual values are source matched.

## Original custody and saved editions

The existing `primary-financial-context-v1` verifies company-facts receipt metadata, original compressed-byte fingerprint, exact SEC ticker/CIK mapping, source observation and filing cutoffs, and the saved annual period. That implementation and its output are unchanged. This supplementary report uses the same verified originals and the same three period ends. It selects annual USD facts as known in that saved edition. Later filings or restatements are not inserted into an earlier capture.

The report includes its source-context hash and the hash of the complete saved research item. Available facts have the same ambiguity checks used by the existing financial context. Captures retain exact JSON and SHA-256 in an immutable table; exports reparse the original sources and recompute the entire report. A current-edition capture rejects changed editions, changed report inputs, arbitrary numbers and reused request IDs with different parameters. Repeated identical requests return the original capture. Old captures continue replaying after new saved editions are created. A storage reserve prevents new captures when space is low.

Authenticated routes:

- `GET /api/cashflow-evidence/{ticker}?version={edition}`: dated source report; no network fetch or source refresh.
- `POST /api/cashflow-evidence/{ticker}/capture`: accepts only the saved edition, displayed content hash and a new request ID.
- `GET /api/cashflow-evidence/reports/{id}`: original JSON/hash plus full source replay.

The original company dashboard shows **Filed cash-flow scopes** under **Number checks**. Source downloads use the existing original SEC receipt route. No forecasts, security suitability, distributable shareholder cash, account returns, complete obligations or profitable trading edge are certified. Companies lacking supported PP&E-only capex remain outside the existing trading policy even if this report supplies a broader cash-flow measure.

When changing the methodology, retain v1 replay behavior for its immutable reports. Do not edit the original financial-context method to make old captures adopt new definitions.
