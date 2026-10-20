"""Replayed, bounded cross-provider price observations; never execution proof."""
import hashlib
import json
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from market_provenance import calendar_check, init_sources, save_source
from research_store import encoded, identities, immutable, now

METHOD = 'cross-provider-regular-quotes-v1'
NY = ZoneInfo('America/New_York')
POLICY = {'maximum_quote_age_seconds': 1200, 'maximum_time_difference_seconds': 60,
          'future_clock_allowance_seconds': 5, 'absolute_price_tolerance': '0.01',
          'relative_price_tolerance': '0.001', 'minimum_capture_interval_seconds': 300,
          'comparison': 'Same exact current common-share symbol, USD, listing exchange, scheduled regular session and nearby source timestamps.',
          'independence': 'Separately retrieved Yahoo and CNBC responses. CNBC reports its upstream feed. Shared exchange or vendor inputs are possible; upstream independence is not established.',
          'limits': 'A time-window price agreement is a research tolerance, not the same trade, an NBBO check, an executable fill, independently certified market value, historical continuity or profitability.'}
KINDS = {'yahoo': 'quote_check_yahoo', 'cnbc': 'quote_check_cnbc'}
EXCHANGES = {'NYQ': 'NYSE', 'NYSE': 'NYSE', 'NMS': 'NASDAQ', 'NGM': 'NASDAQ', 'NCM': 'NASDAQ',
             'NASDAQ': 'NASDAQ', 'NAS': 'NASDAQ', 'NASDAQNM':'NASDAQ', 'NASDAQGM':'NASDAQ', 'NASDAQCM':'NASDAQ',
             'ASE': 'AMEX', 'AMEX': 'AMEX', 'NYSEAMERICAN': 'AMEX', 'N':'NYSE', 'A':'AMEX',
             'Q':'NASDAQ', 'P':'NYSEARCA', 'PCX':'NYSEARCA', 'PCA':'NYSEARCA', 'NYSEARCA':'NYSEARCA',
             'Z':'BATS', 'BTS':'BATS', 'BATS':'BATS', 'V':'IEX', 'IEX':'IEX'}
RESERVE = 256 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def instant(value):
    if not isinstance(value, str): raise ValueError('Missing aware timestamp')
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None: raise ValueError('Missing timestamp timezone')
    return value.astimezone(timezone.utc)


def decimal(value, maximum='1e12'):
    if isinstance(value, bool): raise ValueError('Boolean price')
    try: result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): raise ValueError('Invalid price') from None
    if not result.is_finite() or result < Decimal('1e-12') or result > Decimal(maximum): raise ValueError('Invalid price')
    return result


def epoch(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)): raise ValueError('Invalid epoch')
    return datetime.fromtimestamp(value, timezone.utc)


def exchange(value):
    return EXCHANGES.get(re.sub(r'[^A-Z]', '', str(value).upper()))


def parse_quote(provider, body, ticker):
    data = json.loads(body)
    if provider == 'yahoo':
        chart = data['chart']; results = chart['result']
        if chart.get('error') or not isinstance(results, list) or len(results) != 1: raise ValueError('Ambiguous Yahoo result')
        q = results[0]['meta']
        if q['symbol'] != ticker or q['instrumentType'] != 'EQUITY' or q['exchangeTimezoneName'] != 'America/New_York': raise ValueError('Yahoo instrument mismatch')
        at = epoch(q['regularMarketTime'])
        result = {'price': str(decimal(q['regularMarketPrice'])), 'as_of': at.isoformat(), 'currency': q['currency'],
                  'exchange': exchange(q['exchangeName']), 'company_name': q.get('longName') or q.get('shortName'),
                  'reported_feed': 'Yahoo chart regularMarketPrice; upstream feed not specified', 'market_field': 'regularMarketPrice'}
    elif provider == 'cnbc':
        quotes = data['QuickQuoteResult']['QuickQuote']
        if not isinstance(quotes, list) or len(quotes) != 1: raise ValueError('Ambiguous CNBC result')
        q = quotes[0]
        if q['symbol'] != ticker or q.get('providerSymbol') != ticker or str(q.get('code')) != '0' or q['assetType'] != 'STOCK' or q['assetSubType'] != 'Common Stock' or q['countryCode'] != 'US': raise ValueError('CNBC instrument mismatch')
        # Do not pair a pre/post-market last with Yahoo's regular-market price.
        if q['last_time'] != q['reg_last_time'] or q['curmktstatus'] not in ('REG_MKT', 'POST_MKT', 'CLOSED'): raise ValueError('CNBC last is not an identified regular-market observation')
        at = instant(q['last_time'])
        ms = decimal(q['last_time_msec'], '1e15')
        if abs(ms / 1000 - Decimal(str(at.timestamp()))) > Decimal('0.001'): raise ValueError('CNBC timestamps disagree')
        result = {'price': str(decimal(q['last'])), 'as_of': at.isoformat(), 'currency': q['currencyCode'],
                  'exchange': exchange(q['exchange']), 'company_name': q.get('name'), 'reported_feed': q.get('source'),
                  'market_field': 'last with matching reg_last_time'}
    else: raise ValueError('Unsupported provider')
    if result['currency'] != 'USD' or not result['exchange']: raise ValueError('Unsupported currency or listing exchange')
    return {'provider': provider, 'ticker': ticker, **result}


