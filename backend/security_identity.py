"""Exact SEC cover-page share classes, joined to a fresh official listing.

These receipts identify a class. They do not establish financial coverage,
historical continuity, a tradable quote, an insider's returns or profitability.
"""
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import threading
from datetime import timedelta

import requests
from fastapi import APIRouter, HTTPException, Response
from lxml import etree
from pydantic import BaseModel, ConfigDict
from research_store import DIRECTORIES, classify, encoded, identities, immutable, instant, now, parse_directory

METHOD = 'sec-registered-share-class-v1'
MAPPING_URL = 'https://www.sec.gov/files/company_tickers.json'
RESERVE = 256 * 1024 * 1024
LOCK = threading.Lock()
IX = {'http://www.xbrl.org/2013/inlineXBRL', 'http://www.xbrl.org/2008/inlineXBRL'}
XBRLI = 'http://www.xbrl.org/2003/instance'
EXCHANGES = {
    'NYSE': 'NYSE', 'New York Stock Exchange': 'NYSE', 'New York Stock Exchange LLC': 'NYSE',
    'NASDAQ': 'NASDAQ', 'Nasdaq': 'NASDAQ', 'Nasdaq Stock Market': 'NASDAQ',
    'Nasdaq Stock Market LLC': 'NASDAQ', 'Nasdaq Global Select Market': 'NASDAQ',
    'Nasdaq Global Market': 'NASDAQ', 'Nasdaq Capital Market': 'NASDAQ',
    'NYSE American': 'AMEX', 'NYSE American LLC': 'AMEX',
}
LISTING_EXCHANGES = {'N': 'NYSE', 'NASDAQ': 'NASDAQ', 'A': 'AMEX'}
FORMS = {'10-Q': 140, '10-K': 550, '20-F': 550, '40-F': 550}


