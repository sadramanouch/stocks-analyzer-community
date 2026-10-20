"""Documented security identities and immutable, reproducible saved-list editions.

Legacy analyses remain intact. A refreshed quote is never evidence that old
fundamentals, a security classification, or a trade history have been verified.
"""
import csv
import hashlib
import io
import json
import re
import sqlite3
import threading
import shutil
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from financial_integrity import SCHEMA_VERSION, finite, free_cash_flow
from research_integrity import instrument_eligibility

VERSION = f"saved-research-v5/financial-v{SCHEMA_VERSION}/original-sec-receipts-v1/registered-share-class-v1"
IDENTITY_VERSION = 'nasdaq-directory-v1'
_REBUILD_LOCK = threading.Lock()
DIRECTORIES = {
    "nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def instant(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def age(value, at):
    dt = instant(value)
    return (instant(at) - dt).total_seconds() / 86400 if dt else None


def currently_fresh(item, at):
    from security_identity import class_evidence_fresh
    return item.get('status') == 'documented_inputs' and all(
        elapsed is not None and 0 <= elapsed <= maximum for elapsed, maximum in (
            (age(item.get('input_observed_at'), at), 2), (age(item.get('quote_as_of'), at), 4),
            (age((item.get('identity') or {}).get('retrieved_at'), at), 7),
            (age((item.get('identity') or {}).get('source_date'), at), 8),
            (age(item.get('financial_period_end'), at), 550))) and class_evidence_fresh(item.get('identity'), at)


def classify(row):
    title = row["Security Name"]
    if row.get("Test Issue") != "N":
        return "test"
    if row.get("ETF") == "Y" or row.get("NextShares") == "Y":
        return "fund"
    # Test non-common terms FIRST: ADS titles can also mention common shares.
    patterns = [("preferred", r"\bpreferred|preference|depositary shares.*interest\b"),
                ("debt", r"\bnotes?\b|\bdebentures?\b|\bbonds?\b"),
                ("warrant", r"\bwarrants?\b"), ("right", r"\brights?\b"),
                ("unit", r"\bunits?\b"),
                ("depositary", r"\bADS\b|deposit[ao]ry|deposit[ao]r[yi] shares"),
                ("fund", r"\bETF\b|\bfund\b|\bETN\b|beneficial interest")]
    for kind, pattern in patterns:
        if re.search(pattern, title, re.I):
            return kind
    return "CS" if re.search(r"\bcommon (?:stock|shares?)\b|\bordinary shares?\b", title, re.I) else "unknown"


def parse_directory(text, source):
    lines = text.strip().splitlines()
    if not lines or not lines[-1].startswith("File Creation Time:"):
        raise ValueError("Directory footer is missing")
    stamp = re.search(r'File Creation Time:\s*(\d{8})', lines[-1])
    if not stamp:
        raise ValueError('Directory generation date is missing')
    source_date = datetime.strptime(stamp[1], '%m%d%Y').date().isoformat()
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|"))
    if not rows or not {"Security Name", "Test Issue", "ETF"}.issubset(rows[0]):
        raise ValueError("Directory schema changed")
    result = {}
    for row in rows:
        symbols = [row.get("Symbol")] if source == "nasdaqlisted" else [row.get("ACT Symbol"), row.get("CQS Symbol"), row.get("NASDAQ Symbol")]
        for symbol in set(symbols):
            if not symbol or not re.fullmatch(r"[A-Za-z0-9.$^/-]{1,20}", symbol):
                continue
            # CQS preferred suffixes are case-sensitive: AT&T TpC must never
            # become Tutor Perini TPC. Keep exact uppercase broker aliases only.
            if symbol != symbol.upper():
                continue
            record = {"ticker": symbol.upper(), "title": row["Security Name"], "security_type": classify(row),
                      "exchange": "NASDAQ" if source == "nasdaqlisted" else row.get("Exchange"),
                      "source_url": DIRECTORIES[source], "source": "Nasdaq Trader official symbol directory",
                      "source_file_time": lines[-1], "raw_record": row}
            record['source_date'] = source_date
            if symbol.upper() in result and result[symbol.upper()] != record:
                raise ValueError("Ambiguous symbol mapping")
            result[symbol.upper()] = record
    return result


def immutable(db, table):
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Immutable research evidence'); END")


def init_research(path):
    from financial_sources import init_financial_sources
    init_financial_sources(path)
    from security_identity import init_identity_evidence
    init_identity_evidence(path)
    with sqlite3.connect(path, timeout=30) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS identity_batches (
          id TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, documents_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS instrument_revisions (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, batch_id TEXT NOT NULL,
          retrieved_at TEXT NOT NULL, evidence_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS identity_ticker ON instrument_revisions(ticker,id DESC);
        CREATE TABLE IF NOT EXISTS legacy_analysis_revisions (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, observed_at TEXT NOT NULL,
          operation TEXT NOT NULL, row_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS saved_list_versions (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, model_version TEXT NOT NULL,
          basis TEXT NOT NULL, item_count INTEGER NOT NULL, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS saved_list_items (
          version_id INTEGER NOT NULL, ticker TEXT NOT NULL, status TEXT NOT NULL,
          payload_json TEXT NOT NULL, PRIMARY KEY(version_id,ticker));
        CREATE TABLE IF NOT EXISTS reviewed_statement_evidence (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, recorded_at TEXT NOT NULL,
          record_sha256 TEXT NOT NULL UNIQUE, evidence_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_refresh_jobs (
          id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          state TEXT NOT NULL, tickers_json TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0, current_ticker TEXT, errors_json TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS research_refresh_attempts (
          ticker TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, state TEXT NOT NULL);
        """)
        for table in ("identity_batches", "instrument_revisions", "legacy_analysis_revisions", "saved_list_versions", "saved_list_items", "reviewed_statement_evidence"):
            immutable(db, table)
        for record in json.loads(Path(__file__).with_name('reviewed_statements.json').read_text()):
            text = encoded(record)
            db.execute('INSERT OR IGNORE INTO reviewed_statement_evidence(ticker,recorded_at,record_sha256,evidence_json) VALUES(?,?,?,?)',
                       (record['ticker'], now(), digest(text), text))
        columns = [r[1] for r in db.execute("PRAGMA table_info(company_analyses)")]
        if columns:
            # Captures EVERY existing save/update path without treating it as a
            # versioned rebuild. Delete/replace cannot erase prior evidence.
            def row_json(prefix):
                return "json_object(" + ",".join("'" + col + "'," + prefix + ".\"" + col + "\"" for col in columns) + ")"
            if not db.execute("SELECT 1 FROM legacy_analysis_revisions LIMIT 1").fetchone():
                sql = row_json("company_analyses")
                db.execute(f"INSERT INTO legacy_analysis_revisions(ticker,observed_at,operation,row_json) SELECT ticker,?, 'legacy-baseline', {sql} FROM company_analyses", (now(),))
            for operation, prefix in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS archive_analysis_{operation} AFTER {operation} ON company_analyses BEGIN INSERT INTO legacy_analysis_revisions(ticker,observed_at,operation,row_json) VALUES({prefix}.ticker,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'{operation.lower()}',{row_json(prefix)}); END")


def sync_identities(path, fetch=requests.get):
    # Fetch both before committing. Failure retains the previous dated edition.
    records, documents = {}, {}
    for key, url in DIRECTORIES.items():
        response = fetch(url, timeout=(5, 30))
        response.raise_for_status()
        text = response.text
        parsed = parse_directory(text, key)
        if len(parsed) < 1000:
            raise ValueError("Directory appears truncated")
        elapsed = age(next(iter(parsed.values()))['source_date'], now())
        if elapsed is None or not -1 <= elapsed <= 8:
            raise ValueError('Directory generation date is stale or invalid')
        documents[key] = {"url": url, "sha256": digest(text), "text": text}
        for ticker, record in parsed.items():
            if ticker in records and records[ticker]["title"] != record["title"]:
                records[ticker] = {**record, "security_type": "unknown", "conflict": "Conflicting directory identities"}
            else:
                records[ticker] = record
    document_set = {'algorithm_version': IDENTITY_VERSION, 'documents': documents}
    batch_id, at = digest(encoded(document_set)), now()
    with sqlite3.connect(path, timeout=30) as db:
        if not db.execute("SELECT 1 FROM identity_batches WHERE id=?", (batch_id,)).fetchone():
            db.execute("INSERT INTO identity_batches VALUES(?,?,?)", (batch_id, at, encoded(document_set)))
            db.executemany("INSERT INTO instrument_revisions(ticker,batch_id,retrieved_at,evidence_json) VALUES(?,?,?,?)",
                           [(ticker, batch_id, at, encoded(record)) for ticker, record in records.items()])
        from security_identity import apply_verified
        supplemented = apply_verified(db, at)
    return {"batch_id": batch_id, "symbols": len(records), "retrieved_at": at, 'supplemented_classes': supplemented}


def identities(db):
    return {r[0]: {**json.loads(r[2]), "revision_id": r[3], "retrieved_at": r[1]} for r in db.execute(
        "SELECT ticker,retrieved_at,evidence_json,id FROM instrument_revisions WHERE id IN (SELECT MAX(id) FROM instrument_revisions GROUP BY ticker)")}


def identity_eligibility(record, company, at):
    if not record:
        return {"status": "unverified", "model_applicable": False, "reasons": ["No exact authoritative listing match; delisted or renamed symbols require historical identification."]}
    elapsed = age(record["retrieved_at"], at)
    source_age = age(record.get('source_date'), at)
    if elapsed is None or not 0 <= elapsed <= 7 or source_age is None or not -1 <= source_age <= 8:
        return {"status": "unverified", "model_applicable": False, "reasons": ["Authoritative identity snapshot is over seven days old or has an invalid timestamp."]}
    from security_identity import class_evidence_fresh
    if not class_evidence_fresh(record, at):
        return {"status": "unverified", "model_applicable": False, "reasons": ["Supplemental SEC share-class evidence has expired; a new original-source check is required."]}
    result = instrument_eligibility(record["title"], security_type=record["security_type"],
                                   sector=company.get("sector"), industry=company.get("industry"))
    if record["security_type"] == "unknown":
        result = {"status": "unverified", "model_applicable": False, "reasons": ["Official listing title does not explicitly identify common shares."]}
    return {**result, "source_url": record["source_url"], "basis": record["source"], "revision_id": record["revision_id"]}


CORE = ("revenue", "net_income", "operating_cf", "capex")


def rebuild_item(saved, cached, identity, at, reviewed=None, primary_comparison=None):
    data, fetched_at = cached if cached else ({}, None)
    observed = instant(data.get('market_data_fetched_at') or fetched_at)
    fetched_at = observed.isoformat() if observed else None
    eligibility = identity_eligibility(identity, data or saved, at)
    issues = []
    financials = {k: finite(data.get(k)) for k in CORE}
    normalized = data.get("_fw_version") == SCHEMA_VERSION
    if not normalized:
        financials = {k: None for k in CORE}
        issues.append("Legacy or missing cache: financial inputs must be fetched and normalized again.")
    fresh_age = age(fetched_at, at)
    if fresh_age is None or not 0 <= fresh_age <= 2:
        issues.append("Input observation is missing, invalid, or older than two days.")
    period = data.get("financial_period_end") if normalized else None
    provider_period = period
    reviewed_match = False
    if reviewed and normalized:
        p1, p2 = instant(period), instant(reviewed['period_end'])
        tolerance = reviewed['reported_precision_usd'] / 2
        reviewed_match = bool(p1 and p2 and abs((p1-p2).days) <= 7 and all(
            financials[field] is not None and abs((abs(financials[field]) if field == 'capex' else financials[field]) - reviewed['fields'][field]) <= tolerance
            for field in CORE))
        if reviewed_match:
            # Retain the provider label separately. This correction applies to
            # the four independently checked fields, not every old ratio/model.
            period = reviewed['period_end']
            financials = {**reviewed['fields'], 'capex': -abs(reviewed['fields']['capex'])}
    period_age = age(period, at)
    if period_age is None or not 0 <= period_age <= 550:
        issues.append("Comparable annual financial period is missing or over 550 days old.")
    if any(v is None for v in financials.values()):
        issues.append("Required annual revenue, net income, operating cash flow or capex is missing.")
    provenance = data.get("financial_provenance") or {} if normalized else {}
    if not isinstance(provenance, dict):
        provenance = {}
    documented = True
    for field in CORE:
        p = provenance.get(field) or {}
        if not isinstance(p, dict):
            p = {}
        filed_age = age(p.get("filed"), at)
        if not (p.get("end") == period and p.get("form") in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
                and re.fullmatch(r"\d{10}-\d{2}-\d{6}", p.get("accn", ""))
                and p.get('unit') == 'USD' and finite(p.get('value')) is not None
                and (abs(finite(p['value'])) == abs(financials[field]) if field == 'capex' and financials[field] is not None else finite(p['value']) == financials[field])
                and filed_age is not None and filed_age >= 0):
            documented = False
    if reviewed_match:
        documented = True
        provenance = {field: {'value': financials[field], 'unit': 'USD', 'end': period,
                             'source_url': reviewed['source_url'], 'method': reviewed['method'],
                             'statement_basis': reviewed['statement_basis'], 'reviewed_on': reviewed['reviewed_on'],
                             'reported_precision_usd': reviewed['reported_precision_usd'],
                             'reference_record_sha256': digest(encoded(reviewed))} for field in CORE}
    elif reviewed and normalized:
        issues.append('Provider fields or period differ from the reviewed primary statement. A new source comparison is required.')
    if not reviewed_match and primary_comparison is not None and primary_comparison['status'] != 'matched_original_annual_facts':
        documented = False
        issues.extend(primary_comparison['issues'])
    if not documented:
        issues.append("Required financial fields lack matching annual SEC values or an independently reviewed primary statement. Provider-only values remain unverified.")
    quote_age = age(data.get("quote_as_of"), at)
    if quote_age is None or not 0 <= quote_age <= 4:
        issues.append("Quote execution date is unknown, invalid, or over four days old; retrieval time is not quote time.")
    financial_currency = 'USD' if documented else data.get('financial_currency') if normalized else None
    currency_match = data.get('quote_currency') == 'USD' and financial_currency == 'USD'
    if not currency_match:
        issues.append('Financial and quote/market-cap currencies are not both confirmed USD; cash-flow yield requires matching currencies or a documented conversion.')
    cap, price, shares = [finite(data.get(k)) if normalized else None for k in ("market_cap", "price", "shares_outstanding")]
    if not all(v is not None and v > 0 for v in (cap, price, shares)) or abs(shares * price / cap - 1) > .25:
        issues.append("Current shares × price do not reconcile with equity market cap within 25%.")
    if cap is None or cap < 50_000_000:
        issues.append("Equity market cap is missing or below the $50M research floor.")
    if (data or saved).get("sector") in (None, "", "N/A") or (data or saved).get("industry") in (None, "", "N/A"):
        issues.append("Sector or industry is unknown; model suitability cannot be assessed.")
    fcf = free_cash_flow(financials["operating_cf"], financials["capex"])
    if fcf is not None and (finite(data.get('fcf')) is None or abs(finite(data['fcf']) - fcf) > (reviewed['reported_precision_usd'] if reviewed_match else 0)):
        issues.append("Stored free cash flow disagrees with OCF minus absolute capex.")
    yield_value = fcf / cap if fcf is not None and cap is not None and cap > 0 and currency_match else None
    if yield_value is not None and abs(yield_value) >= 1:
        issues.append("FCF yield magnitude is at least 100%; investigate capital structure, market cap and units.")
    if eligibility["status"] != "eligible":
        issues.extend(eligibility["reasons"])
    status = "excluded" if eligibility["status"] == "excluded" else "needs_evidence" if issues else "documented_inputs"
    return {"ticker": saved["ticker"], "company_name": (identity or {}).get("title") or saved.get("company_name"),
            "model_version": VERSION, "status": status, "rebuilt_at": at, "input_observed_at": fetched_at,
            "financial_period_end": period, "provider_period_label": provider_period,
            "financial_source": reviewed['statement_basis'] if reviewed_match else data.get("financial_data_source") if normalized else None,
            "quote_as_of": data.get("quote_as_of") if normalized else None, "quote_source": data.get("quote_source") if normalized else None,
            "quote_currency": data.get('quote_currency') if normalized else None,
            "financial_currency": financial_currency,
            "price": price, "market_cap": cap, "shares_outstanding": shares, "financials": financials,
            "fcf": fcf, "fcf_yield": yield_value, "financial_provenance": provenance,
            "primary_fact_comparison": primary_comparison,
            "identity": identity, "instrument_eligibility": eligibility, "issues": issues,
            # Hash even invalid legacy JSON numbers without placing NaN/Infinity
            # in the public payload. Such caches cannot be certified as inputs.
            "input_sha256": digest(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=True)), "legacy_analyzed_at": saved.get("analyzed_at"),
            "legacy_fcf_yield": finite(saved.get("fcf_yield")), "legacy_quant_score": finite(saved.get("quant_score")),
            "limitations": "Documented inputs refer only to the required annual fields and quote checks. Other ratios, forecasts, trader profitability and historical execution are not certified."}


def rebuild_list(path, basis="Saved list + normalized cache; no invented refresh timestamps"):
    with _REBUILD_LOCK:
        return _rebuild_list(path,basis)


def _rebuild_list(path,basis):
    at = now()
    with sqlite3.connect(path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        refs = identities(db)
        reviewed = {r[0]: json.loads(r[1]) for r in db.execute('SELECT ticker,evidence_json FROM reviewed_statement_evidence WHERE id IN (SELECT MAX(id) FROM reviewed_statement_evidence GROUP BY ticker)')}
        cache = {}
        for ticker, raw, fetched in db.execute('SELECT ticker,data_json,fetched_at FROM market_data_cache'):
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('Invalid cache object')
            except (ValueError, TypeError):
                data = {'_cache_error': 'Invalid input JSON; source refresh required', '_original_sha256': digest(str(raw))}
            cache[ticker] = (data, fetched)
        from financial_sources import compare_primary, REBUILD_PROOFS
        mapping_cache = {}
        items = [rebuild_item(dict(r), cache.get(r["ticker"]), refs.get(r["ticker"]), at, reviewed.get(r['ticker']),
                             compare_primary(db, cache.get(r['ticker'],({},None))[0], refs.get(r['ticker']), at, mapping_cache=mapping_cache, proof_cache=REBUILD_PROOFS))
                 for r in db.execute("SELECT * FROM company_analyses ORDER BY ticker")]
        content_hash = digest(encoded(items))
        # Inputs were read from one consistent snapshot. Publish those frozen
        # values in a short write transaction, leaving pause/cancel and source
        # progress writes available during expensive original-fact validation.
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        version_id = db.execute("INSERT INTO saved_list_versions(created_at,model_version,basis,item_count,content_hash) VALUES(?,?,?,?,?)",
                                (at, VERSION, basis, len(items), content_hash)).lastrowid
        db.executemany("INSERT INTO saved_list_items VALUES(?,?,?,?)",
                       [(version_id, item["ticker"], item["status"], encoded(item)) for item in items])
    return {"version_id": version_id, "items": len(items), "content_hash": content_hash}


class RefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=25, ge=1, le=100)


def make_research_router(path, gather, collect_identities=None):
    router = APIRouter(prefix="/api/research", tags=["Verified research"])
    lock = threading.Lock()

    @router.get('/coverage')
    def inspect_coverage():
        from financial_sources import coverage
        with sqlite3.connect(path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            return coverage(db, now())

    @router.post('/coverage/refresh')
    def expand_coverage():
        plan = inspect_coverage()
        if not plan['next_tickers']:
            raise HTTPException(409, 'No eligible original-portfolio symbols need source evidence in this edition.')
        return refresh(RefreshRequest(tickers=plan['next_tickers']))

    @router.get('/coverage/sources/{identifier}')
    def original_financial_source(identifier: str):
        import gzip
        from fastapi.responses import Response
        with sqlite3.connect(path, timeout=30) as db:
            row = db.execute('SELECT sha256,status_code,body_gzip FROM financial_source_documents WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Financial source receipt not found.')
        try:
            raw = gzip.decompress(row[2])
        except (OSError, EOFError):
            raise HTTPException(409, 'Financial source archive differs.')
        if hashlib.sha256(raw).hexdigest() != row[0]:
            raise HTTPException(409, 'Original source fingerprint differs.')
        return Response(raw, media_type='application/json' if row[1]==200 else 'text/plain',
                        headers={'Content-Disposition':'attachment; filename="sec-financial-source.json"', 'X-Source-SHA256':row[0]})

    @router.get("")
    def overview(version: int | None = None, status: str = Query("all", pattern="^(all|documented_inputs|needs_evidence|excluded)$"),
                 search: str = Query("", max_length=100), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        with sqlite3.connect(path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            editions = [dict(r) for r in db.execute("SELECT * FROM saved_list_versions ORDER BY id DESC LIMIT 50")]
            version = version if version is not None else editions[0]["id"] if editions else None
            counts = dict(db.execute("SELECT status,COUNT(*) FROM saved_list_items WHERE version_id=? GROUP BY status", (version,)))
            params = (version, status, status, "%" + search.upper() + "%")
            where = "version_id=? AND (?='all' OR status=?) AND UPPER(ticker) LIKE ?"
            total = db.execute("SELECT COUNT(*) FROM saved_list_items WHERE " + where, params).fetchone()[0]
            rows = db.execute("SELECT payload_json FROM saved_list_items WHERE " + where + " ORDER BY ticker LIMIT ? OFFSET ?", (*params, limit, offset))
            items = [json.loads(r[0]) for r in rows]
            # Readiness expires; immutable historical contents are retained.
            for item in items:
                item["currently_fresh"] = currently_fresh(item, now())
            job = db.execute("SELECT * FROM research_refresh_jobs ORDER BY id DESC LIMIT 1").fetchone()
            batch = db.execute("SELECT id,retrieved_at FROM identity_batches ORDER BY retrieved_at DESC LIMIT 1").fetchone()
        return {"version": version, "versions": editions, "counts": counts, "total": total, "items": items,
                "identity_batch": dict(batch) if batch else None, "refresh_job": dict(job) if job else None,
                "methodology": "Immutable list editions. Unknown identities and undocumented or stale financial inputs are quarantined. Legacy analyses remain available in the original analyzer. Documented inputs do not establish investment returns."}

    @router.get("/history/{ticker}")
    def history(ticker: str):
        with sqlite3.connect(path, timeout=15) as db:
            versions = [{"version_id": r[0], "analysis": json.loads(r[1])} for r in db.execute(
                "SELECT version_id,payload_json FROM saved_list_items WHERE ticker=? ORDER BY version_id DESC LIMIT 50", (ticker.upper(),))]
            changes = [{"observed_at": r[0], "operation": r[1], "legacy_analysis": json.loads(r[2])} for r in db.execute(
                "SELECT observed_at,operation,row_json FROM legacy_analysis_revisions WHERE ticker=? ORDER BY id DESC LIMIT 50", (ticker.upper(),))]
        return {"ticker": ticker.upper(), "versions": versions, "legacy_changes": changes}

    @router.post("/identities/sync")
    def sync():
        try:
            result = sync_identities(path)
            return {**result, **rebuild_list(path, "Official Nasdaq directories + saved cache audit")}
        except (requests.RequestException, ValueError):
            raise HTTPException(502, "Official directory refresh failed. Previous dated identity evidence was retained.")

    @router.post("/rebuild")
    def rebuild():
        return rebuild_list(path)

    def run(job_id, tickers):
        try:
            for index, ticker in enumerate(tickers):
                with sqlite3.connect(path, timeout=15) as db:
                    if db.execute("SELECT state FROM research_refresh_jobs WHERE id=?", (job_id,)).fetchone()[0] != "running":
                        break
                    db.execute("UPDATE research_refresh_jobs SET current_ticker=?,updated_at=? WHERE id=?", (ticker, now(), job_id))
                    denied = db.execute('SELECT observed_at FROM financial_source_documents WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
                    if denied and 0 <= (instant(now())-instant(denied[0])).total_seconds() < 900:
                        db.execute("UPDATE research_refresh_jobs SET state='blocked',errors_json=?,updated_at=? WHERE id=?", (encoded([{'message':'SEC access paused after a source denial; retry only after access recovers.'}]),now(),job_id))
                        break
                    if shutil.disk_usage(Path(path).parent).free < 256_000_000:
                        db.execute("UPDATE research_refresh_jobs SET state='blocked',errors_json=?,updated_at=? WHERE id=?", (encoded([{'message':'Storage reserve reached; no further source requests started.'}]),now(),job_id))
                        break
                error = None
                provider_blocked = False
                try:
                    gather(ticker, force_refresh=True, sync_sqlite=True)
                except Exception as failure:
                    provider_blocked = (isinstance(failure, HTTPException) and failure.status_code in (429,503)) or type(failure).__name__ == 'YFRateLimitError'
                    error = {"ticker": ticker, "message": "Source refresh failed; previous data retained and remains dated."}
                with sqlite3.connect(path, timeout=15) as db:
                    errors = json.loads(db.execute("SELECT errors_json FROM research_refresh_jobs WHERE id=?", (job_id,)).fetchone()[0])
                    if error:
                        errors.append(error)
                    db.execute("UPDATE research_refresh_jobs SET completed=?,failed=?,errors_json=?,updated_at=? WHERE id=?",
                               (index + 1, len(errors), encoded(errors), now(), job_id))
                    db.execute('INSERT INTO research_refresh_attempts VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET attempted_at=excluded.attempted_at,state=excluded.state',
                               (ticker, now(), 'error' if error else 'fetched'))
                    if provider_blocked:
                        db.execute("UPDATE research_refresh_jobs SET state='blocked',errors_json=?,updated_at=? WHERE id=?", (encoded(errors+[{'message':'Market provider unavailable or rate limited; remaining companies retained for a later resume.'}]),now(),job_id))
                    denied = db.execute('SELECT observed_at FROM financial_source_documents WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
                    if denied and 0 <= (instant(now())-instant(denied[0])).total_seconds() < 900:
                        db.execute("UPDATE research_refresh_jobs SET state='blocked',errors_json=?,updated_at=? WHERE id=?", (encoded(errors+[{'message':'SEC access paused after a source denial; remaining companies retained for later review.'}]),now(),job_id))
            rebuild_list(path, "Bounded source refresh followed by full saved-list audit")
            with sqlite3.connect(path, timeout=15) as db:
                db.execute("UPDATE research_refresh_jobs SET state=CASE WHEN state='running' THEN 'completed' ELSE state END,current_ticker=NULL,updated_at=? WHERE id=?", (now(), job_id))
        except Exception:
            with sqlite3.connect(path, timeout=15) as db:
                db.execute("UPDATE research_refresh_jobs SET state='error',current_ticker=NULL,updated_at=? WHERE id=?", (now(), job_id))
        finally:
            lock.release()

    @router.post("/refresh")
    def refresh(body: RefreshRequest):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "A research refresh is already running.")
        job_id = None
        try:
            with sqlite3.connect(path, timeout=15) as db:
                available = {r[0] for r in db.execute("SELECT ticker FROM company_analyses")}
                if body.tickers:
                    tickers = list(dict.fromkeys(t.strip().upper() for t in body.tickers))
                    if any(t not in available for t in tickers):
                        raise HTTPException(422, "Refresh tickers must belong to the saved list.")
                else:
                    # Oldest observations first; repeat batches progress through the list.
                    tickers = [r[0] for r in db.execute("SELECT a.ticker FROM company_analyses a LEFT JOIN market_data_cache m ON m.ticker=a.ticker LEFT JOIN research_refresh_attempts r ON r.ticker=a.ticker ORDER BY COALESCE(r.attempted_at,m.fetched_at,''),a.ticker LIMIT ?", (body.limit,))]
                job_id = db.execute("INSERT INTO research_refresh_jobs(started_at,updated_at,state,tickers_json) VALUES(?,?,?,?)", (now(), now(), "running", encoded(tickers))).lastrowid
            threading.Thread(target=run, args=(job_id, tickers), daemon=True).start()
            return {"job_id": job_id, "count": len(tickers), "state": "running"}
        except Exception:
            if job_id is not None:
                with sqlite3.connect(path, timeout=15) as db:
                    db.execute("UPDATE research_refresh_jobs SET state='error',current_ticker=NULL,updated_at=? WHERE id=?", (now(),job_id))
            lock.release()
            raise

    @router.post("/refresh/cancel")
    def cancel():
        with sqlite3.connect(path, timeout=15) as db:
            count = db.execute("UPDATE research_refresh_jobs SET state='cancelled',updated_at=? WHERE state='running'", (now(),)).rowcount
        return {"cancelled": count, "message": "The current source request finishes; remaining tickers will be skipped."}

    @router.post('/refresh/resume/{job_id}')
    def resume(job_id: int):
        with sqlite3.connect(path, timeout=15) as db:
            row = db.execute('SELECT state,tickers_json,completed FROM research_refresh_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Refresh job not found')
        if row[0] not in ('cancelled', 'interrupted', 'error', 'blocked'):
            raise HTTPException(409, 'Only stopped refreshes can be resumed')
        remaining = json.loads(row[1])[row[2]:]
        if not remaining:
            raise HTTPException(409, 'No unattempted tickers remain; retry failed tickers explicitly')
        return refresh(RefreshRequest(tickers=remaining))

    def daily_refresh():
        # Maintain the independently reviewed starter set without an unbounded
        # provider crawl. Every refresh still audits the complete saved list.
        try:
            sync_identities(path)
        except (requests.RequestException, ValueError):
            pass  # existing dated evidence is retained and freshness can expire
        if collect_identities:
            try:
                collect_identities()
            except (requests.RequestException, ValueError, OSError):
                pass  # dated classes expire; no company-name inference
        with sqlite3.connect(path, timeout=15) as db:
            tickers = [r[0] for r in db.execute('SELECT DISTINCT a.ticker FROM company_analyses a JOIN reviewed_statement_evidence s ON s.ticker=a.ticker ORDER BY a.ticker LIMIT 25')]
        plan = inspect_coverage()
        tickers = list(dict.fromkeys(tickers + plan['maintenance_tickers'] + plan['next_tickers'][:10]))
        if tickers:
            try:
                refresh(RefreshRequest(tickers=tickers))
            except HTTPException:
                pass  # manual refresh already owns the source worker
        else:
            rebuild_list(path, 'Daily source-directory and saved-input audit')

    router.daily_refresh = daily_refresh
    router.refresh_inputs = refresh

    return router
