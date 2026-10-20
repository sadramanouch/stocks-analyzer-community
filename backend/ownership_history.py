"""SEC Form 4 history with filing/owner/table/line identity and raw evidence.

Transactions are reported activity, not verified executions or actor returns.
Amendments are preserved and quarantined until the original lines are reconciled.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from collections import Counter
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from research_store import immutable, now, instant
from trade_evidence import init_store

SPEC = "https://www.sec.gov/info/edgar/ownershipxmltechspec-v3.pdf"


def init_ownership(path):
    init_store(path)
    with sqlite3.connect(path, timeout=30) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS ownership_documents (
          accession TEXT PRIMARY KEY, source_url TEXT NOT NULL, sha256 TEXT NOT NULL,
          observed_at TEXT NOT NULL, filed_date TEXT NOT NULL, document_xml TEXT NOT NULL,
          metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ownership_history_runs (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
          state TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0, imported INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0, oldest_date TEXT, newest_date TEXT,
          selected INTEGER NOT NULL, indexed_total INTEGER NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ownership_index_snapshots (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, issuer_cik TEXT NOT NULL,
          observed_at TEXT NOT NULL, source_url TEXT NOT NULL, sha256 TEXT NOT NULL,
          source_json TEXT NOT NULL, added_references INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS ownership_import_attempts (
          id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, accession TEXT NOT NULL,
          attempted_at TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL,
          document_sha256 TEXT);
        CREATE TABLE IF NOT EXISTS ownership_index_documents (
          ticker TEXT NOT NULL, source_url TEXT NOT NULL, sha256 TEXT NOT NULL,
          observed_at TEXT NOT NULL, document_json TEXT NOT NULL, kind TEXT NOT NULL,
          descriptor_json TEXT NOT NULL, ownership_references INTEGER NOT NULL,
          unsupported_references INTEGER NOT NULL,
          PRIMARY KEY(ticker,source_url,sha256));
        """)
        immutable(db, "ownership_documents")
        immutable(db, 'ownership_index_snapshots')
        immutable(db, 'ownership_import_attempts')
        immutable(db, 'ownership_index_documents')


def ownership_references(recent, cik, legacy=False, bounds=None):
    fields = ('form','accessionNumber','filingDate','primaryDocument')
    if any(not isinstance(recent.get(f),list) for f in fields) or len({len(recent[f]) for f in fields}) != 1:
        raise ValueError('SEC submissions arrays have inconsistent lengths')
    rows, unsupported = [], 0
    for index, form in enumerate(recent['form']):
        filed = recent['filingDate'][index]
        date.fromisoformat(filed)
        if filed > datetime.now(timezone.utc).date().isoformat() or (bounds and not bounds[0] <= filed <= bounds[1]):
            raise ValueError('SEC index contains an invalid filing date')
        if form not in ('4','4/A'):
            continue
        accn, document = recent['accessionNumber'][index], recent['primaryDocument'][index]
        if not isinstance(accn,str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accn):
            raise ValueError('Invalid SEC accession')
        if legacy and (not document or (isinstance(document,str) and not document.lower().endswith('.xml'))):
            unsupported += 1
            continue  # retained in raw index; legacy text is not parsed as XML
        if not isinstance(document,str) or not document:
            raise ValueError('Invalid SEC document identity')
        source_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-','')}/{document}"
        xml_url(source_url,accn)
        rows.append({'accession':accn,'filed_date':filed,'form':form,'url':source_url})
    return rows, unsupported


def older_descriptor(value, cik):
    if not isinstance(value,dict) or not re.fullmatch(rf'CIK{cik.zfill(10)}-submissions-\d{{3,}}\.json',str(value.get('name',''))):
        raise ValueError('Invalid historical SEC index filename')
    start, end = value.get('filingFrom'), value.get('filingTo')
    date.fromisoformat(start); date.fromisoformat(end)
    if start > end or end > datetime.now(timezone.utc).date().isoformat():
        raise ValueError('Invalid historical SEC index range')
    if type(value.get('filingCount')) is not int or not 0 <= value['filingCount'] <= 100_000:
        raise ValueError('Invalid historical SEC index count')
    return value


