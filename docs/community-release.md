# Community release checks — 2026-10-19

Prepared from `feat/trade-evidence`, not the older default branch.

Verified in an isolated checkout using Python 3.12 and Node 22:

- 511 backend unittest cases passed.
- 10 frontend node:test cases passed.
- TypeScript and repository ESLint checks passed.
- Production Next.js build passed; its configured fonts require network access.
- All 3 built-app authentication/proxy integration suites passed.

The source includes a synthetic preview. No private database or paid provider
credential is needed for that preview. No production writes or deployments were
performed in preparing this release.

The old private Git history has credential-like material, so publication must
use a fresh reviewed snapshot. Keep the private repository intact. Source
screening does not certify financial models, data licenses, historical returns
or the security of arbitrary deployments. See APPLICATION_AUDIT.md and
docs/first-release.md for the documented research limits.
