# Stocks Analyzer

An experimental, self-hosted equity research workspace built with FastAPI,
SQLite and Next.js. Inspect company financial inputs and original SEC insider
filings, save research evidence, and evaluate explicitly virtual positions.

**Contributors and co-maintainers wanted.** This project is being prepared for
a community source release so others can continue it. Start with
[ROADMAP.md](ROADMAP.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## What exists

- Company screening, financial provenance and disclosed valuation scenarios.
- Original SEC Form 4 evidence, issuer history and bounded collection tools.
- An independent paper ledger, corporate-action checks and research studies.
- A synthetic demo, backend regression tests and frontend authentication tests.

These features are research tools. Saved scores, legacy portfolio returns and
actor profitability are not independently certified. There is no demonstrated
profitable trading strategy. See [the first-release scope](docs/first-release.md)
and [the historical audit](APPLICATION_AUDIT.md) for specific limitations.
The MIT code license does not grant redistribution rights to provider data.

## Local synthetic demo

Requirements: Python 3.12 and Node.js 22. No production database or paid account
is needed for the included synthetic preview.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
cd backend
python preview.py
```

In a second terminal, from the repository root:

```sh
cd frontend
npm ci
ALLOW_UNAUTHENTICATED_LOCAL=1 API_URL=http://127.0.0.1:8000 npm run dev -- --hostname 127.0.0.1 --port 4317
```

Open http://127.0.0.1:4317/trade-evidence for the synthetic records. `/invest`
shows the focused research workflow, which may be empty without real inputs.
The preview uses a temporary database, disables scheduled jobs, and omits paid
feed credentials. Do not point it at the original hosted services or import a
private production database. Explicit live lookups can still request public
market/SEC data; they are not needed to inspect the synthetic records.

Optional integrations include a local Ollama model and separately entitled
market-data feeds. AI/model downloads and provider usage have their own terms
and possible costs. Keep keys server-side. SEC access requires an honest
operator user agent and compliance with its access policies.

## Checks

```sh
cd backend
../.venv/bin/python -m unittest discover -s tests -q
cd ../frontend
node --experimental-strip-types --test tests/*.test.mjs
npx tsc --noEmit
npm run lint
npm run build
node --test tests/auth-proxy.integration.mjs
```

The Python command assumes the root `.venv` above. The production frontend
build can download Google fonts. No passing check establishes investment returns.
The older [launch notes](LAUNCH_AND_TEST.md) describe the founder's private hosted
installation; those databases, credentials and dated measurements are not
distributed with this source release.

## Contribute

Useful first work: reproduce the synthetic setup, expand financial-input
fixtures, add accessible empty states, and improve test/data-source documentation.
Larger work includes instrument identity, corporate-action reconciliation and
independent prospective research. Every claim needs dated source evidence.

See [security reporting](SECURITY.md), [community conduct](CODE_OF_CONDUCT.md)
and [the MIT license](LICENSE). Third-party dependencies retain their licenses.