def identity_check(identity, ticker, at):
    from security_identity import class_evidence_fresh
    if not identity or identity.get('ticker') != ticker or identity.get('security_type') != 'CS': return False
    if not class_evidence_fresh(identity, at): return False
    try:
        elapsed = (instant(at) - instant(identity['retrieved_at'])).total_seconds()
        source_day = datetime.fromisoformat(identity['source_date'][:10]).date()
        source_age = (instant(at).date() - source_day).days
        return 0 <= elapsed <= 7 * 86400 and -1 <= source_age <= 8 and exchange(identity.get('exchange')) is not None
    except (ValueError, TypeError, KeyError): return False


def evaluate(inputs, sources):
    ticker, at, identity = inputs['ticker'], inputs['captured_at'], inputs['identity']
    quotes = []; issues = []
    identity_ok = identity_check(identity, ticker, at)
    if not identity_ok: issues.append('Current authoritative common-share identity is missing, stale or unsupported.')
    for provider in KINDS:
        source = sources.get(provider)
        record = {'provider': provider, 'state': 'unavailable', 'source_id': source['id'] if source else None,
                  'reason': inputs['failures'].get(provider)}
        if source:
            record.update(source_sha256=source['sha256'], retrieved_at=source['observed_at'], http_status=source['status_code'],
                          source_url=source.get('source_url'), request_parameters=json.loads(source.get('request_json','{}')))
            try:
                if source['status_code'] != 200: raise ValueError('Source HTTP response unavailable')
                parsed = parse_quote(provider, source['body_bytes'], ticker)
                age = Decimal(str((instant(at) - instant(parsed['as_of'])).total_seconds()))
                observed_lag = (instant(source['observed_at']) - instant(parsed['as_of'])).total_seconds()
                local = instant(parsed['as_of']).astimezone(NY)
                cal = calendar_check(local.date().isoformat())
                early = local.date().isoformat() in ('2026-11-27', '2026-12-24')
                close = 13 if early else 16
                regular = cal['state'] == 'scheduled_session' and (9,30) <= (local.hour,local.minute) <= (close,0) and not (local.hour == close and (local.minute or local.second or local.microsecond))
                if not -POLICY['future_clock_allowance_seconds'] <= observed_lag or not 0 <= age <= POLICY['maximum_quote_age_seconds']: raise ValueError('Quote is stale or future-dated')
                if not regular: raise ValueError('Quote is outside the supported scheduled regular session')
                if identity_ok and parsed['exchange'] != exchange(identity['exchange']): raise ValueError('Quote and authoritative listing exchange disagree')
                record.update(parsed, age_seconds=str(age), session_date=local.date().isoformat(), state='dated_reference')
            except (ValueError, TypeError, KeyError, IndexError, OverflowError): record['reason'] = 'Source schema, exact identity, currency, exchange, timestamp or regular-session validation failed.'
        quotes.append(record)
    usable = [q for q in quotes if q['state'] == 'dated_reference']
    comparison = {'state': 'not_comparable', 'absolute_difference': None, 'relative_difference': None,
                  'time_difference_seconds': None, 'price_tolerance': None}
    if identity_ok and len(usable) == 2:
        a,b = usable
        lag = abs((instant(a['as_of']) - instant(b['as_of'])).total_seconds())
        comparison['time_difference_seconds'] = str(lag)
        if a['exchange'] != b['exchange'] or a['session_date'] != b['session_date']: issues.append('Source session or listing exchange differs.')
        elif lag > POLICY['maximum_time_difference_seconds']: issues.append('Source timestamps are too far apart for the declared comparison.')
        else:
            x,y = decimal(a['price']),decimal(b['price']); diff = abs(x-y)
            tolerance = max(Decimal(POLICY['absolute_price_tolerance']), min(x,y) * Decimal(POLICY['relative_price_tolerance']))
            comparison.update(state='within_declared_tolerance' if diff <= tolerance else 'price_disagreement',
                              absolute_difference=str(diff), relative_difference=str(diff / min(x,y)), price_tolerance=str(tolerance))
    if len(usable) != 2: issues.append('Two usable, dated regular-market references are required.')
    return {'methodology': METHOD, 'ticker': ticker, 'captured_at': at, 'policy': POLICY, 'identity': identity,
            'quotes': quotes, 'comparison': comparison, 'issues': issues, 'upstream_independence_verified': False,
            'executable_price_verified': False, 'account_return_verified': False}


