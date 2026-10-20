"""Original SEC company-facts receipts and exact annual fact comparisons."""
import gzip
import hashlib
import json
import sqlite3
import threading
from decimal import Decimal, InvalidOperation

from financial_integrity import annual_facts
from research_store import encoded, immutable, now, instant, currently_fresh


def init_financial_sources(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS financial_source_documents(
            id TEXT PRIMARY KEY, cik TEXT NOT NULL, observed_at TEXT NOT NULL,
            source_url TEXT NOT NULL, status_code INTEGER NOT NULL,
            sha256 TEXT NOT NULL, body_gzip BLOB NOT NULL)''')
        immutable(db, 'financial_source_documents')


def save_financial_source(path, cik, response, observed_at=None, source_url=None):
    at = observed_at or now(); body = response.content
    if not isinstance(body, bytes) or len(body) > 32_000_000:
        raise ValueError('SEC response exceeds the bounded original-source archive.')
    sha = hashlib.sha256(body).hexdigest()
    url = source_url or f'https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json'
    identifier = hashlib.sha256(encoded([str(cik).zfill(10), at, response.status_code, sha]).encode()).hexdigest()
    with sqlite3.connect(path, timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO financial_source_documents VALUES(?,?,?,?,?,?,?)',
                   (identifier, str(cik).zfill(10), at, url, response.status_code, sha, gzip.compress(body)))
    return {'id':identifier, 'sha256':sha, 'observed_at':at, 'source_url':url, 'status_code':response.status_code}


class PrimaryProofCache:
    """Bounded process memory for completed, identical source comparisons."""
    def __init__(self, limit=1024):
        self.limit=limit
        self.values={}
        self.lock=threading.Lock()

    def get(self,key):
        with self.lock:
            raw=self.values.get(key)
        return json.loads(raw) if raw is not None else None

    def put(self,key,value):
        raw=encoded(value)
        with self.lock:
            if len(self.values)>=self.limit and key not in self.values:
                self.values.pop(next(iter(self.values)))
            self.values[key]=raw


REBUILD_PROOFS=PrimaryProofCache()


def compare_primary(db, data, identity, at, mapping_cache=None, proof_cache=None):
    # Independent audits omit proof_cache and always reparse originals. A rebuild
    # can reuse a prior comparison only for identical inputs, original
    # compressed bytes, all source metadata and the same filing cutoff day.
    key=None
    if proof_cache is not None:
        try:
            ids=[data['financial_source_receipt']['id'],data['sec_issuer_mapping']['source_receipt']['id']]
            sources=[]
            for identifier in ids:
                row=db.execute('SELECT * FROM financial_source_documents WHERE id=?',(identifier,)).fetchone()
                if not row:raise ValueError('Source missing')
                source=dict(row)
                if not instant(source['observed_at']) or instant(source['observed_at'])>instant(at):
                    raise ValueError('Future source observation')
                body=source.pop('body_gzip')
                sources.append({**source,'compressed_sha256':hashlib.sha256(body).hexdigest()})
            inputs={k:data.get(k) for k in ('ticker','financial_period_end','financial_source_receipt',
                    'financial_provenance','sec_issuer_mapping','revenue','net_income','operating_cf','capex')}
            key=hashlib.sha256(encoded({'method':'primary-fact-proof-v1','cutoff':at[:10],'sources':sources,'inputs':inputs}).encode()).hexdigest()
            cached=proof_cache.get(key)
            if cached is not None:return cached
        except (ValueError,KeyError,TypeError,AttributeError):
            key=None
    result=_compare_primary(db,data,identity,at,mapping_cache)
    if key is not None and result['status'] in ('matched_original_annual_facts','mismatch'):
        proof_cache.put(key,result)
    return result


def _compare_primary(db, data, identity, at, mapping_cache=None):
    """Re-find each USD fact in the original bytes, including period/accession.

    No cached label, URL or hash alone establishes a financial value. A security
    mapping must also corroborate the issuer CIK; company names are insufficient.
    """
    receipt = data.get('financial_source_receipt') or {}
    if not isinstance(receipt, dict):
        return {'status':'mismatch', 'issues':['Financial source receipt metadata is not an object.']}
    row = db.execute('SELECT * FROM financial_source_documents WHERE id=?', (receipt.get('id'),)).fetchone()
    if not row:
        return {'status':'missing_original', 'issues':['No archived original SEC company-facts response.']}
    r = dict(row); issues = []
    try:
        raw = gzip.decompress(r['body_gzip'])
        if not instant(r['observed_at']) or instant(r['observed_at']) > instant(at) or r['source_url'] != f"https://data.sec.gov/api/xbrl/companyfacts/CIK{r['cik']}.json":
            raise ValueError('SEC source URL or observation date differs.')
        if hashlib.sha256(raw).hexdigest() != r['sha256'] or receipt.get('sha256') != r['sha256'] or r['status_code'] != 200:
            raise ValueError('Original SEC response fingerprint/status differs.')
        payload = json.loads(raw)
        issuer = str(payload.get('cik','')).zfill(10)
        mapping = data.get('sec_issuer_mapping') or {}
        mr = db.execute('SELECT * FROM financial_source_documents WHERE id=?', ((mapping.get('source_receipt') or {}).get('id'),)).fetchone()
        if not mr:
            raise ValueError('Original official SEC ticker-to-issuer mapping is missing.')
        mr = dict(mr)
        if not instant(mr['observed_at']) or instant(mr['observed_at']) > instant(at) or (mapping.get('source_receipt') or {}).get('sha256') != mr['sha256'] or mr['status_code'] != 200 or mr['source_url'] != 'https://www.sec.gov/files/company_tickers.json':
            raise ValueError('SEC ticker mapping source differs or was unavailable.')
        # Only a verified directory decode is shared within one rebuild. The
        # compressed-byte digest prevents reuse after even manual DB tampering;
        # issuer, metadata and every company fact are still checked per item.
        cache_key = (mr['id'],mr['sha256'],hashlib.sha256(mr['body_gzip']).hexdigest())
        by_ticker = mapping_cache.get(cache_key) if mapping_cache is not None else None
        if by_ticker is None:
            mapping_bytes = gzip.decompress(mr['body_gzip'])
            if hashlib.sha256(mapping_bytes).hexdigest() != mr['sha256']:
                raise ValueError('SEC ticker mapping source fingerprint differs.')
            by_ticker = {}
            for entry in json.loads(mapping_bytes).values():
                by_ticker.setdefault(entry.get('ticker','').upper(),[]).append(entry)
            if mapping_cache is not None:
                if len(mapping_cache)>=8:
                    mapping_cache.pop(next(iter(mapping_cache)))
                mapping_cache[cache_key] = by_ticker
        ticker = data.get('ticker')
        entries = by_ticker.get(ticker,[])
        if len(entries) != 1 or str(entries[0]['cik_str']).zfill(10) != str(mapping.get('cik')).zfill(10):
            raise ValueError('Ticker-to-issuer mapping does not match the original SEC directory.')
        mapped_cik = str(mapping.get('cik','')).zfill(10)
        if not issuer.isdigit() or issuer != r['cik'] or issuer != mapped_cik:
            raise ValueError('SEC response issuer does not match the recorded SEC ticker mapping.')
        for field in ('revenue','net_income','operating_cf','capex'):
            p = (data.get('financial_provenance') or {}).get(field) or {}
            if not isinstance(p,dict) or not p.get('namespace') or not p.get('concept'):
                issues.append(f'{field}: no supported annual SEC concept provenance. Provider values and broader or continuing-operations concepts require separate primary review.')
                continue
            facts = payload['facts'][p['namespace']][p['concept']]['units']['USD']
            matches = [f for f in annual_facts(facts, True, at[:10]) if all(f.get(k) == p.get(k) for k in ('start','end','filed','accn','form'))]
            value = Decimal(str(data[field])); actual = Decimal(str(p.get('value')))
            if not value.is_finite() or not actual.is_finite() or p.get('unit') != 'USD' or p.get('end') != data.get('financial_period_end'):
                raise ValueError(f'{field}: invalid unit, amount or annual period.')
            if not matches or not any(Decimal(str(f['val'])) == actual for f in matches) or (abs(value) != abs(actual) if field == 'capex' else value != actual):
                raise ValueError(f'{field}: cached value/metadata does not match the original annual SEC fact.')
    except (ValueError, KeyError, TypeError, InvalidOperation, OSError, AttributeError, EOFError) as exc:
        issues.append(str(exc))
    return {'status':'matched_original_annual_facts' if not issues else 'mismatch', 'issues':issues,
            'receipt':{k:r[k] for k in ('id','cik','observed_at','source_url','status_code','sha256')},
            'scope':'Four required annual USD inputs. Other ratios, capital structure and forecasts are not certified.'}


def coverage(db, at, queue_limit=25):
    db.row_factory = sqlite3.Row
    edition = db.execute('SELECT id FROM saved_list_versions ORDER BY id DESC LIMIT 1').fetchone()
    if not edition:
        return {'version':None, 'scope_count':0, 'next_tickers':[], 'maintenance_tickers':[], 'counts':{}, 'queued':0, 'issues':{}, 'original_sec_receipts':{}}
    # The 568-symbol historical-source disclosure approval covers this original
    # portfolio universe. Excluded instruments never enter this expansion queue.
    universe = {r[0] for r in db.execute('SELECT DISTINCT ticker FROM paper_positions')}
    attempts = {r[0]:r[1] for r in db.execute('SELECT ticker,attempted_at FROM research_refresh_attempts')}
    items = [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=?', (edition[0],))]
    scope = [i for i in items if i['ticker'] in universe]
    scope = [{**i, 'status':'needs_evidence'} if i['status']=='documented_inputs' and not currently_fresh(i,at) else i for i in scope]
    counts = {}; reasons = {}
    for i in scope:
        counts[i['status']] = counts.get(i['status'],0)+1
        for reason in i.get('issues',[]):
            reasons[reason] = reasons.get(reason,0)+1
    queue = [i for i in scope if i['status'] == 'needs_evidence' and (i.get('identity') or {}).get('security_type') == 'CS'
             and i.get('instrument_eligibility',{}).get('status') == 'eligible']
    queue.sort(key=lambda i:(attempts.get(i['ticker'],''),i.get('input_observed_at') or '',i['ticker']))
    maintenance = sorted((i for i in scope if i['status']=='documented_inputs'),key=lambda i:(i.get('input_observed_at') or '',i['ticker']))[:25]
    source_counts = dict(db.execute('SELECT status_code,COUNT(*) FROM financial_source_documents GROUP BY status_code'))
    return {'version':edition[0], 'observed_at':at, 'scope_count':len(scope), 'scope':'Original saved portfolio symbols only; eligible common equities.',
            'counts':counts, 'queued':len(queue), 'next_tickers':[i['ticker'] for i in queue[:queue_limit]], 'issues':reasons,
            'maintenance_tickers':[i['ticker'] for i in maintenance],
            'original_sec_receipts':source_counts, 'batch_limit':25,
            'schedule':'Weekdays after close: reviewed starter inputs, up to 25 documented original-portfolio symbols and 10 rotating eligible symbols. Source failures remain dated and unverified.'}