def sha(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def init_identity_evidence(path):
    with sqlite3.connect(path, timeout=30) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS identity_source_documents (
          id TEXT PRIMARY KEY, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
          status_code INTEGER NOT NULL, sha256 TEXT NOT NULL, body_gzip BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS security_class_reports (
          id TEXT PRIMARY KEY, ticker TEXT NOT NULL, captured_at TEXT NOT NULL,
          input_json TEXT NOT NULL, report_json TEXT NOT NULL, report_sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS security_class_ticker ON security_class_reports(ticker,captured_at DESC);
        ''')
        for table in ('identity_source_documents', 'security_class_reports'):
            immutable(db, table)


def listing_key(record):
    return {key: record.get(key) for key in ('ticker', 'title', 'exchange', 'raw_record', 'source_url')}


def source_bytes(row):
    body = gzip.decompress(row['body_gzip'])
    if len(body) > 12_000_000 or sha(body) != row['sha256']:
        raise ValueError('Original identity source fingerprint differs.')
    return body


def identity_source_bytes(row):
    if row['id'] != sha(encoded([row['source_url'], row['observed_at'], row['status_code'], row['sha256']])):
        raise ValueError('Original identity source metadata differs.')
    return source_bytes(row)


def save_source(path, url, body, status, at):
    identifier = sha(encoded([url, at, status, sha(body)]))
    with sqlite3.connect(path, timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO identity_source_documents VALUES(?,?,?,?,?,?)',
                   (identifier, url, at, status, sha(body), gzip.compress(body)))
    return identifier


def filing_reference(body, cik, ticker, at):
    data = json.loads(body)
    if str(data.get('cik', '')).zfill(10) != cik or ticker not in data.get('tickers', []):
        raise ValueError('SEC submissions do not identify this exact issuer and ticker.')
    recent = data['filings']['recent']
    fields = ('form', 'accessionNumber', 'filingDate', 'primaryDocument', 'reportDate')
    if any(not isinstance(recent.get(k), list) for k in fields) or len({len(recent[k]) for k in fields}) != 1:
        raise ValueError('SEC periodic-filing index arrays differ.')
    candidates = [{k: recent[k][i] for k in fields} for i, f in enumerate(recent['form']) if f.removesuffix('/A') in FORMS]
    if not candidates:
        raise ValueError('No supported periodic filing in the current SEC submissions index.')
    selected = max(candidates, key=lambda r: (r['filingDate'], r['accessionNumber']))
    if selected['form'] not in FORMS:
        raise ValueError('Latest periodic filing is an amendment; a separate review is required.')
    filed, period = instant(selected['filingDate']), instant(selected['reportDate'])
    if not filed or not period or not period <= filed <= instant(at) or not 0 <= (instant(at) - period).total_seconds() / 86400 <= 550 or not 0 <= (instant(at) - filed).total_seconds() / 86400 <= FORMS[selected['form']]:
        raise ValueError('SEC periodic filing date is stale, future or invalid.')
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', selected['accessionNumber']) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:htm|html)', selected['primaryDocument']):
        raise ValueError('SEC filing location is unsupported.')
    selected['url'] = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{selected['accessionNumber'].replace('-', '')}/{selected['primaryDocument']}"
    return selected


def cover_class(body, ticker, cik, filing, listing, exchange_aliases=None):
    if b'<!ENTITY' in body.upper():
        raise ValueError('Entity declarations are unsupported in a filing cover.')
    root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True, recover=False))
    facts = []
    for node in root.iter():
        if not isinstance(node.tag, str) or etree.QName(node).namespace not in IX or etree.QName(node).localname != 'nonNumeric':
            continue
        name = node.get('name', '').split(':')
        if len(name) != 2 or not re.fullmatch(r'https?://xbrl.sec.gov/dei/20\d\d(?:q[1-4])?', node.nsmap.get(name[0], '')):
            continue
        # SEC entity metadata is normally in ix:hidden. Registered-security
        # title/symbol/exchange must instead identify a visible table row.
        hidden = any(etree.QName(p).localname == 'hidden' or p.get('hidden') is not None or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', p.get('style', ''), re.I) for p in (node, *node.iterancestors()))
        if node.get('continuedAt') or (hidden and name[1] not in ('EntityCentralIndexKey', 'DocumentType')):
            continue
        facts.append((name[1], node, ' '.join(''.join(node.itertext()).split())))
    values = lambda name: {text for kind, _, text in facts if kind == name}
    if values('DocumentType') != {filing['form']} or not values('EntityCentralIndexKey') or {v.zfill(10) for v in values('EntityCentralIndexKey')} != {cik}:
        raise ValueError('Filing cover document type or issuer CIK differs.')
    contexts = {}
    for c in root.iter(f'{{{XBRLI}}}context'):
        if not c.get('id') or c.get('id') in contexts:
            raise ValueError('Filing contexts are missing or duplicated.')
        contexts[c.get('id')] = c
    matches = []
    for kind, symbol, text in facts:
        if kind != 'TradingSymbol' or text != ticker:
            continue
        # A single context often covers common shares AND bonds. Join the
        # registered-security table row as well; context alone is insufficient.
        row = next((p for p in symbol.iterancestors() if etree.QName(p).localname == 'tr'), None)
        if row is None:
            continue
        row_facts = [(k, n, v) for k, n, v in facts if row in n.iterancestors()]
        selected = {k: [(n, v) for name, n, v in row_facts if name == k] for k in ('TradingSymbol', 'Security12bTitle', 'SecurityExchangeName')}
        if any(len(v) != 1 for v in selected.values()):
            continue
        if {n.get('contextRef') for v in selected.values() for n, _ in v} != {symbol.get('contextRef')}:
            continue
        context = contexts.get(symbol.get('contextRef'))
        if context is None:
            continue
        identifiers = context.findall(f'{{{XBRLI}}}entity/{{{XBRLI}}}identifier')
        ends = context.findall(f'{{{XBRLI}}}period/{{{XBRLI}}}endDate')
        if len(identifiers) != 1 or identifiers[0].get('scheme') not in ('http://www.sec.gov/CIK', 'https://www.sec.gov/CIK') or (identifiers[0].text or '').zfill(10) != cik or len(ends) != 1 or ends[0].text != filing['reportDate']:
            continue
        title = selected['Security12bTitle'][0][1]
        exchange_name=re.sub(r'^The\s+', '', selected['SecurityExchangeName'][0][1])
        exchange = EXCHANGES.get(exchange_name) or (exchange_aliases or {}).get(exchange_name)
        if exchange is None or exchange != LISTING_EXCHANGES.get(listing['exchange']):
            continue
        matches.append({'title': title, 'exchange': exchange, 'context_ref': symbol.get('contextRef'),
                        'security_type': classify({'Security Name': title, 'Test Issue': 'N', 'ETF': 'N'})})
    unique = {encoded(m) for m in matches}
    if len(unique) != 1:
        raise ValueError('No unique exact ticker/title/exchange/issuer row in the SEC cover.')
    result = json.loads(unique.pop())
    if result['security_type'] != 'CS':
        raise ValueError('SEC registered-security title does not explicitly establish common or ordinary shares.')
    return result


def evaluate(inputs, sources):
    ticker, at, listing = inputs['ticker'], inputs['captured_at'], inputs['directory_identity']
    result = {'methodology': METHOD, 'ticker': ticker, 'captured_at': at,
              'status': 'requires_review', 'source_ids': inputs['source_ids'],
              'limits': 'Registered share class only. No historical symbol continuity, financial values, quotes, trades, returns or profitability are certified.'}
    try:
        if inputs['methodology'] != METHOD or listing.get('ticker') != ticker or classify(listing['raw_record']) != 'unknown' or listing.get('conflict'):
            raise ValueError('Only unambiguous, currently unspecified official listing classes can be supplemented.')
        for field, days in (('retrieved_at', 7), ('source_date', 8)):
            elapsed = (instant(at) - instant(listing[field])).total_seconds() / 86400
            if not 0 <= elapsed <= days:
                raise ValueError('Official listing snapshot is stale or future.')
        original = {}
        for kind in ('mapping', 'submissions', 'filing'):
            source = sources[kind]
            elapsed = (instant(at) - instant(source['observed_at'])).total_seconds()
            if source['status_code'] != 200 or not 0 <= elapsed <= (7 * 86400 if kind == 'mapping' else 600):
                raise ValueError('Original SEC identity response is unsuccessful, stale or future.')
            if source['id'] != inputs['source_ids'][kind]:
                raise ValueError('SEC identity source identifier differs.')
            original[kind] = identity_source_bytes(source)
        if sources['mapping']['source_url'] != MAPPING_URL:
            raise ValueError('SEC mapping URL differs.')
        directory = json.loads(original['mapping'])
        entries = [e for e in directory.values() if e.get('ticker') == ticker]
        if len(entries) != 1 or not re.fullmatch(r'\d{1,10}', str(entries[0]['cik_str'])):
            raise ValueError('SEC directory does not uniquely map this exact ticker to an issuer.')
        cik = str(entries[0]['cik_str']).zfill(10)
        if sources['submissions']['source_url'] != f'https://data.sec.gov/submissions/CIK{cik}.json':
            raise ValueError('SEC submissions URL differs.')
        filing = filing_reference(original['submissions'], cik, ticker, at)
        if sources['filing']['source_url'] != filing['url']:
            raise ValueError('SEC primary filing URL differs.')
        share = cover_class(original['filing'], ticker, cik, filing, listing)
        expiry = min(instant(sources['mapping']['observed_at']) + timedelta(days=7),
                     instant(at) + timedelta(days=7), instant(filing['filingDate']) + timedelta(days=FORMS[filing['form']]))
        result.update(status='matched_common_share_class', cik=cik, share_class=share, filing=filing,
                      valid_until=expiry.isoformat(), reason='Exact SEC registered-security row matches the current official listing symbol and exchange.')
    except (ValueError, TypeError, KeyError, AttributeError, etree.XMLSyntaxError, OSError, EOFError) as failure:
        result['reason'] = str(failure)[:250]
    return result


def replay(db, row):
    inputs, report = json.loads(row['input_json']), json.loads(row['report_json'])
    if sha(row['input_json']) != row['id'] or sha(row['report_json']) != row['report_sha256'] or inputs['ticker'] != row['ticker'] or inputs['captured_at'] != row['captured_at']:
        raise ValueError('Identity report custody differs.')
    identity = inputs['directory_identity']
    revision = db.execute('SELECT * FROM instrument_revisions WHERE id=?', (identity['revision_id'],)).fetchone()
    if not revision or {**json.loads(revision['evidence_json']), 'revision_id': revision['id'], 'retrieved_at': revision['retrieved_at']} != identity or revision['ticker'] != row['ticker']:
        raise ValueError('Original official identity revision differs.')
    batch = db.execute('SELECT documents_json FROM identity_batches WHERE id=?', (revision['batch_id'],)).fetchone()
    if not batch or sha(batch[0]) != revision['batch_id']:
        raise ValueError('Official directory batch fingerprint differs.')
    documents = json.loads(batch[0])['documents']
    directory = {}
    for key, url in DIRECTORIES.items():
        doc = documents[key]
        if doc['url'] != url or sha(doc['text']) != doc['sha256']:
            raise ValueError('Original official directory source differs.')
        parsed = parse_directory(doc['text'], key)
        if identity['ticker'] in parsed:
            candidate = parsed[identity['ticker']]
            if directory and directory != candidate:
                raise ValueError('Official directory symbol is ambiguous.')
            directory = candidate
    base = {k: v for k, v in identity.items() if k not in ('class_evidence', 'revision_id', 'retrieved_at')}
    base['security_type'] = classify(base['raw_record'])
    if base != directory:
        raise ValueError('Identity cannot be reproduced from its original directory bytes.')
    sources = {k: db.execute('SELECT * FROM identity_source_documents WHERE id=?', (identifier,)).fetchone() for k, identifier in inputs['source_ids'].items()}
    if set(sources) != {'mapping', 'submissions', 'filing'} or any(r is None for r in sources.values()):
        raise ValueError('Identity original source is missing.')
    if evaluate(inputs, sources) != report:
        raise ValueError('Identity report cannot be reproduced from its original sources.')
    return {'id': row['id'], 'report_sha256': row['report_sha256'], 'report': report}


def class_evidence_fresh(identity, at):
    evidence = (identity or {}).get('class_evidence')
    if not evidence:
        return True
    start, end, current = instant(evidence.get('captured_at')), instant(evidence.get('valid_until')), instant(at)
    return bool(start and end and current and start <= current < end)


def apply_verified(db, at):
    """Append qualified revisions; never edit existing identities or editions."""
    db.row_factory = sqlite3.Row
    added = []
    for ticker, identity in identities(db).items():
        if classify(identity['raw_record']) != 'unknown' or identity.get('conflict'):
            continue
        row = db.execute('SELECT * FROM security_class_reports WHERE ticker=? ORDER BY captured_at DESC,rowid DESC LIMIT 1', (ticker,)).fetchone()
        if not row:
            continue
        try:
            result = replay(db, row)['report']
            original = json.loads(row['input_json'])['directory_identity']
            if result['status'] != 'matched_common_share_class' or listing_key(original) != listing_key(identity):
                continue
            evidence = {k: result[k] for k in ('captured_at', 'valid_until', 'cik', 'filing', 'share_class')}
            evidence.update(report_id=row['id'], report_sha256=row['report_sha256'], methodology=METHOD)
            if not class_evidence_fresh({'class_evidence': evidence}, at) or identity.get('class_evidence') == evidence:
                continue
            if any(instant(identity.get(k)) is None or not 0 <= (instant(at)-instant(identity[k])).total_seconds()/86400 <= days for k, days in (('retrieved_at', 7), ('source_date', 8))):
                continue
            batch = db.execute('SELECT batch_id FROM instrument_revisions WHERE id=?', (identity['revision_id'],)).fetchone()[0]
            record = {k: v for k, v in identity.items() if k not in ('revision_id', 'retrieved_at', 'class_evidence')}
            record.update(security_type='CS', class_evidence=evidence)
            db.execute('INSERT INTO instrument_revisions(ticker,batch_id,retrieved_at,evidence_json) VALUES(?,?,?,?)',
                       (ticker, batch, identity['retrieved_at'], encoded(record)))
            added.append(ticker)
        except (ValueError, OSError, EOFError):
            continue  # Original listing remains unspecified; no inferred class.
    return added


def download(path, client, url, headers):
    with client.get(url, headers=headers, timeout=(5, 30), stream=True, allow_redirects=False) as response:
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 12_000_000:
                raise ValueError('SEC identity response exceeds the source size bound.')
            chunks.append(chunk)
        identifier = save_source(path, url, b''.join(chunks), response.status_code, now())
        if response.status_code != 200:
            raise ValueError(f'SEC identity source returned HTTP {response.status_code}; remaining requests stopped.')
        return identifier


def collect(path, ticker, headers, client=requests):
    if not re.fullmatch(r'[A-Z0-9.-]{1,12}', ticker):
        raise ValueError('Unsupported exact ticker.')
    if not headers.get('User-Agent'):
        raise ValueError('Configured SEC contact header is required.')
    if not LOCK.acquire(blocking=False):
        raise ValueError('Another bounded identity collection is running.')
    try:
        if shutil.disk_usage(str(path)).free < RESERVE + 36_000_000:
            raise ValueError('Identity source storage reserve reached.')
        with sqlite3.connect(path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            denied = db.execute('SELECT observed_at FROM identity_source_documents WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
            if denied and 0 <= (instant(now())-instant(denied[0])).total_seconds() < 900:
                raise ValueError('SEC identity requests paused after a recent access denial.')
            denied = db.execute('SELECT observed_at FROM financial_source_documents WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
            if denied and 0 <= (instant(now())-instant(denied[0])).total_seconds() < 900:
                raise ValueError('SEC identity requests paused after a recent financial-source access denial.')
            latest = db.execute('SELECT * FROM security_class_reports WHERE ticker=? ORDER BY captured_at DESC,rowid DESC LIMIT 1', (ticker,)).fetchone()
            identity = identities(db).get(ticker)
            if not identity or identity.get('conflict') or classify(identity['raw_record']) != 'unknown':
                raise ValueError('An exact official listing with an unspecified class is required.')
            if any(instant(identity.get(k)) is None or not 0 <= (instant(now())-instant(identity[k])).total_seconds()/86400 <= days for k, days in (('retrieved_at', 7), ('source_date', 8))):
                raise ValueError('A fresh official listing snapshot is required before SEC requests.')
            if latest and 0 <= (instant(now())-instant(latest['captured_at'])).total_seconds() < 3600 and listing_key(json.loads(latest['input_json'])['directory_identity']) == listing_key(identity):
                return replay(db, latest)
            mapping = db.execute('SELECT * FROM financial_source_documents WHERE source_url=? AND status_code=200 ORDER BY observed_at DESC LIMIT 1', (MAPPING_URL,)).fetchone()
            cached = bool(mapping and 0 <= (instant(now())-instant(mapping['observed_at'])).total_seconds() < 2*86400)
            mapping_body = source_bytes(mapping) if cached else None
        source_ids = {}
        source_ids['mapping'] = save_source(path, MAPPING_URL, mapping_body, 200, mapping['observed_at']) if cached else download(path, client, MAPPING_URL, headers)
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            directory = json.loads(source_bytes(db.execute('SELECT * FROM identity_source_documents WHERE id=?', (source_ids['mapping'],)).fetchone()))
        entries = [e for e in directory.values() if e.get('ticker') == ticker]
        if len(entries) != 1 or not re.fullmatch(r'\d{1,10}', str(entries[0]['cik_str'])):
            raise ValueError('SEC directory has no unique exact ticker/issuer match.')
        cik = str(entries[0]['cik_str']).zfill(10)
        source_ids['submissions'] = download(path, client, f'https://data.sec.gov/submissions/CIK{cik}.json', headers)
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            body = source_bytes(db.execute('SELECT * FROM identity_source_documents WHERE id=?', (source_ids['submissions'],)).fetchone())
        filing = filing_reference(body, cik, ticker, now())
        source_ids['filing'] = download(path, client, filing['url'], headers)
        inputs = {'methodology': METHOD, 'ticker': ticker, 'captured_at': now(), 'directory_identity': identity, 'source_ids': source_ids}
        raw = encoded(inputs)
        with sqlite3.connect(path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            sources = {k: db.execute('SELECT * FROM identity_source_documents WHERE id=?', (v,)).fetchone() for k, v in source_ids.items()}
            report_raw = encoded(evaluate(inputs, sources))
            db.execute('INSERT INTO security_class_reports VALUES(?,?,?,?,?,?)', (sha(raw), ticker, inputs['captured_at'], raw, report_raw, sha(report_raw)))
            return replay(db, db.execute('SELECT * FROM security_class_reports WHERE id=?', (sha(raw),)).fetchone())
    finally:
        LOCK.release()


def collect_due(path, headers, limit=5):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        originals = {r[0] for r in db.execute('SELECT DISTINCT ticker FROM paper_positions')}
        queue = []
        for ticker, identity in identities(db).items():
            if ticker not in originals or identity.get('conflict') or classify(identity['raw_record']) != 'unknown':
                continue
            last = db.execute('SELECT captured_at FROM security_class_reports WHERE ticker=? ORDER BY captured_at DESC LIMIT 1', (ticker,)).fetchone()
            if not last or (instant(now())-instant(last[0])).total_seconds() >= 3*86400:
                queue.append((last[0] if last else '', ticker))
    results = []
    for _, ticker in sorted(queue)[:min(5, max(0, limit))]:
        try:
            report = collect(path, ticker, headers)
            results.append({'ticker': ticker, 'status': report['report']['status']})
        except (ValueError, requests.RequestException, OSError):
            break  # Never bypass a denial or advance through a failing source.
    with sqlite3.connect(path) as db:
        apply_verified(db, now())
    return results


def make_identity_router(path, headers):
    router = APIRouter(prefix='/api/security-identity', tags=['Registered security classes'])

    @router.get('/reports/{identifier}')
    def report(identifier: str):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM security_class_reports WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Security class report not found.')
            try:
                return replay(db, row)
            except (ValueError, OSError, EOFError):
                raise HTTPException(409, 'Security class report or original source differs.')

    @router.get('/sources/{identifier}')
    def source(identifier: str):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM identity_source_documents WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Identity source not found.')
            try:
                body = identity_source_bytes(row)
            except (ValueError, OSError, EOFError):
                raise HTTPException(409, 'Identity original source differs.')
            # Download HTML as inert text; do not render third-party scripts.
            return Response(body, media_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename="identity-source.txt"', 'X-Content-SHA256': row['sha256']})

    class Empty(BaseModel):
        model_config = ConfigDict(extra='forbid')

    @router.post('/{ticker}/collect')
    def capture(ticker: str, body: Empty):
        try:
            return collect(path, ticker.upper(), headers())
        except (ValueError, requests.RequestException, OSError):
            raise HTTPException(502, 'Identity source collection stopped; previous dated evidence retained.')

    return router
