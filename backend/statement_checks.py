"""Original annual cash-flow statement cross-checks; no capex relabeling."""
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import zlib
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from fastapi import APIRouter, HTTPException, Query, Response
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field
from financial_context import financial_context, receipt
from research_store import encoded, immutable, instant, now
from security_identity import IX, XBRLI, cover_class

METHOD = 'annual-filed-statement-cross-checks-v1'
RESERVE = 256 * 1024 * 1024
LOCK = threading.Lock()
DEFINITIONS = {
    'total_operating_cf': ('NetCashProvidedByUsedInOperatingActivities', 'Total operating cash flow'),
    'continuing_operating_cf': ('NetCashProvidedByUsedInOperatingActivitiesContinuingOperations', 'Continuing operating cash flow'),
    'discontinued_operating_cf': ('CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations', 'Discontinued operating cash flow'),
    'ppe_payments': ('PaymentsToAcquirePropertyPlantAndEquipment', 'PP&E payments'),
    'productive_asset_payments': ('PaymentsToAcquireProductiveAssets', 'Productive-asset payments'),
    'other_productive_asset_payments': ('PaymentsToAcquireOtherProductiveAssets', 'Other productive-asset payments'),
    'oil_gas_payments': ('PaymentsToAcquireOilAndGasPropertyAndEquipment', 'Oil/gas property and equipment payments'),
    'software_payments': ('PaymentsToAcquireSoftware', 'Software payments'),
    'intangible_payments': ('PaymentsToAcquireIntangibleAssets', 'Intangible-asset payments'),
    'acquisition_payments_net': ('PaymentsToAcquireBusinessesNetOfCashAcquired', 'Business acquisitions, net of cash acquired'),
    'acquisition_payments_gross': ('PaymentsToAcquireBusinessesGross', 'Gross business acquisitions'),
}
MEASURES = {
    'cash_after_ppe_payments': ('ppe_payments', 'Total OCF less PP&E payments'),
    'cash_after_productive_asset_payments': ('productive_asset_payments', 'Total OCF less productive-asset payments'),
    'cash_after_other_productive_asset_payments': ('other_productive_asset_payments', 'Total OCF less other productive-asset payments'),
    'cash_after_oil_gas_payments': ('oil_gas_payments', 'Total OCF less oil/gas property and equipment payments'),
}
TRANSFORMS = {'http://www.xbrl.org/inlineXBRL/transformation/2020-02-12': {'num-dot-decimal', 'fixed-zero'},
              'http://www.xbrl.org/inlineXBRL/transformation/2015-02-26': {'numdotdecimal', 'zerodash'},
              'http://www.xbrl.org/inlineXBRL/transformation/2011-07-31': {'numdotdecimal', 'numcommadot', 'zerodash'}}
LIMITS = [
    'Company-facts JSON and the filed statement are two representations of issuer-reported data, not independent upstream audits. Later corrections or restatements can change the numbers.',
    'Only visible, unsegmented annual USD facts in one identified cash-flow table are supported. Unsupported transformations, ambiguous tables or contexts and missing facts remain unverified.',
    'The issuer row label and taxonomy concept are both retained. Productive assets, other productive assets, software, acquisitions and PP&E have different scopes and are never silently combined or relabeled.',
    'Subtractions use reported total operating cash flow and one stated payment category in the same filing and annual period. They are not issuer-adjusted free cash flow, cash available to shareholders, forecasts or investment returns.',
    'No legacy capex, FCF, ranking, saved-list eligibility, copy policy, frozen trial, order or account return is changed.',
]


def sha(raw): return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()
def text(node): return ' '.join(''.join(node.itertext()).split())
def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('An explicit source timezone is required.')
    return result.astimezone(timezone.utc)
def decimal(value):
    if isinstance(value, bool): raise ValueError('Boolean monetary value.')
    result = Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal('1e18'): raise ValueError('Invalid monetary value.')
    return result


