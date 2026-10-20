# Operational evidence

`/system` is an authenticated, manually refreshed snapshot of the running
APScheduler instance, its nine expected jobs, actual triggers and next invocation
times. `/api/system-status` reads the existing database in a read-only transaction
and makes no broker, quote, SEC or provider requests. It creates no ledger events,
changes no collector settings and exposes only credential-presence flags.

Each job shows the latest related stored record, independently of scheduler state.
A failed record remains failed even when its schedule is registered. A paused
collector's wrapper may still have a next invocation. The two quote jobs share
an archive: the last archived quote cannot prove either particular attempt ran.
Legacy snapshots retain only a snapshot day. Completed campaigns may be idle.
These limits are shown rather than converting absent receipts into successful runs.

The in-process scheduler is retained on application state after startup and
stopped on application shutdown. Its running state and registrations are runtime
evidence, not proof that the latest provider data is correct or every prior
invocation succeeded. Keep the backend running continuously as configured in
`backend/fly.toml`; this does not create a Codex heartbeat automation.

Acceptance checks cover the actual migrated schema and authentication, unchanged
database bytes, failed/missing/paused work, missing database fail-closed behavior,
actual scheduler trigger metadata and credential non-disclosure. Production
verification must also check deployed source hashes, the original frozen evidence,
real collector records, the browser page and separate paper-account balances.

Broker records still require the user's IBKR reporting configuration and a genuine
validated download. Politician/crypto access, upstream quote independence,
execution quality and profitable strategy results are separate dependencies.
