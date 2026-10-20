# Contributor roadmap

The source release is a starting point. The maintainer welcomes owners for these
milestones; no completion date or investment outcome is promised.

| Priority | Task | Acceptance criterion |
| --- | --- | --- |
| First contribution | Reproduce synthetic setup on a clean machine | Document OS/runtime versions and show the demo without production data or paid keys. |
| First contribution | Improve empty/error states | Add a regression case for empty evidence, stale data or provider failure without weakening eligibility checks. |
| High | Dependency maintenance | Update compatible packages; record remaining advisories and verify unit, type, lint and integration checks. |
| High | Financial input fixtures | Add independently sourced annual-period, units, missing-input and valuation reference cases. |
| High | Canonical instrument identity | Link stable identifiers and corporate-action continuity; do not infer identity from ticker alone. |
| Research | Reconciled forward evaluation | Freeze selection policy before observations; compare cash-flow-aware net results against a matched benchmark. |

Existing historical positions, provider rights and private broker data are not
community datasets. See `docs/first-release.md` for included/deferred scope.