def init_statements(path):
    with sqlite3.connect(path, timeout=30) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS statement_source_documents (
          id TEXT PRIMARY KEY, cik TEXT NOT NULL, source_url TEXT NOT NULL,
          observed_at TEXT NOT NULL, status_code INTEGER NOT NULL,
          sha256 TEXT NOT NULL, body_gzip BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS statement_check_reports (
          id TEXT PRIMARY KEY, ticker TEXT NOT NULL, research_version INTEGER NOT NULL,
          captured_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
          input_json TEXT NOT NULL, report_json TEXT NOT NULL, report_sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS statement_check_ticker ON statement_check_reports(ticker,research_version,captured_at DESC);
        ''')
        for table in ('statement_source_documents', 'statement_check_reports'): immutable(db, table)


def save_source(path, cik, url, body, status, at):
    if not isinstance(body, bytes) or len(body) > 32_000_000: raise ValueError('Statement source exceeds 32 MB.')
    identifier = sha(encoded([cik, url, at, status, sha(body)]))
    with sqlite3.connect(path, timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO statement_source_documents VALUES(?,?,?,?,?,?,?)',
                   (identifier, cik, url, at, status, sha(body), gzip.compress(body)))
    return identifier


def original(row):
    if row['id'] != sha(encoded([row['cik'], row['source_url'], row['observed_at'], row['status_code'], row['sha256']])):
        raise ValueError('Statement source metadata differs.')
    try: body = gzip.decompress(row['body_gzip'])
    except (OSError, EOFError, zlib.error): raise ValueError('Statement source compression differs.') from None
    if len(body) > 32_000_000 or sha(body) != row['sha256']: raise ValueError('Statement source fingerprint differs.')
    return body


def numeric(node):
    if node.get('continuedAt') or node.get('target') or node.get('tupleRef') or node.get('precision') or node.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true', '1'):
        raise ValueError('Unsupported numeric context, continuation or nil fact.')
    if any(isinstance(e.tag, str) and etree.QName(e).namespace in IX for e in node.iterdescendants()):
        raise ValueError('Nested inline numeric facts require separate review.')
    raw = ''.join(node.itertext()).strip()
    fmt = node.get('format')
    transform = None
    if fmt:
        parts = fmt.split(':')
        if len(parts) != 2 or parts[1] not in TRANSFORMS.get(node.nsmap.get(parts[0]), set()):
            raise ValueError('Unsupported numeric transformation.')
        transform = parts[1]
    if transform in ('fixed-zero', 'zerodash'):
        if raw not in ('', '-', '—', '–', '0', '0.0'): raise ValueError('Unexpected fixed-zero rendering.')
        value = Decimal(0)
    else:
        if transform:
            if not re.fullmatch(r'\d[\d,\s\u00a0]*(?:\.[\d\s\u00a0]+)?', raw): raise ValueError('Unsupported dot-decimal rendering.')
            raw = re.sub(r'[,\s\u00a0]', '', raw)
        elif not re.fullmatch(r'\d+(?:\.\d+)?', raw):
            raise ValueError('An untransformed fact is not a plain nonnegative decimal.')
        value = decimal(raw)
    scale, precision = node.get('scale', '0'), node.get('decimals', '')
    if not re.fullmatch(r'-?\d{1,2}', scale) or not -12 <= int(scale) <= 12:
        raise ValueError('Unsupported numeric scale.')
    if precision != 'INF' and (not re.fullmatch(r'-?\d{1,2}', precision) or not -18 <= int(precision) <= 18):
        raise ValueError('Unsupported reported decimal precision.')
    if node.get('sign') not in (None, '-'): raise ValueError('Unsupported numeric sign.')
    value = decimal(value * Decimal(10) ** int(scale) * (-1 if node.get('sign') == '-' else 1))
    return {'value': format(value, 'f'), 'rendered_number': ''.join(node.itertext()).strip(),
            'scale': int(scale), 'sign': node.get('sign'), 'decimals': precision,
            'reported_rounding_unit_usd': None if precision == 'INF' else format(Decimal(10) ** -int(precision), 'f'),
            'format': fmt, 'fact_id': node.get('id')}


def hidden(node):
    return any(etree.QName(p).localname == 'hidden' or p.get('hidden') is not None or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', p.get('style', ''), re.I) for p in (node, *node.iterancestors()))


def parse_statement(body, ticker, cik, filing, listing):
    if len(body) > 32_000_000 or b'<!ENTITY' in body.upper(): raise ValueError('Unsafe or oversized statement.')
    cover_class(body, ticker, cik, filing, listing)
    root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True, recover=False))
    contexts, units, ids = {}, set(), set()
    for node in root.iter():
        if not isinstance(node.tag, str): continue
        name = etree.QName(node)
        if name.namespace == XBRLI and name.localname in ('context', 'unit'):
            identifier = node.get('id')
            if not identifier or identifier in ids: raise ValueError('Duplicate or missing XBRL context/unit identifier.')
            ids.add(identifier)
        if name == etree.QName(XBRLI, 'context'):
            entity = node.findall(f'{{{XBRLI}}}entity/{{{XBRLI}}}identifier')
            start = node.find(f'{{{XBRLI}}}period/{{{XBRLI}}}startDate'); end = node.find(f'{{{XBRLI}}}period/{{{XBRLI}}}endDate')
            if len(entity) != 1 or entity[0].get('scheme') not in ('http://www.sec.gov/CIK', 'https://www.sec.gov/CIK') or (entity[0].text or '').zfill(10) != cik or start is None or end is None:
                continue
            if node.find(f'{{{XBRLI}}}entity/{{{XBRLI}}}segment') is not None or node.find(f'{{{XBRLI}}}scenario') is not None:
                continue
            try:
                days = (date.fromisoformat(end.text) - date.fromisoformat(start.text)).days + 1
                if 335 <= days <= 395 and end.text <= filing['reportDate']: contexts[node.get('id')] = {'start': start.text, 'end': end.text}
            except (TypeError, ValueError): continue
        if name == etree.QName(XBRLI, 'unit'):
            measures = node.findall(f'{{{XBRLI}}}measure')
            if len(node) == 1 and len(measures) == 1 and measures[0].text:
                parts = measures[0].text.split(':')
                if len(parts) == 2 and parts[1] == 'USD' and measures[0].nsmap.get(parts[0]) == 'http://www.xbrl.org/2003/iso4217': units.add(node.get('id'))
    facts, tables = [], {}
    for node in root.iter():
        if not isinstance(node.tag, str) or etree.QName(node).namespace not in IX or etree.QName(node).localname != 'nonFraction' or hidden(node): continue
        name = node.get('name', '').split(':')
        if len(name) != 2 or not re.fullmatch(r'https?://fasb.org/us-gaap/20\d\d', node.nsmap.get(name[0], '')): continue
        table = next((p for p in node.iterancestors() if etree.QName(p).localname == 'table'), None)
        if table is None: continue
        tables.setdefault(table, set()).add(name[1])
        if node.get('contextRef') not in contexts or node.get('unitRef') not in units: continue
        row = next((p for p in node.iterancestors() if etree.QName(p).localname == 'tr'), None)
        if row is None: continue
        cells = [p for p in row if isinstance(p.tag, str) and etree.QName(p).localname in ('td', 'th')]
        labels = [text(p) for p in cells if not any(e.get('name') for e in p.iter()) and re.search(r'[A-Za-z]', text(p))]
        label = labels[0] if labels else None
        fact = {'concept': name[1], 'namespace': 'us-gaap', 'context_ref': node.get('contextRef'),
                **contexts[node.get('contextRef')], 'row_label': label[:300] if label else None}
        try: fact.update(numeric(node), parse_status='parsed')
        except (ValueError, InvalidOperation): fact.update(parse_status='requires_review', value=None)
        if not label: fact.update(parse_status='requires_review', value=None)
        facts.append((table, fact))
    required = {'NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInInvestingActivities', 'NetCashProvidedByUsedInFinancingActivities'}
    candidates = [table for table, concepts in tables.items() if required <= concepts and any(re.fullmatch(r'Cash.*PeriodIncreaseDecrease.*', c) for c in concepts)]
    if len(candidates) != 1: raise ValueError('No unique supported cash-flow statement table.')
    return [fact for table, fact in facts if table is candidates[0]]


def filing_reference(index, cik, ticker, accession, end):
    if str(index.get('cik', '')).zfill(10) != cik or ticker not in index.get('tickers', []): raise ValueError('Original SEC submissions issuer or exact ticker differs.')
    recent = index['filings']['recent']; fields = ('form', 'accessionNumber', 'filingDate', 'primaryDocument', 'reportDate')
    if any(not isinstance(recent.get(k), list) for k in fields) or len({len(recent[k]) for k in fields}) != 1: raise ValueError('Original SEC submissions arrays differ.')
    rows = [{k: recent[k][i] for k in fields} for i, a in enumerate(recent['accessionNumber']) if a == accession]
    if len(rows) != 1: raise ValueError('Exact annual accession is unavailable in the archived submissions index.')
    row = rows[0]
    if row['form'] not in ('10-K', '20-F', '40-F') or row['reportDate'] != end or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:htm|html)', row['primaryDocument']): raise ValueError('Unsupported exact annual filing identity.')
    if not instant(row['filingDate']) or row['filingDate'] < end: raise ValueError('Invalid annual filing date.')
    return {**row, 'url': f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{row['primaryDocument']}"}


def comparison(candidates, payload, concept, period, filing):
    matches = [f for f in candidates if f['concept'] == concept and f['end'] == period]
    result = {'concept': concept, 'value': None, 'status': 'unavailable', 'reason': 'No supported visible annual USD fact in the filed cash-flow table.'}
    if not matches: return result
    if any(f['parse_status'] != 'parsed' for f in matches) or len({(f['start'], f['value'], f['row_label']) for f in matches}) != 1:
        return {**result, 'status': 'requires_review', 'reason': 'Unsupported or conflicting filed statement values, contexts or row labels.'}
    chosen = matches[0]
    result.update(chosen, status='filed_statement_only', reason='Original statement parsed; no exact original company-facts match.')
    raw = payload.get('facts', {}).get('us-gaap', {}).get(concept, {}).get('units', {}).get('USD', [])
    entries = [f for f in raw if all(f.get(k) == value for k, value in {'start':chosen['start'], 'end':period, 'accn':filing['accessionNumber'], 'filed':filing['filingDate'], 'form':filing['form']}.items())]
    values = {format(decimal(f['val']), 'f') for f in entries}
    if len(values) > 1 or (values and decimal(chosen['value']) != decimal(next(iter(values)))):
        return {**result, 'status': 'source_mismatch', 'reason': 'Filed statement and original company-facts values differ.', 'company_facts_values': sorted(values)}
    if values: result.update(status='matched', reason='Exact amount, annual period, USD, accession, filing date and form match both originals.')
    if concept.startswith('Payments') and decimal(chosen['value']) < 0:
        result.update(status='requires_review', reason='Negative payment fact requires separate review; it is not converted to an absolute value.')
    return result


def build(item, version, context, payload, filing_body, filing, sources, at):
    result = {'methodology': METHOD, 'ticker': item['ticker'], 'research_version': version,
              'captured_at': at, 'primary_context_sha256': context['content_sha256'],
              'saved_item_sha256': sha(encoded(item)), 'status': 'requires_review', 'filing': filing,
              'sources': sources, 'periods': [], 'issues': [], 'limitations': LIMITS,
              'legacy_capex_replaced': False, 'selection_policy_unchanged': True,
              'account_return_verified': False, 'profitability_verified': False}
    try:
        parsed = parse_statement(filing_body, item['ticker'], str(payload['cik']).zfill(10), filing, item['identity'])
        for period in context['periods']:
            if not period['end']: continue
            facts = {key: {**comparison(parsed, payload, concept, period['end'], filing), 'label': label} for key, (concept, label) in DEFINITIONS.items()}
            operating = facts['total_operating_cf']; reconciliation = 'unavailable'
            components = [facts[k] for k in ('total_operating_cf', 'continuing_operating_cf', 'discontinued_operating_cf')]
            if all(f['status'] == 'matched' for f in components) and len({f['start'] for f in components}) == 1:
                reconciliation = 'matched' if decimal(components[0]['value']) == decimal(components[1]['value']) + decimal(components[2]['value']) else 'source_mismatch'
            measures = {}
            for key, (spending_key, label) in MEASURES.items():
                spending = facts[spending_key]
                valid = reconciliation != 'source_mismatch' and all(f['status'] == 'matched' for f in (operating, spending)) and operating['start'] == spending['start']
                measures[key] = {'label': label, 'status': 'calculated_from_matched_originals' if valid else 'unavailable',
                                 'value': format(decimal(operating['value']) - decimal(spending['value']), 'f') if valid else None,
                                 'operating_fact': 'total_operating_cf', 'spending_fact': spending_key,
                                 'reason': 'Same annual period and filing; total OCF minus one nonnegative reported payment category.' if valid else 'Both scopes need matching original facts and coherent total operating cash flow.'}
            result['periods'].append({'end': period['end'], 'facts': facts, 'operating_component_reconciliation': reconciliation, 'cash_measures': measures})
        result['status'] = 'statement_cross_checks_available' if any(f['status'] == 'matched' for p in result['periods'] for f in p['facts'].values()) else 'no_matched_statement_facts'
    except (ValueError, KeyError, TypeError, InvalidOperation, etree.XMLSyntaxError) as error:
        result['issues'].append(str(error)[:250])
    result['coverage'] = {'matched_original_facts': sum(f['status'] == 'matched' for p in result['periods'] for f in p['facts'].values()),
                          'source_mismatches': sum(f['status'] == 'source_mismatch' for p in result['periods'] for f in p['facts'].values()),
                          'available_cash_measures': sum(m['value'] is not None for p in result['periods'] for m in p['cash_measures'].values())}
    return result


def scope(db, ticker, version):
    row = db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?', (version, ticker)).fetchone()
    if not row: raise ValueError('Saved research item is unavailable.')
    item = json.loads(row[0]); context = financial_context(db, item, version)
    if context['status'] != 'source_context_available': raise ValueError('Verified original financial context is required.')
    operating = context['periods'][0]['annual_fields']['operating_cf']
    if operating['status'] != 'source_matched': raise ValueError('An original annual operating cash-flow fact is required.')
    inputs = {'methodology': METHOD, 'ticker': ticker, 'research_version': version,
              'primary_context_sha256': context['content_sha256'], 'saved_item_sha256': sha(encoded(item)),
              'cik': context['sources'][0]['cik'], 'accession': operating['accn'], 'period_end': context['periods'][0]['end']}
    return item, context, inputs


def replay(db, row):
    if sha(row['input_json']) != row['id'] or sha(row['report_json']) != row['report_sha256']: raise ValueError('Statement report fingerprint differs.')
    inputs = json.loads(row['input_json'])
    if any(inputs[k] != row[k] for k in ('ticker', 'research_version', 'captured_at', 'request_id')): raise ValueError('Statement report identity differs.')
    item, context, expected = scope(db, row['ticker'], row['research_version'])
    if any(inputs.get(k) != value for k, value in expected.items()): raise ValueError('Original saved research scope differs.')
    if inputs.get('scope_sha256') != sha(encoded(expected)) or set(inputs.get('source_ids', {})) != {'submissions','filing'}: raise ValueError('Original statement capture scope differs.')
    if aware(inputs['captured_at']) < aware(item['rebuilt_at']): raise ValueError('Statement capture predates its saved research inputs.')
    refs, bodies = [], {}
    for kind in ('submissions', 'filing'):
        r = db.execute('SELECT * FROM statement_source_documents WHERE id=?', (inputs['source_ids'][kind],)).fetchone()
        if not r or r['cik'] != inputs['cik'] or r['status_code'] != 200 or not aware(r['observed_at']) <= aware(inputs['captured_at']): raise ValueError('Original statement response is unavailable or future-dated.')
        bodies[kind] = original(r); refs.append({k: r[k] for k in ('id', 'source_url', 'observed_at', 'status_code', 'sha256')})
    if refs[0]['source_url'] != f"https://data.sec.gov/submissions/CIK{inputs['cik']}.json": raise ValueError('Original submissions URL differs.')
    filing = filing_reference(json.loads(bodies['submissions']), inputs['cik'], inputs['ticker'], inputs['accession'], inputs['period_end'])
    if refs[1]['source_url'] != filing['url'] or filing['filingDate'] > inputs['captured_at'][:10]: raise ValueError('Original annual filing URL or date differs.')
    source = context['sources'][0]
    _, payload = receipt(db, db.execute('SELECT * FROM financial_source_documents WHERE id=?', (source['id'],)).fetchone(), item['rebuilt_at'])
    result = build(item, row['research_version'], context, payload, bodies['filing'], filing, refs, row['captured_at'])
    if encoded(result) != row['report_json']: raise ValueError('Statement report cannot be reproduced from its original sources.')
    return {'id': row['id'], 'report_sha256': row['report_sha256'], 'report': result}


def download(path, cik, url, headers, client):
    with client.get(url, headers=headers, timeout=(5, 30), stream=True, allow_redirects=False) as response:
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 32_000_000: raise ValueError('Statement response exceeds 32 MB.')
            chunks.append(chunk)
        identifier = save_source(path, cik, url, b''.join(chunks), response.status_code, now())
        if response.status_code != 200: raise ValueError('SEC statement source denied or unavailable; remaining requests stopped.')
        return identifier


class Capture(BaseModel):
    model_config = ConfigDict(extra='forbid')
    research_version: int = Field(ge=1)
    scope_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')


def collect(path, ticker, request, headers, client=requests):
    if not headers.get('User-Agent'): raise ValueError('Configured SEC contact header is required.')
    if not LOCK.acquire(blocking=False): raise ValueError('Another bounded statement collection is running.')
    try:
        if shutil.disk_usage(str(path)).free < RESERVE + 68_000_000: raise ValueError('Statement source storage reserve reached.')
        with sqlite3.connect(path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            existing = db.execute('SELECT * FROM statement_check_reports WHERE request_id=?', (request.request_id,)).fetchone()
            if existing:
                inputs = json.loads(existing['input_json'])
                if existing['ticker'] != ticker or existing['research_version'] != request.research_version or inputs['scope_sha256'] != request.scope_sha256: raise HTTPException(409, 'Request identifier belongs to different statement evidence.')
                return replay(db, existing)
            item, context, inputs = scope(db, ticker, request.research_version)
            if aware(now()) < aware(item['rebuilt_at']): raise ValueError('Statement capture cannot predate its saved research inputs.')
            expected_scope = sha(encoded(inputs))
            if request.research_version != db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0] or request.scope_sha256 != expected_scope: raise HTTPException(409, 'Research inputs changed; reload before collecting.')
            for table in ('statement_source_documents', 'financial_source_documents', 'identity_source_documents'):
                denied = db.execute(f'SELECT observed_at FROM {table} WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
                if denied and 0 <= (instant(now())-instant(denied[0])).total_seconds() < 900: raise ValueError('SEC statement requests paused after a recent access denial.')
            sources = {}
            cik = inputs['cik']; url = f'https://data.sec.gov/submissions/CIK{cik}.json'
            cached = db.execute('SELECT * FROM statement_source_documents WHERE source_url=? AND status_code=200 ORDER BY observed_at DESC LIMIT 1', (url,)).fetchone()
            if cached and 0 <= (instant(now())-instant(cached['observed_at'])).total_seconds() <= 7*86400:
                original(cached); sources['submissions'] = cached['id']
        if 'submissions' not in sources: sources['submissions'] = download(path, cik, url, headers, client)
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            index = json.loads(original(db.execute('SELECT * FROM statement_source_documents WHERE id=?', (sources['submissions'],)).fetchone()))
            filing = filing_reference(index, cik, ticker, inputs['accession'], inputs['period_end'])
            cached = db.execute('SELECT * FROM statement_source_documents WHERE source_url=? AND cik=? AND status_code=200 ORDER BY observed_at LIMIT 1', (filing['url'], cik)).fetchone()
            if cached: original(cached); sources['filing'] = cached['id']
        if 'filing' not in sources: sources['filing'] = download(path, cik, filing['url'], headers, client)
        inputs.update(source_ids=sources, captured_at=now(), request_id=request.request_id, scope_sha256=expected_scope)
        with sqlite3.connect(path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            current_scope = scope(db,ticker,request.research_version)[2]
            if request.research_version != db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0] or current_scope != {k:inputs[k] for k in current_scope}: raise HTTPException(409, 'Research inputs changed during collection; originals retained without publishing a report.')
            refs, bodies = [], {}
            for kind in ('submissions', 'filing'):
                r = db.execute('SELECT * FROM statement_source_documents WHERE id=?', (sources[kind],)).fetchone(); bodies[kind] = original(r)
                refs.append({k:r[k] for k in ('id','source_url','observed_at','status_code','sha256')})
            source = context['sources'][0]
            _, payload = receipt(db,db.execute('SELECT * FROM financial_source_documents WHERE id=?',(source['id'],)).fetchone(),item['rebuilt_at'])
            report = build(item,request.research_version,context,payload,bodies['filing'],filing,refs,inputs['captured_at'])
            raw, report_raw = encoded(inputs), encoded(report)
            db.execute('INSERT INTO statement_check_reports VALUES(?,?,?,?,?,?,?,?)',(sha(raw),ticker,request.research_version,inputs['captured_at'],request.request_id,raw,report_raw,sha(report_raw)))
            return replay(db,db.execute('SELECT * FROM statement_check_reports WHERE id=?',(sha(raw),)).fetchone())
    finally: LOCK.release()


def make_statement_router(path, headers):
    router = APIRouter(prefix='/api/statement-checks', tags=['Original annual statement checks'])

    @router.get('/reports/{identifier}')
    def saved(identifier: str):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row; row = db.execute('SELECT * FROM statement_check_reports WHERE id=?',(identifier,)).fetchone()
            if not row: raise HTTPException(404, 'Statement check report not found.')
            try: return replay(db,row)
            except (ValueError, KeyError, OSError, EOFError): raise HTTPException(409, 'Statement report or original sources differ.')

    @router.get('/sources/{identifier}')
    def source(identifier: str):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row; row = db.execute('SELECT * FROM statement_source_documents WHERE id=?',(identifier,)).fetchone()
            if not row: raise HTTPException(404, 'Statement source not found.')
            try: body = original(row)
            except (ValueError, OSError, EOFError): raise HTTPException(409, 'Statement original source differs.')
            return Response(body,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="original-sec-statement.txt"','X-Content-SHA256':row['sha256']})

    @router.get('/{ticker}')
    def inspect(ticker: str, version: int | None = Query(None,ge=1)):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row; v = version or db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
            row = db.execute('SELECT * FROM statement_check_reports WHERE ticker=? AND research_version=? ORDER BY captured_at DESC LIMIT 1',(ticker.upper(),v)).fetchone()
            if row:
                try: return replay(db,row)
                except (ValueError,KeyError,OSError,EOFError): raise HTTPException(409,'Statement report or original source differs.')
            try:
                _, _, inputs = scope(db,ticker.upper(),v)
                older = db.execute('SELECT * FROM statement_check_reports WHERE ticker=? AND research_version<? ORDER BY research_version DESC,captured_at DESC LIMIT 1',(ticker.upper(),v)).fetchone()
                if older:
                    try:
                        return {**replay(db,older),'historical_report':True,'requested_research_version':v,'current_scope_sha256':sha(encoded(inputs))}
                    except (ValueError,KeyError,OSError,EOFError): raise HTTPException(409,'Archived statement report or original source differs.')
                return {'status':'not_collected','ticker':ticker.upper(),'research_version':v,'scope_sha256':sha(encoded(inputs)),'limitations':LIMITS}
            except (ValueError,KeyError):
                return {'status':'unavailable','ticker':ticker.upper(),'research_version':v,'reason':'Verified original annual financial context is required; no numbers have been guessed.','limitations':LIMITS}

    @router.post('/{ticker}/capture')
    def capture(ticker: str, request: Capture):
        try: return collect(path,ticker.upper(),request,headers())
        except HTTPException: raise
        except (ValueError,KeyError,requests.RequestException,OSError,EOFError): raise HTTPException(502,'Statement collection stopped; previous dated evidence retained.')

    return router