def init_quote_checks(path):
    init_sources(path)
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS quote_check_reports (
          id TEXT PRIMARY KEY,ticker TEXT NOT NULL,captured_at TEXT NOT NULL,input_json TEXT NOT NULL,
          input_sha256 TEXT NOT NULL,report_json TEXT NOT NULL,report_sha256 TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS quote_checks_symbol ON quote_check_reports(ticker,captured_at)')
        immutable(db, 'quote_check_reports')


def source_rows(db, inputs):
    output = {}
    for provider, source_id in inputs['source_ids'].items():
        r = db.execute('SELECT * FROM market_source_documents WHERE id=?', (source_id,)).fetchone()
        if not r: raise ValueError('Missing quote source')
        r = dict(r)
        if digest(r['body_bytes']) != r['sha256'] or r['ticker'] != inputs['ticker'] or r['source_kind'] != KINDS[provider]: raise ValueError('Quote source custody mismatch')
        output[provider] = r
    return output


def export_report(db, row):
    if digest(row['input_json']) != row['input_sha256'] or digest(row['report_json']) != row['report_sha256']: raise ValueError('Quote report hash mismatch')
    inputs = json.loads(row['input_json'])
    if inputs['ticker'] != row['ticker'] or inputs['captured_at'] != row['captured_at'] or row['id'] != row['input_sha256']: raise ValueError('Quote row identity mismatch')
    identity = inputs['identity']
    if identity:
        r = db.execute('SELECT ticker,retrieved_at,evidence_json FROM instrument_revisions WHERE id=?', (identity['revision_id'],)).fetchone()
        if not r or {**json.loads(r['evidence_json']), 'revision_id':identity['revision_id'], 'retrieved_at':r['retrieved_at']} != identity or r['ticker'] != inputs['ticker']: raise ValueError('Quote identity custody mismatch')
        if identity.get('class_evidence'):
            from security_identity import replay
            evidence = identity['class_evidence']
            original = db.execute('SELECT * FROM security_class_reports WHERE id=?', (evidence['report_id'],)).fetchone()
            if not original or replay(db, original)['report_sha256'] != evidence['report_sha256']:
                raise ValueError('Supplemental quote identity source differs')
    result = evaluate(inputs, source_rows(db, inputs))
    if encoded(result) != row['report_json']: raise ValueError('Quote report replay mismatch')
    return {'id':row['id'], 'input_sha256':row['input_sha256'], 'report_sha256':row['report_sha256'],
            'report_json':row['report_json'], 'report':result}


def collect(path, ticker, client=None):
    if shutil.disk_usage(str(path) if str(path) != ':memory:' else '.').free < RESERVE + 4_000_000: raise ValueError('Source storage reserve reached')
    with sqlite3.connect(path, timeout=30) as db:
        db.row_factory = sqlite3.Row; latest = db.execute('SELECT * FROM quote_check_reports WHERE ticker=? ORDER BY rowid DESC LIMIT 1', (ticker,)).fetchone()
        at = now()
        if latest and (instant(at)-instant(latest['captured_at'])).total_seconds() < 0:
            raise ValueError('Capture clock precedes the latest retained observation')
        if latest and (instant(at)-instant(latest['captured_at'])).total_seconds() < POLICY['minimum_capture_interval_seconds']:
            return {**export_report(db, latest), 'created':False}
        identity = identities(db).get(ticker)
        if not identity_check(identity,ticker,at): raise ValueError('A fresh authoritative common-share listing identity is required')
    session = client or requests.Session()
    inputs = {'ticker':ticker, 'captured_at':at, 'identity':identity, 'source_ids':{}, 'failures':{}}
    blocked = False
    try:
        for provider,url,params in [('yahoo','https://query2.finance.yahoo.com/v8/finance/chart/'+ticker,{'range':'1d','interval':'1m','includePrePost':'false'}),
                                    ('cnbc','https://quote.cnbc.com/quote-html-webservice/quote.htm',{'symbols':ticker,'output':'json'})]:
            if blocked:
                inputs['failures'][provider] = 'Not requested after a source access/rate-limit response.'; continue
            try:
                response = session.get(url,params=params,headers={'User-Agent':'stocks-analyzer/1.0 (personal research; quote comparison)'},timeout=20,stream=True)
                try:
                    chunks=[]; size=0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 2_000_000: raise ValueError('Source response exceeds storage limit')
                        chunks.append(chunk)
                    body=b''.join(chunks)
                    inputs['source_ids'][provider] = save_source(path,ticker,url,KINDS[provider],response.status_code,params,body,now())
                    blocked = response.status_code in (403,429,503)
                finally: response.close()
            except (requests.RequestException,ValueError): inputs['failures'][provider] = 'Source request failed or exceeded the supported response limit.'
        inputs['captured_at'] = now()
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row; result=evaluate(inputs,source_rows(db,inputs)); raw=encoded(inputs); report=encoded(result); key=digest(raw)
            db.execute('INSERT INTO quote_check_reports VALUES(?,?,?,?,?,?,?)', (key,ticker,inputs['captured_at'],raw,key,report,digest(report)))
            row=db.execute('SELECT * FROM quote_check_reports WHERE id=?',(key,)).fetchone()
            return {**export_report(db,row),'created':True}
    finally:
        if client is None: session.close()


class EmptyCapture(BaseModel):
    model_config = ConfigDict(extra='forbid')


def make_quote_router(path):
    router=APIRouter(prefix='/api/quote-checks',tags=['Cross-provider quote observations']); lock=threading.Lock()
    def symbol(value):
        value=value.upper()
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',value): raise HTTPException(422,'Invalid symbol')
        return value
    def exported(db,row):
        try: return export_report(db,row)
        except (ValueError,KeyError,TypeError): raise HTTPException(409,'Quote source or report integrity failed') from None
    @router.get('/reports/{report_id}')
    def report(report_id:str):
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM quote_check_reports WHERE id=?',(report_id,)).fetchone()
            if not row: raise HTTPException(404,'Quote check not found')
            return exported(db,row)
    @router.get('/sources/{source_id}')
    def source(source_id:str):
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;r=db.execute('SELECT * FROM market_source_documents WHERE id=?',(source_id,)).fetchone()
            if not r or r['source_kind'] not in KINDS.values(): raise HTTPException(404,'Quote source not found')
            if digest(r['body_bytes'])!=r['sha256']: raise HTTPException(409,'Quote source hash mismatch')
            return Response(r['body_bytes'],media_type='application/json',headers={'X-Content-SHA256':r['sha256'],'Cache-Control':'private, no-store'})
    @router.get('/{ticker}')
    def latest(ticker:str):
        ticker=symbol(ticker)
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM quote_check_reports WHERE ticker=? ORDER BY rowid DESC LIMIT 1',(ticker,)).fetchone()
            return exported(db,row) if row else {'ticker':ticker,'report':None,'policy':POLICY}
    @router.post('/{ticker}/capture')
    def capture(ticker:str,body:EmptyCapture):
        ticker=symbol(ticker)
        if not lock.acquire(False): raise HTTPException(409,'A quote check is already running')
        try: return collect(path,ticker)
        except ValueError as e: raise HTTPException(409,str(e)) from None
        finally: lock.release()
    def scheduled_collect():
        # Supplement existing active trials, without altering their frozen policy or observations.
        if not lock.acquire(False): return
        try:
            at=now();day=instant(at).astimezone(NY).date().isoformat()
            if calendar_check(day)['state']!='scheduled_session': return
            with sqlite3.connect(path) as db:
                enabled=db.execute('SELECT enabled FROM signal_trial_settings WHERE id=1').fetchone()
                if not enabled or not enabled[0]: return
                tickers=[r[0] for r in db.execute('SELECT DISTINCT ticker FROM signal_trials WHERE registered_at<=? AND exit_date>=? ORDER BY ticker LIMIT 5',(at,day))]
            for ticker in tickers:
                try: collect(path,ticker)
                except (ValueError,requests.RequestException): continue
        finally: lock.release()
    router.scheduled_collect=scheduled_collect
    return router