def store_index_document(db, ticker, url, response, kind, descriptor, rows, unsupported=0):
    raw = response.content.decode('utf-8')
    if len(response.content) > 10_000_000:
        raise ValueError('SEC index is oversized')
    sha = hashlib.sha256(response.content).hexdigest()
    db.execute('INSERT OR IGNORE INTO ownership_index_documents VALUES(?,?,?,?,?,?,?,?,?)',
               (ticker,url,sha,now(),raw,kind,json.dumps(descriptor,sort_keys=True),len(rows),unsupported))
    return sha


def insert_references(db, company_id, rows):
    before = db.total_changes
    db.executemany('INSERT OR IGNORE INTO filings(company_id,form_type,filed_date,accession_number,url) SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM filings WHERE company_id=? AND accession_number=?)',
                   [(company_id,r['form'],r['filed_date'],r['accession'],r['url'],company_id,r['accession']) for r in rows])
    return db.total_changes-before


def refresh_issuer_index(path, ticker, fetch=None, resolve_issuer=None, older_files_limit=0):
    """Refresh a confirmed issuer, retaining every earlier filing reference.

    Missing local issuers can resolve through SEC's ticker map, but registration
    waits for matching current submissions. Older files need separate backfills.
    """
    with sqlite3.connect(path, timeout=15) as db:
        company = db.execute('SELECT id,cik FROM companies WHERE ticker=?', (ticker,)).fetchone()
    mapped = resolve_issuer(ticker) if not company and resolve_issuer else None
    candidate = company[1] if company else mapped.get('cik') if isinstance(mapped,dict) else None
    if not str(candidate).isdigit() or not 0 < int(candidate) < 10**10:
        raise ValueError('No confirmed issuer CIK for this ticker')
    cik = str(int(candidate))
    url = f'https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json'
    response = (fetch or requests.get)(url, headers={'User-Agent': os.environ.get('EDGAR_USER_AGENT', '')}, timeout=(5,20), allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('Unexpected SEC submissions response')
    data = response.json()
    if not isinstance(data,dict) or not isinstance(data.get('filings'),dict) or not isinstance(data['filings'].get('recent'),dict):
        raise ValueError('Invalid SEC submissions object')
    if str(data.get('cik','')).lstrip('0') != cik or ticker not in data.get('tickers',[]):
        raise ValueError('SEC issuer/ticker does not match; historical identity review required')
    # A low-volume issuer's current submissions can still include pre-XML
    # filings. Archive those original metadata entries as unsupported context;
    # they must not prevent recent, strictly validated XML from being checked.
    rows, unsupported_current = ownership_references(data['filings']['recent'],cik,legacy=True)
    files = data['filings'].get('files',[])
    if not isinstance(files,list):
        raise ValueError('Invalid historical SEC manifest')
    # Validate before registering an issuer or requesting any additional URL.
    if older_files_limit:
        files = [older_descriptor(f,cik) for f in files]
    at = now()
    source = {'cik':cik,'tickers':data['tickers'],'ownership_filings':rows,
              'older_submission_files':data.get('filings',{}).get('files',[]),
              'unsupported_current_references':unsupported_current}
    sha = hashlib.sha256(response.content).hexdigest()
    with sqlite3.connect(path,timeout=30) as db:
        if not company:
            db.execute('INSERT OR IGNORE INTO companies(ticker,cik,name,last_updated) VALUES(?,?,?,?)',
                       (ticker,cik,data.get('name'),at))
            company = db.execute('SELECT id,cik FROM companies WHERE ticker=?',(ticker,)).fetchone()
            if str(company[1]).lstrip('0') != cik:
                raise ValueError('Concurrent issuer identity differs; reconciliation required')
        added = insert_references(db,company[0],rows)
        store_index_document(db,ticker,url,response,'current',{},rows,unsupported_current)
        db.execute('INSERT INTO ownership_index_snapshots(ticker,issuer_cik,observed_at,source_url,sha256,source_json,added_references) VALUES(?,?,?,?,?,?,?)',
                   (ticker,cik,at,url,sha,json.dumps(source),added))
    fetched = 0
    for descriptor in sorted(files,key=lambda f:f.get('filingTo',''),reverse=True) if older_files_limit else []:
        archive_url = 'https://data.sec.gov/submissions/'+descriptor['name']
        with sqlite3.connect(path,timeout=15) as db:
            prior=db.execute('SELECT 1 FROM ownership_index_documents WHERE ticker=? AND source_url=? AND descriptor_json=?',
                             (ticker,archive_url,json.dumps(descriptor,sort_keys=True))).fetchone()
        if prior:
            continue
        if fetched >= older_files_limit:
            break
        archive = (fetch or requests.get)(archive_url,headers={'User-Agent':os.environ.get('EDGAR_USER_AGENT','')},timeout=(5,20),allow_redirects=False)
        archive.raise_for_status()
        if archive.status_code != 200:
            raise ValueError('Unexpected historical SEC index response')
        historical = archive.json()
        if not isinstance(historical,dict) or len(historical.get('form',[])) != descriptor['filingCount']:
            raise ValueError('Historical SEC index differs from manifest count')
        references, unsupported = ownership_references(historical,cik,legacy=True,bounds=(descriptor['filingFrom'],descriptor['filingTo']))
        with sqlite3.connect(path,timeout=30) as db:
            added += insert_references(db,company[0],references)
            store_index_document(db,ticker,archive_url,archive,'historical',descriptor,references,unsupported)
        fetched += 1
        import time
        time.sleep(.25)
    return {'added':added,'observed_at':at,'source_rows':len(rows),'unsupported_current_references':unsupported_current,'older_files_fetched':fetched,
            'latest_date':max((r['filed_date'] for r in rows),default=None)}


def xml_url(source_url, accession):
    parts = urlsplit(source_url)
    if parts.scheme != "https" or parts.hostname != "www.sec.gov" or parts.port or parts.query or parts.fragment:
        raise ValueError("Source must be an HTTPS SEC archive document")
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("Invalid accession")
    path = re.sub(r"/xslF\d+X\d+/", "/", parts.path)
    match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/([A-Za-z0-9_.-]+\.xml)", path)
    if not match or match[2] != accession.replace("-", ""):
        raise ValueError("Accession and document URL do not match")
    return "https://www.sec.gov" + path


def dec(text, field, zero=True):
    if text is None:
        return None
    try:
        value = Decimal(text)
        if not value.is_finite() or value < 0 or value > Decimal('1e18') or (not zero and value == 0):
            raise ValueError("Invalid " + field)
        return str(value)
    except InvalidOperation:
        raise ValueError("Invalid " + field)


def parse_form4(xml, accession, source_url, filed_date, observed_at):
    url = xml_url(source_url, accession)
    if len(xml.encode()) > 2_000_000 or re.search(r"<!DOCTYPE|<!ENTITY", xml, re.I):
        raise ValueError("Unsafe or oversized XML")
    root = ET.fromstring(xml)
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    if root.tag != "ownershipDocument" or root.findtext("documentType") not in {"4", "4/A"}:
        raise ValueError("Not a Form 4 ownership document")
    filed = date.fromisoformat(filed_date)
    observed = instant(observed_at)
    if observed is None or filed > observed.date():
        raise ValueError("Filing date is in the future")
    ticker = (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper()
    issuer = (root.findtext("issuer/issuerCik") or "").lstrip("0")
    if not ticker or not issuer.isdigit():
        raise ValueError("Missing issuer identity")
    owners = []
    for owner in root.findall("reportingOwner"):
        cik = (owner.findtext("reportingOwnerId/rptOwnerCik") or "").lstrip("0")
        name = owner.findtext("reportingOwnerId/rptOwnerName")
        if not cik.isdigit() or not name:
            raise ValueError("Missing reporting owner identity")
        rel = owner.find("reportingOwnerRelationship")
        owners.append({"cik": cik, "name": name, "relationship": {n.tag: n.text for n in rel} if rel is not None else {}})
    if not owners:
        raise ValueError("No reporting owner")
    actor_id = "sec:" + ",".join(sorted(owner["cik"] for owner in owners))
    actor = "; ".join(owner["name"] for owner in owners)
    amendment = root.findtext("documentType") == "4/A"
    plan = root.findtext("aff10b5One")
    footnotes = {n.get("id"): " ".join(n.itertext()).strip() for n in root.findall("footnotes/footnote")}
    events = []
    for table in ("nonDerivative", "derivative"):
        for line, transaction in enumerate(root.findall(f"{table}Table/{table}Transaction"), 1):
            title = transaction.findtext("securityTitle/value") or "Unknown"
            trade_date = date.fromisoformat(transaction.findtext("transactionDate/value") or "")
            code = transaction.findtext("transactionCoding/transactionCode") or ""
            unconfirmed_award = trade_date > filed and code == 'A'
            if trade_date > filed and not unconfirmed_award:
                raise ValueError("Transaction date is after filing")
            direction = transaction.findtext("transactionAmounts/transactionAcquiredDisposedCode/value")
            shares = dec(transaction.findtext("transactionAmounts/transactionShares/value"), "shares", zero=False)
            price = dec(transaction.findtext("transactionAmounts/transactionPricePerShare/value"), "price")
            if not shares or direction not in {"A", "D"} or (code == "P" and direction != "A") or (code == "S" and direction != "D"):
                raise ValueError("Invalid share amount or transaction direction")
            if code in {"P", "S"} and (price is None or Decimal(price) <= 0):
                # A footnote-only price cannot be safely reconstructed.
                price = None
            refs = [n.get("id") for n in transaction.iter("footnoteId")]
            if any(ref not in footnotes for ref in refs):
                raise ValueError("Unresolved footnote reference")
            common = table == "nonDerivative" and bool(re.search(r"\bcommon (?:stock|shares?)\b|\bordinary shares?\b", title, re.I)) and not re.search(r"preferred|deposit[ao]ry", title, re.I)
            available = observed.isoformat()
            notes = ["Original SEC filing parsed; reported shares and prices are not broker-verified executions.",
                     "Filing and transaction dates have day precision; first observation controls available-at.",
                     "History is bounded by the stored filing index. Complete holdings, costs and amendments are required before calculating trader returns."]
            if amendment:
                notes.append("Amendment retained separately; original-line reconciliation is unresolved. Excluded from copy research.")
            if refs:
                notes.append("Transaction footnotes require review.")
            if plan in {"1", "true"}:
                notes.append("Filing marks a Rule 10b5-1 trading plan; discretion is not established.")
            if unconfirmed_award:
                notes.append('Award date is reported after the filing date. A prospective award or source error requires manual review; this is not a confirmed execution.')
            side = "buy" if code == "P" else "sell" if code == "S" else "other"
            if (amendment or refs or plan in {"1", "true"} or not common or price is None or unconfirmed_award):
                review = "needs_filing_review"
            else:
                review = "primary_parsed"
            canonical = f"{accession}:{actor_id}:{table}:{line}"
            amount = Decimal(shares) * Decimal(price) if price is not None else None
            events.append({"id": hashlib.sha256(canonical.encode()).hexdigest(), "canonical_id": canonical,
                "kind": "insiders", "actor": actor, "actor_id": actor_id, "asset": ticker,
                "instrument": "CS" if common else title, "side": side, "token_address": None,
                "amount_usd": float(amount) if amount is not None else None,
                "amount_label": f"Reported {shares} shares × ${price}" if price is not None else f"Reported {shares} shares; price unresolved",
                "reported_shares": shares, "reported_price": price, "transaction_code": code,
                "ownership": transaction.findtext("ownershipNature/directOrIndirectOwnership/value"),
                "holdings_after": transaction.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                "traded_at": trade_date.isoformat() + "T00:00:00+00:00", "disclosed_at": filed_date + "T00:00:00+00:00",
                "traded_at_display": trade_date.isoformat() + (" (award after filing; unconfirmed)" if unconfirmed_award else " (date only)"), "disclosed_at_display": filed_date + " (date only)",
                'date_status':'unconfirmed_award' if unconfirmed_award else 'reported_date_only',
                "first_seen_at": available, "available_at": available,
                "disclosure_lag_days": (filed - trade_date).days, "source_url": url, "source_label": "Original SEC Form 4",
                "source_verification": review, "accession": accession, "issuer_cik": issuer,
                "owners": owners, "table": table, "line": line, "amendment": amendment,
                "original_submission_date": root.findtext("dateOfOriginalSubmission"), "trading_plan": plan,
                "footnotes": {ref: footnotes[ref] for ref in refs}, "notes": notes})
    return {"ticker": ticker, "issuer_cik": issuer, "owners": owners, "amendment": amendment,
            "original_submission_date": root.findtext("dateOfOriginalSubmission"), "schema": "sec-form4-v1",
            "events": events, "source_url": url}


def save_form4(path, xml, accession, source_url, filed_date, observed_at=None, expected_ticker=None, expected_cik=None):
    observed_at = observed_at or now()
    parsed = parse_form4(xml, accession, source_url, filed_date, observed_at)
    if expected_ticker and parsed["ticker"] != expected_ticker.upper():
        raise ValueError("Filing ticker differs from the stored index; historical symbol reconciliation is required")
    if expected_cik and parsed["issuer_cik"] != str(expected_cik).lstrip("0"):
        raise ValueError("Filing issuer differs from stored CIK")
    sha = hashlib.sha256(xml.encode()).hexdigest()
    inserted = 0
    with sqlite3.connect(path, timeout=30) as db:
        prior = db.execute("SELECT sha256 FROM ownership_documents WHERE accession=?", (accession,)).fetchone()
        if prior:
            if prior[0] != sha:
                raise ValueError("Same accession has different content; reconciliation required")
            return {"inserted": 0, "accession": accession, "duplicate": True}
        db.execute("INSERT INTO ownership_documents VALUES(?,?,?,?,?,?,?)", (accession, parsed["source_url"], sha,
                   observed_at, filed_date, xml, json.dumps({k: v for k, v in parsed.items() if k != "events"})))
        for event in parsed["events"]:
            inserted += db.execute("INSERT OR IGNORE INTO trade_events(id,kind,event_json,first_seen_at) VALUES(?,?,?,?)",
                (event["id"], "insiders", json.dumps(event), event["first_seen_at"])).rowcount
            db.execute("INSERT OR IGNORE INTO trade_observations VALUES(?,?,?,?)", (event["id"], sha, observed_at,
                json.dumps({"accession": accession, "source_url": parsed["source_url"], "document_sha256": sha, "table": event["table"], "line": event["line"]})))
    return {"inserted": inserted, "accession": accession, "duplicate": False}


class HistoryRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-^]+$")
    limit: int = Field(default=10, ge=1, le=100)
    refresh_index: bool = True
    older_files_limit: int = Field(default=0, ge=0, le=5)
    since_date: date | None = None


def make_ownership_router(path, resolve_issuer=None):
    router = APIRouter(prefix="/api/trade-history", tags=["Primary trade histories"])
    lock = threading.Lock()

    @router.get("")
    def overview(ticker: str = Query("", max_length=16, pattern=r"^[A-Za-z0-9.\-^]*$")):
        with sqlite3.connect(path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            runs = [dict(r) for r in db.execute("SELECT * FROM ownership_history_runs WHERE (?='' OR ticker=?) ORDER BY id DESC LIMIT 20", (ticker.upper(), ticker.upper()))]
            documents = db.execute("SELECT COUNT(*) FROM ownership_documents WHERE (?='' OR json_extract(metadata_json,'$.ticker')=?)", (ticker.upper(), ticker.upper())).fetchone()[0]
            lines = db.execute("SELECT COUNT(*) FROM trade_events WHERE json_extract(event_json,'$.accession') IS NOT NULL AND (?='' OR json_extract(event_json,'$.asset')=?)", (ticker.upper(), ticker.upper())).fetchone()[0]
            indexed = db.execute("SELECT COUNT(*) FROM filings f JOIN companies c ON c.id=f.company_id WHERE f.form_type IN ('4','4/A') AND (?='' OR c.ticker=?)", (ticker.upper(), ticker.upper())).fetchone()[0]
            # Date range refers to the filing index, NOT proof of complete trades.
            dates = db.execute("SELECT MIN(f.filed_date),MAX(f.filed_date) FROM filings f JOIN companies c ON c.id=f.company_id WHERE f.form_type IN ('4','4/A') AND (?='' OR c.ticker=?)", (ticker.upper(), ticker.upper())).fetchone()
            latest_index = db.execute("SELECT ticker,observed_at,source_url,sha256,added_references FROM ownership_index_snapshots WHERE (?='' OR ticker=?) ORDER BY id DESC LIMIT 1",(ticker.upper(),ticker.upper())).fetchone()
            failed_attempts = [dict(r) for r in db.execute("SELECT a.accession,a.attempted_at,a.state,a.message FROM ownership_import_attempts a JOIN ownership_history_runs r ON r.id=a.run_id WHERE a.state NOT IN ('imported','duplicate') AND (?='' OR r.ticker=?) ORDER BY a.id DESC LIMIT 20",(ticker.upper(),ticker.upper()))]
            index_archives=[]
            current_indexes=db.execute("SELECT d.* FROM ownership_index_documents d WHERE d.kind='current' AND (?='' OR d.ticker=?) AND d.rowid=(SELECT x.rowid FROM ownership_index_documents x WHERE x.ticker=d.ticker AND x.kind='current' ORDER BY x.observed_at DESC LIMIT 1)",(ticker.upper(),ticker.upper()))
            for document in current_indexes:
                current=json.loads(document['document_json'])
                loaded=unsupported=invalid=0
                manifest=current.get('filings',{}).get('files',[])
                for descriptor in manifest:
                    try:older_descriptor(descriptor,str(current['cik']).lstrip('0'))
                    except (ValueError,TypeError,KeyError):
                        invalid+=1
                        continue
                    archived=db.execute("SELECT unsupported_references FROM ownership_index_documents WHERE ticker=? AND source_url=? AND descriptor_json=? ORDER BY observed_at DESC LIMIT 1",(document['ticker'],'https://data.sec.gov/submissions/'+descriptor['name'],json.dumps(descriptor,sort_keys=True))).fetchone()
                    if archived:
                        loaded+=1;unsupported+=archived[0]
                index_archives.append({'ticker':document['ticker'],'manifest_observed_at':document['observed_at'],
                    'unsupported_current_references':document['unsupported_references'],
                    'older_files_total':len(manifest),'older_files_archived':loaded,'older_files_remaining':len(manifest)-loaded,
                    'unsupported_legacy_references':unsupported,'invalid_manifest_entries':invalid})
            remaining_xml=db.execute("SELECT COUNT(*) FROM filings f JOIN companies c ON c.id=f.company_id LEFT JOIN ownership_documents d ON d.accession=f.accession_number WHERE f.form_type IN ('4','4/A') AND f.url LIKE '%.xml' AND d.accession IS NULL AND (?='' OR c.ticker=?)",(ticker.upper(),ticker.upper())).fetchone()[0]
        return {"indexed_filings": indexed, "index_date_range": list(dates), "parsed_documents": documents,
                "parsed_transaction_lines": lines, "runs": runs, "complete_history": False,
                "sec_user_agent_configured": bool(re.search(r"[^\s]+@[^\s]+", os.environ.get("EDGAR_USER_AGENT", ""))),
                "latest_index":dict(latest_index) if latest_index else None,'failed_attempts':failed_attempts,
                'index_archives':index_archives,'remaining_xml_documents':remaining_xml,
                "source": "https://www.sec.gov/search-filings", "specification": SPEC,
                "message": "Free original SEC filings; stored index may be stale or incomplete. Congress and crypto provider histories require their own access and validation. No actor profitability is established."}

    def select_rows(db,ticker,limit,since_date=None):
        return [dict(r) for r in db.execute("SELECT f.*,c.cik FROM filings f JOIN companies c ON c.id=f.company_id LEFT JOIN ownership_documents d ON d.accession=f.accession_number WHERE c.ticker=? AND f.form_type IN ('4','4/A') AND f.url LIKE '%.xml' AND d.accession IS NULL AND (? IS NULL OR f.filed_date>=?) ORDER BY f.filed_date DESC,f.accession_number DESC LIMIT ?",(ticker,since_date,since_date,limit))]

    def run(run_id, ticker, rows, limit, refresh_index, older_files_limit, since_date):
        imported = failed = attempted = 0
        blocked = False
        message = "Bounded history imported; the stored index is not proof of complete coverage."
        try:
            if refresh_index:
                try:
                    refresh_issuer_index(path,ticker,resolve_issuer=resolve_issuer,older_files_limit=older_files_limit)
                    with sqlite3.connect(path,timeout=30) as db:
                        db.row_factory=sqlite3.Row
                        rows=select_rows(db,ticker,limit,since_date)
                        total=db.execute("SELECT COUNT(*) FROM filings f JOIN companies c ON c.id=f.company_id WHERE c.ticker=? AND f.form_type IN ('4','4/A')",(ticker,)).fetchone()[0]
                        db.execute('UPDATE ownership_history_runs SET selected=?,indexed_total=?,oldest_date=?,newest_date=?,message=? WHERE id=?',
                                   (len(rows),total,min((r['filed_date'] for r in rows),default=None),max((r['filed_date'] for r in rows),default=None),'Importing original documents from refreshed SEC index',run_id))
                except (requests.RequestException,ValueError,TypeError,KeyError,HTTPException):
                    blocked=True
                    message='Current SEC index refresh failed; old index and documents retained. Check contact, source availability and issuer identity before retrying.'
                    return
            for row in rows:
                attempted += 1
                outcome, detail, document_sha = 'failed', 'Source request or validation failed; original evidence retained.', None
                try:
                    url = xml_url(row["url"], row["accession_number"])
                    response = requests.get(url, headers={"User-Agent": os.environ.get("EDGAR_USER_AGENT", "StockAnalyzer https://github.com/sadramanouch/stocks-analyzer")}, timeout=(5, 20), allow_redirects=False)
                    if response.status_code in (403, 429, 503):
                        failed += 1
                        blocked = True
                        message = f"SEC returned HTTP {response.status_code}; paused to avoid repeated blocked requests. Configure an operator contact in EDGAR_USER_AGENT and retry when access is available. Existing evidence retained."
                        detail=message
                        break
                    response.raise_for_status()
                    if 300 <= response.status_code < 400:
                        raise ValueError("Unexpected redirect")
                    xml = response.content.decode('utf-8')
                    result = save_form4(path, xml, row["accession_number"], url, row["filed_date"],
                                        expected_ticker=ticker, expected_cik=row["cik"])
                    imported += int(not result["duplicate"])
                    outcome='duplicate' if result['duplicate'] else 'imported'
                    detail='Original filing and transaction fields validated.'
                    document_sha=hashlib.sha256(response.content).hexdigest()
                except (ValueError, ET.ParseError) as error:
                    failed += 1
                    detail=str(error)[:300]
                except requests.RequestException:
                    failed += 1
                finally:
                    with sqlite3.connect(path,timeout=30) as db:
                        db.execute('INSERT INTO ownership_import_attempts(run_id,accession,attempted_at,state,message,document_sha256) VALUES(?,?,?,?,?,?)',
                                   (run_id,row['accession_number'],now(),outcome,detail,document_sha))
                        db.execute('UPDATE ownership_history_runs SET attempted=?,imported=?,failed=?,message=? WHERE id=?',
                                   (attempted,imported,failed,message,run_id))
                # Stay well below SEC's 10 requests/sec limit.
                import time
                time.sleep(.25)
        except Exception:
            blocked=True
            if attempted > imported + failed:
                failed+=1
            message='Import interrupted by an unexpected source or storage failure. Existing evidence retained; retry the remaining documents after resolving the failure.'
        finally:
            if not blocked:
                message=f'Bounded import finished: {imported} new documents, {failed} failed. Complete actor coverage and performance remain unverified.'
            try:
                with sqlite3.connect(path, timeout=30) as db:
                    db.execute("UPDATE ownership_history_runs SET finished_at=?,state=?,attempted=?,imported=?,failed=?,message=? WHERE id=?",
                               (now(), "blocked" if blocked else "partial" if failed else "bounded", attempted, imported, failed, message, run_id))
            finally:
                lock.release()

    @router.post("/sec/sync")
    def sync(body: HistoryRequest):
        if body.since_date and body.since_date > datetime.now(timezone.utc).date():
            raise HTTPException(422,'The filing cutoff cannot be future-dated.')
        if body.older_files_limit and not body.refresh_index:
            raise HTTPException(422,'Historical backfill requires a current issuer/manifest refresh.')
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "An SEC history import is already running.")
        try:
            ticker = body.ticker.upper()
            with sqlite3.connect(path, timeout=15) as db:
                db.row_factory = sqlite3.Row
                total = db.execute("SELECT COUNT(*) FROM filings f JOIN companies c ON c.id=f.company_id WHERE c.ticker=? AND f.form_type IN ('4','4/A')", (ticker,)).fetchone()[0]
                rows = select_rows(db,ticker,body.limit,body.since_date.isoformat() if body.since_date else None)
                if not rows and not body.refresh_index:
                    raise HTTPException(409, "No unparsed Form 4 references for this ticker in the stored filing index. Refresh its filings first.")
                run_id = db.execute("INSERT INTO ownership_history_runs(ticker,started_at,state,oldest_date,newest_date,selected,indexed_total,message) VALUES(?,?,'running',?,?,?,?,?)",
                    (ticker, now(), min((r["filed_date"] for r in rows),default=None), max((r["filed_date"] for r in rows),default=None), len(rows), total, "Refreshing current SEC index" if body.refresh_index else "Importing bounded original SEC documents")).lastrowid
            threading.Thread(target=run, args=(run_id, ticker, rows, body.limit, body.refresh_index, body.older_files_limit,body.since_date.isoformat() if body.since_date else None), daemon=True).start()
            return {"run_id": run_id, "selected": len(rows), "indexed_total": total}
        except Exception:
            lock.release()
            raise

    @router.get("/documents/{accession}")
    def evidence(accession: str):
        with sqlite3.connect(path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM ownership_documents WHERE accession=?", (accession,)).fetchone()
        if not row:
            raise HTTPException(404, "Original filing has not been imported")
        return dict(row)

    @router.get('/actors')
    def actors(ticker:str=Query('',max_length=16,pattern=r'^[A-Za-z0-9.\-^]*$'),search:str=Query('',max_length=100),
               offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=200)):
        with sqlite3.connect(path,timeout=15) as db:
            events=[json.loads(r[0]) for r in db.execute("SELECT event_json FROM trade_events WHERE json_extract(event_json,'$.accession') IS NOT NULL AND (?='' OR json_extract(event_json,'$.asset')=?)",(ticker.upper(),ticker.upper()))]
        grouped={}
        for event in events:
            actor=grouped.setdefault(event['actor_id'],{'actor_id':event['actor_id'],'name':event['actor'],'filings':set(),'assets':set(),'rows':[]})
            actor['filings'].add(event['accession']);actor['assets'].add(event['asset']);actor['rows'].append(event)
        result=[]
        for actor in grouped.values():
            rows=actor.pop('rows'); counts=Counter(e['transaction_code'] for e in rows)
            result.append({**actor,'filings':len(actor['filings']),'assets':sorted(actor['assets']),'transactions':len(rows),
                           'transaction_codes':dict(counts),'planned_trades':sum(e.get('trading_plan') in ('1','true') for e in rows),
                           'needs_filing_review':sum(e.get('source_verification')!='primary_parsed' for e in rows),
                           'first_reported_trade':min(e['traded_at'][:10] for e in rows),'last_reported_trade':max(e['traded_at'][:10] for e in rows),
                           'net_return':None,'performance_status':'unverified','copy_return':None,
                           'blocking_reasons':['Only parsed issuer filings are covered; complete actor portfolio and initial cost basis are unknown.',
                                               'Plans, gifts, exercises, tax withholding and amendments require economic reconciliation.',
                                               'Executable post-observation prices, fees, slippage, dividends and splits are required for a follower backtest.']})
        result.sort(key=lambda r:(-r['transactions'],r['actor_id']))
        result=[a for a in result if search.casefold() in ' '.join([a['name'],a['actor_id'],*a['assets']]).casefold()]
        return {'actors':result[offset:offset+limit],'total':len(result),'offset':offset,'limit':limit,
                'scope':'Observed activity only; sorting by record count does not measure profitability.'}

    router.sync_sec = sync
    return router
