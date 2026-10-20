# Preview and release status

Updated October 13, 2026 (Vancouver). Feature branch `feat/trade-evidence`, draft PR #1. **Deployed to both existing Fly apps on October 13, 2026.** Read `APPLICATION_AUDIT.md` for findings, verification and unresolved investment-model issues.

- Original hosted application: https://stockanalyzer-web.fly.dev
- Integrated local preview: http://127.0.0.1:4317/
- Portfolio integrity view: http://127.0.0.1:4317/portfolio → **Reconciliation & ledger**
- Public trades: dashboard toggle, company detail tab, and `/trade-evidence`.

The preview starts from a consistent October 10 production backup: 3,714 company analyses, 568 paper positions and 578,974 filing rows. The snapshot is copied into a disposable temporary directory. Scheduled jobs and provider secrets are disabled; individual company lookups can fetch current public market data. Saved rankings and portfolio marks can therefore have different vintages from a newly opened company detail. Stopping the API discards local changes.

## Start the preview

Python 3.11, Node 22. Install `backend/requirements-dev.txt` into an isolated Python environment. The frontend build downloads its configured Google fonts.

```sh
cd backend
python snapshot_preview.py /absolute/path/to/consistent-backup.db
```

In another terminal:

```sh
cd frontend
npm ci
npm run build
mkdir -p .next/standalone/.next/static .next/standalone/public
cp -R .next/static/. .next/standalone/.next/static/
cp -R public/. .next/standalone/public/
ALLOW_UNAUTHENTICATED_LOCAL=1 API_URL=http://127.0.0.1:8000 HOSTNAME=127.0.0.1 PORT=4317 node .next/standalone/server.js
```

Bind this preview to localhost only. Hosted deployments ignore the local bypass and fail closed without authentication. To test real login locally, omit the bypass and set a temporary `APP_PASSWORD`; do not reuse or publish production credentials. Build before starting/restarting the standalone server.

## Repeat the checks

```sh
cd backend
python -m unittest discover -s tests -q
```

```sh
cd frontend
node --experimental-strip-types --test tests/*.test.mjs
npx tsc --noEmit
npm run build
node --test tests/auth-proxy.integration.mjs
```

The integration test starts and closes isolated localhost servers with dummy credentials. It exercises real built Next routes against a mock API; it does not connect to production or paid feeds. Current result: **94 Python tests, 6 frontend unit tests and 3 built-app integration suites passed**. Run `npm run lint` for the full repository: zero errors/warnings. The production build uses the supported Webpack builder. Frontend runtime audit is clean; five high development dependency findings remain in the unpatched braces/ESLint chain.

The restore/migration drill preserved every original analysis, position and filing row, passed SQLite integrity validation, and created an empty independent ledger. Both Fly configuration files validate. With specific user approval, Fly remote builders produced both images and the existing production apps received them. A fresh consistent backup was downloaded and verified before rollout; every original analysis, position and filing row matched its pre-release hash afterward.

## Inspect the changes

1. Open the original screener; search, sort and paginate its saved analyses. Known instrument exclusions are hidden by default; enable the research-only inclusion checkbox to inspect them.
2. Open a current company detail and expand **Valuation assumptions**. It displays nine sensitivity cells when required inputs reconcile, otherwise an explicit unavailable reason. Saved batch scores can still reflect older models.
3. Open Portfolio → **Reconciliation & ledger**. Inspect the SEC evidence for SVRN/HCAI. The report states zero fully reconciled historical positions; conditional split scenarios do not establish actual returns.
4. The independent ledger starts unfunded. A manual paper deposit is required before buying; events require timezone-aware times, evidence references and unadjusted prices. Cash remains after sales. These entries never send broker orders. Test only on a disposable copy and restart the preview API to discard fixtures.
5. Open public-trade evidence. The free SEC source reports actual archived filings and transaction lines independently of Quiver/Nansen. Search an actor, open Details and inspect the original source and availability dates. Congress and crypto feeds remain disconnected without entitled provider keys.
6. Open Research to inspect the immutable saved-list edition, financial provenance and current freshness. Documented inputs cover required annual fields and quote checks; other ratios, forecasts and returns remain outside that verification.
7. In Original SEC trade history, enter a ticker and refresh the current index plus the next ten original documents. Missing local issuers resolve through SEC’s ticker map and must match current submissions before registration. Original references remain intact; older submissions files and complete actor portfolios require separate backfills. Inspect actor coverage to see the exact missing profitability evidence.

