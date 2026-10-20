"""Bounded, replayable same-security symbol links for legacy price references.

This v1 catalog requires an explicit unchanged-CUSIP notice. It never maps a
merger consideration, preferred share or warrant to an underlying common share.
Linked series are excluded from trading decisions and copy studies.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, HTTPException
from lxml import html, etree
from pydantic import BaseModel, ConfigDict
from market_provenance import save_source, parse_original_chart
from research_store import encoded, immutable, instant, now

METHOD = 'unchanged-cusip-symbol-history-v1'
LOCK = threading.Lock()
# Freeze these notice definitions with v1 for reproducible old captures.
LINKS = {
    'SCVL': {'provider_symbol': 'SHOE', 'cik': '0000895447', 'exchange': 'Nasdaq', 'chart_exchange': 'NMS',
             'effective_date': '2026-06-12', 'filing_date': '2026-06-11', 'accession': '0001193125-26-266625',
             'notice_url': 'https://www.sec.gov/Archives/edgar/data/895447/000119312526266625/scvl-20260610.htm',
             'anchors': ['at the market open on June 12, 2026', 'new trading symbol “SHOE” and will cease trading under the trading symbol “SCVL.”',
                         'There will be no change in the CUSIP number for the Company’s common stock.',
                         'the number of shares represented by those certificates will remain unchanged.']},
    'VSCO': {'provider_symbol': 'VSXY', 'cik': '0001856437', 'exchange': 'NYSE', 'chart_exchange': 'NYQ',
             'effective_date': '2026-06-02', 'filing_date': '2026-05-21', 'accession': '0001856437-26-000007',
             'notice_url': 'https://www.sec.gov/Archives/edgar/data/1856437/000185643726000007/ex991vscomay212026pressrel.htm',
             'anchors': ['(NYSE: VSCO)', 'new ticker symbol VSXY on the New York Stock Exchange on June 2, 2026.',
                         'No action is required by shareholders.', 'its CUSIP number will remain unchanged.']},
}
NOTICE = ('Same-security symbol continuity is supported by an archived issuer notice stating unchanged CUSIP and a matching SEC issuer index. '
          'Yahoo historical adjustments and price correctness are not independently certified. This is a retrospectively linked vendor reference, '
          'not a broker fill, account return or historical strategy-selection input. No legacy position, cash or trading rule is changed.')


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def init_lineage(path):
    with sqlite3.connect(path, timeout=30) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS market_lineage_histories (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, provider_symbol TEXT NOT NULL,
            captured_at TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            input_json TEXT NOT NULL, report_json TEXT NOT NULL, report_sha256 TEXT NOT NULL)''')
        immutable(db, 'market_lineage_histories')


def normal(text):
    return ' '.join(unicodedata.normalize('NFKC', text).translate(str.maketrans({'“':'"', '”':'"', '’':"'"})).split())


def aware(text):
    try:
        value=datetime.fromisoformat(text.replace('Z','+00:00'))
        return instant(text) if value.tzinfo is not None else None
    except (ValueError,TypeError,AttributeError): return None


def source(db, identifier, ticker, kind, url):
    row = db.execute('SELECT * FROM market_source_documents WHERE id=?', (identifier,)).fetchone()
    if not row: raise ValueError('Original symbol-link source is missing.')
    row = dict(row); body = row.pop('body_bytes')
    expected = sha(encoded([row['ticker'], row['source_url'], row['source_kind'], row['status_code'], row['request_json'], sha(body)]))
    if len(body)>2_000_000 or sha(body)!=row['sha256'] or expected!=identifier or row['status_code']!=200 or (row['ticker'],row['source_kind'],row['source_url'])!=(ticker,kind,url):
        raise ValueError('Original symbol-link source identity or fingerprint differs.')
    return row, body


def notice_text(body):
    if b'<!ENTITY' in body.upper(): raise ValueError('Entity declarations are unsupported.')
    body.decode('utf-8')  # This catalog supports UTF-8/ASCII originals only.
    markup=body
    if body.lstrip().startswith(b'<DOCUMENT>'):
        # EDGAR can retain the original exhibit SGML envelope. It is not HTML;
        # require exactly one known exhibit and preserve its original bytes.
        envelope=re.fullmatch(rb'\s*<DOCUMENT>\s*<TYPE>EX-99\.1\s*<SEQUENCE>\d+\s*<FILENAME>[^\r\n]+\s*<DESCRIPTION>[^\r\n]+\s*<TEXT>\s*(<html>.*</html>)\s*</TEXT>\s*</DOCUMENT>\s*',body,re.S)
        if not envelope or body.count(b'<TEXT>')!=1 or body.count(b'<DOCUMENT>')!=1: raise ValueError('Unsupported SEC exhibit envelope.')
        markup=envelope[1]
    tree = html.fromstring(markup,parser=html.HTMLParser(encoding='utf-8',no_network=True,recover=False))
    for node in tree.xpath('//script|//style|//noscript'): node.drop_tree()
    for node in tree.xpath('//*[@hidden or @style]'):
        style=''.join(node.get('style','').lower().split())
        if node.get('hidden') is not None or 'display:none' in style or 'visibility:hidden' in style: node.drop_tree()
    return normal(' '.join(tree.itertext()))


def reproduce(db, inputs):
    from market_lineage_v2 import METHOD as V2, reproduce as reproduce_v2
    if inputs.get('methodology')==V2: return reproduce_v2(db,inputs)
    return base_report(db,inputs,LINKS.get(inputs['ticker']),METHOD)


def base_report(db, inputs, spec, method):
    from market_history import validate_history, completed_day
    ticker = inputs['ticker']
    if inputs.get('methodology')!=method or not spec or inputs['provider_symbol']!=spec['provider_symbol']:
        raise ValueError('Unsupported exact symbol-link policy.')
    at = aware(inputs['captured_at'])
    if not at or inputs['captured_at'][:10]<spec['effective_date']:
        raise ValueError('Symbol link is future or has no timezone.')
    records = inputs['positions']
    if not records or any(r['ticker']!=ticker for r in records) or min(r['entry_date'][:10] for r in records)!=inputs['start_date']:
        raise ValueError('Original position date scope differs.')
    notice, body = source(db, inputs['notice_id'], ticker, 'sec_symbol_notice', spec['notice_url'])
    if notice['request_json']!='{}': raise ValueError('Notice request scope differs.')
    text=notice_text(body)
    if any(normal(anchor) not in text for anchor in spec['anchors']):
        raise ValueError('Exact issuer continuity notice requires review.')
    index_url = 'https://data.sec.gov/submissions/CIK'+spec['cik']+'.json'
    index, body = source(db, inputs['index_id'], ticker, 'sec_symbol_submissions', index_url)
    data = json.loads(body); recent = data['filings']['recent']; fields = ('accessionNumber','form','filingDate')
    if index['request_json']!='{}' or data.get('cik')!=spec['cik'] or data.get('tickers')!=[spec['provider_symbol']] or data.get('exchanges')!=[spec['exchange']]:
        raise ValueError('Current SEC issuer, exact symbol or exchange differs.')
    if any(not isinstance(recent.get(k),list) for k in fields) or len({len(recent[k]) for k in fields})!=1:
        raise ValueError('SEC index arrays differ.')
    if [(recent['form'][i], recent['filingDate'][i]) for i,a in enumerate(recent['accessionNumber']) if a==spec['accession']]!=[('8-K',spec['filing_date'])]:
        raise ValueError('Continuity notice accession or filing date differs.')
    new = spec['provider_symbol']; chart_url='https://query2.finance.yahoo.com/v8/finance/chart/'+new
    chart, body = source(db, inputs['chart_id'], new, 'yahoo_original_chart', chart_url)
    first,last = date.fromisoformat(inputs['start_date']),date.fromisoformat(inputs['end_date']); ny=ZoneInfo('America/New_York')
    params={'period1':int(datetime.combine(first,datetime.min.time(),ny).timestamp()),
            'period2':int(datetime.combine(last+timedelta(days=1),datetime.min.time(),ny).timestamp()),
            'interval':'1d','includePrePost':False,'events':'div,splits,capitalGains'}
    if json.loads(chart['request_json'])!=params or last>completed_day(chart['observed_at']):
        raise ValueError('Historical request bounds or close availability differ.')
    chart_data=json.loads(body)['chart']['result'][0];meta=chart_data['meta']
    if meta.get('exchangeName')!=spec['chart_exchange']: raise ValueError('Provider exchange differs.')
    if chart_data.get('events',{}).get('capitalGains'): raise ValueError('Capital-gain actions require separate review.')
    for r in (notice,index,chart):
        if not aware(r['observed_at']) or aware(r['observed_at'])>at: raise ValueError('Source was not yet observed at capture.')
    if not 0<=(at-instant(index['observed_at'])).total_seconds()<=7*86400 or not 0<=(at-instant(chart['observed_at'])).total_seconds()<=86400:
        raise ValueError('Source index or chart was stale at capture.')
    history=validate_history(parse_original_chart(body,new,first.isoformat(),last.isoformat(),chart['id']),new,first.isoformat(),last.isoformat(),inputs['captured_at'])
    days={b['date'] for b in history['bars']}
    if any(r['entry_date'][:10] not in days or (r.get('close_date') and r['close_date'][:10] not in days) for r in records):
        raise ValueError('Exact original position endpoints are missing; no nearest-date substitution.')
    return {'methodology':method,'ticker':ticker,'provider_symbol':new,'cik':spec['cik'],
            'effective_date':spec['effective_date'],'captured_at':inputs['captured_at'],
            'start_date':first.isoformat(),'end_date':last.isoformat(),'history':history,
            'continuity':'issuer_reported_unchanged_cusip','sources':[notice,index,chart],
            'account_return_verified':False,'independent_prices_verified':False,'complete_session_coverage_verified':False,
            'available_for_trading_decisions':False,'notice':NOTICE}


def replay(db, row):
    row=dict(row); inputs=json.loads(row['input_json'])
    if row['id']!=sha(row['input_json']) or any(row[k]!=inputs[k] for k in ('ticker','provider_symbol','captured_at','start_date','end_date')):
        raise ValueError('Symbol-link capture metadata differs.')
    report=reproduce(db,inputs)
    if encoded(report)!=row['report_json'] or sha(row['report_json'])!=row['report_sha256']:
        raise ValueError('Symbol-link result no longer reproduces.')
    return {'id':row['id'],'input_sha256':row['id'],'report_sha256':row['report_sha256'],'report':report}


def linked_histories(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='market_lineage_histories'").fetchone(): return {}
    out={}
    for row in db.execute('SELECT * FROM market_lineage_histories WHERE rowid IN (SELECT MAX(rowid) FROM market_lineage_histories GROUP BY ticker)'):
        verified=replay(db,row);r=verified['report']
        out[r['ticker']]={'id':verified['id'],'ticker':r['ticker'],'provider_symbol':r['provider_symbol'],
                          'observed_at':r['captured_at'],'start_date':r['start_date'],'end_date':r['end_date'],
                          'source_url':'https://finance.yahoo.com/quote/'+r['provider_symbol']+'/history/',
                          'sha256':sha(encoded(r['history'])),'history':r['history'],'available_for_trading_decisions':False,
                          'symbol_lineage':{k:v for k,v in verified.items() if k!='report'}}
    return out


def download(path, ticker, url, kind, headers, client):
    with client.get(url,headers=headers,timeout=(5,25),allow_redirects=False,stream=True) as response:
        chunks=[];size=0
        for chunk in response.iter_content(65536):
            size+=len(chunk)
            if size>2_000_000: raise ValueError('Symbol-link source exceeds 2 MB.')
            chunks.append(chunk)
        identifier=save_source(path,ticker,url,kind,response.status_code,{},b''.join(chunks),now())
        if response.status_code!=200: raise ValueError('Symbol-link source access failed; remaining requests stopped.')
        return identifier


def capture(path, ticker, client=requests):
    from market_history import completed_day, fetch_history
    spec=catalog().get(ticker)
    if not spec: raise ValueError('This symbol has no supported unchanged-CUSIP notice.')
    if not LOCK.acquire(False): raise ValueError('A symbol-link capture is already running.')
    try:
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row
            old=db.execute('SELECT * FROM market_lineage_histories WHERE ticker=? ORDER BY rowid DESC LIMIT 1',(ticker,)).fetchone()
            if old: return replay(db,old)  # Static one-time legacy scope; retries never refresh or replace it.
            positions=[dict(r) for r in db.execute('SELECT id,ticker,entry_date,close_date FROM paper_positions WHERE ticker=? ORDER BY id',(ticker,))]
            if not positions: raise ValueError('No original portfolio positions for this exact symbol.')
            if shutil.disk_usage(str(path)).free<256*1024*1024+12_000_000: raise ValueError('Source storage reserve reached.')
            for table in ('market_source_documents','identity_source_documents','financial_source_documents','statement_source_documents'):
                if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
                denied=db.execute('SELECT observed_at FROM '+table+' WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
                if denied and 0<=(instant(now())-instant(denied[0])).total_seconds()<900: raise ValueError('Source requests paused after a recent access denial.')
        contact=os.environ.get('EDGAR_USER_AGENT')
        if not contact: raise ValueError('Configured SEC contact is required.')
        headers={'User-Agent':contact}
        notice=download(path,ticker,spec['notice_url'],'sec_symbol_notice',headers,client)
        index=download(path,ticker,'https://data.sec.gov/submissions/CIK'+spec['cik']+'.json','sec_symbol_submissions',headers,client)
        extra={}
        if spec.get('former_filing'):
            extra['former_id']=download(path,ticker,spec['former_filing']['url'],'sec_former_symbol_filing',headers,client)
        start=min(r['entry_date'][:10] for r in positions);at=now();end=completed_day(at).isoformat()
        history=fetch_history(spec['provider_symbol'],start,end,at,db_path=path,original_response=True)
        inputs={'methodology':spec['methodology'],'ticker':ticker,'provider_symbol':spec['provider_symbol'],'captured_at':now(),
                'start_date':start,'end_date':end,'positions':positions,'notice_id':notice,'index_id':index,'chart_id':history['original_source_document_id'],**extra}
        raw=encoded(inputs);identifier=sha(raw)
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row;report=reproduce(db,inputs);result=encoded(report)
            db.execute('INSERT INTO market_lineage_histories VALUES(?,?,?,?,?,?,?,?,?)',
                       (identifier,ticker,spec['provider_symbol'],inputs['captured_at'],start,end,raw,result,sha(result)))
            return replay(db,db.execute('SELECT * FROM market_lineage_histories WHERE id=?',(identifier,)).fetchone())
    finally: LOCK.release()


class EmptyCapture(BaseModel):
    model_config=ConfigDict(extra='forbid')


def catalog():
    from market_lineage_v2 import LINKS as newer, METHOD as V2
    return {**{t:{**s,'methodology':METHOD} for t,s in LINKS.items()},**{t:{**s,'methodology':V2} for t,s in newer.items()}}


def overview(path):
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        rows=[replay(db,r) for r in db.execute('SELECT * FROM market_lineage_histories ORDER BY ticker')]
    return {'methodology':METHOD,'supported_links':[{'ticker':t,**{k:v for k,v in s.items() if k in ('provider_symbol','effective_date','notice_url','methodology')}} for t,s in catalog().items()],
            'captures':rows,'notice':NOTICE}


def make_lineage_router(path):
    router=APIRouter(prefix='/api/market-history/lineage',tags=['Historical symbol continuity'])
    @router.get('')
    def listing():
        try: return overview(path)
        except (ValueError,KeyError,TypeError,etree.LxmlError): raise HTTPException(409,'Symbol-link source or capture failed replay.') from None
    @router.get('/{identifier}')
    def document(identifier:str):
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM market_lineage_histories WHERE id=?',(identifier,)).fetchone()
            if not row: raise HTTPException(404,'Symbol-link capture not archived.')
            try: return replay(db,row)
            except (ValueError,KeyError,TypeError,etree.LxmlError): raise HTTPException(409,'Symbol-link source or capture failed replay.') from None
    @router.post('/{ticker}/capture')
    def collect(ticker:str, body:EmptyCapture):
        try: return capture(path,ticker)
        except (ValueError,KeyError,TypeError,etree.LxmlError,requests.RequestException): raise HTTPException(409,'Symbol-link capture unavailable or requires source review; prior evidence retained.') from None
    return router