## Production configuration and release sequence

Frontend runtime variables: `APP_PASSWORD`, `API_SECRET`, `API_URL`, `APP_ORIGIN`; optional independent `SESSION_SECRET`. Backend variables: `API_SECRET` (same key), `POLYGON_API_KEY`, `QUIVER_API_KEY`, `NANSEN_API_KEY`, `EDGAR_USER_AGENT` with a real operator contact, and existing Ollama/CORS configuration. Keep `ENABLE_LEGACY_PAPER_ALLOCATION=0` (default); explicit opt-in resumes old automatic allocation/closing, which is not a validated copying strategy. `.env.example` files are documentation; these files are not automatically loaded by the backend. Set secrets through deployment configuration, not Git, chat, build arguments or `NEXT_PUBLIC_` variables.

1. Resolve financial-ledger/model/instrument-identity blockers described in the audit before making performance claims.
2. After private-context upload approval, build the images and stage with a separate database. Repeat the already-passing local backup/restore drill against staging, and validate scheduler policy, provider entitlements and original app views.
3. Remove exposed frontend/market credentials from active use and rotate them. The old Git history and already published browser bundles cannot be made secret by deleting current source lines. Coordinate backend/frontend key rollout to avoid a mismatched-key outage.
4. Deploy backend first and frontend second with the new runtime variables; new sessions require sign-in because old password cookies are invalid. Use a brief maintenance window if necessary for credential rotation. Keep previous image references and a compatible rollback configuration.
5. Verify login, unauthorized requests, same-origin writes, streaming/error recovery, original rankings/detail/filings, portfolio, evidence sync/watch persistence, source freshness and restored database. Remote Docker builds and production rollout are complete. A separate staging deployment was not created. The shared API key and session signing key were rotated, and the browser-exposed frontend secret retired. The existing website password remains valid; new signed sessions require signing in again.

Fly apps are `stockanalyzer-api` and `stockanalyzer-web`. No Cloudflare configuration or automated GitHub deployment workflow was found. Pushing this draft PR does not deploy it.

## Hosted release verification

Open https://stockanalyzer-web.fly.dev and sign in with the existing password. A reload serves the deployed release; old password cookies are rejected. All original data is retained, including the 138 open/430 closed position state captured immediately before deployment. Portfolio → Reconciliation & ledger starts empty and never sends broker orders.

SEC refresh failures now show saved filing metadata with its original saved date and an explicit warning that newer filings may be missing. Missing Quiver/Nansen/market-provider credentials remain visible configuration gaps; deploying code does not connect those accounts.

## SEC source access and financial provenance — October 13, 2026

The backend operator contact is configured from the repository’s existing Git contact, and live SEC submissions, company facts and original Form 4 XML requests now succeed. The original HTTP 403 import attempt remains in the history as a dated failure; it does not describe current access. SEC source snapshots, original XML, transaction identities, exact reported decimals and first-observation times are preserved. Amendments, derivatives, plans and unresolved footnotes require review rather than earning a copy-research flag.

The annual financial schema is version 5. Dollar provenance uses USD and share quantities use shares; previous caches require normalization again. Saved-list edition 6 audits all 3,714 analyses: 17 pass the required financial-input checks, 324 are excluded and 3,373 need further evidence. These dated results are not verified actor profitability, a complete investment model or certified portfolio returns. Quiver/Nansen access and complete, reconciled histories with executable post-observation prices, fees, slippage, dividends and splits remain prerequisites for honest follower research.

## Historical source backfill and complete saved-record navigation

The SEC history panel can follow up to two historical index files and import the next 50 XML documents per run. Repeated runs resume from archived index descriptors and already imported accessions. Source JSON bytes and SHA-256 hashes are immutable. The UI distinguishes archived index files, remaining XML documents and legacy non-XML records needing manual review; none establishes a complete actor portfolio.

Trade searches run against all saved events, with pages of 100 records. Actor coverage has server search and pages of 20 owners, with explicit matching totals. Reload refreshes source counts, and locally initiated imports poll until finished before refreshing the feed. Import history displays start dates, so old access failures remain dated evidence.

An original HRMY option award (accession 0001415889-25-013399) reports May 25, 2025 after its May 16 filing. Those values are preserved as an unconfirmed award needing review, excluded from disclosure-delay averages and never treated as an executed purchase. Future-dated purchase/sale validation remains strict.
