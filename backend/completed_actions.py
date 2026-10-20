"""Retrospective issuer-reported conversions; never broker payments or prices."""
import json
import os
import shutil
import sqlite3
from datetime import date
from zoneinfo import ZoneInfo
import requests
from fastapi import APIRouter, HTTPException
from lxml import etree
from market_lineage import LOCK, EmptyCapture, aware, download, normal, notice_text, sha, source
from research_store import encoded, immutable, now
from security_identity import cover_class

METHOD='issuer-completed-action-notices-v1'
ALIASES={'NASDAQ Global Select Market':'NASDAQ','NASDAQ Stock Market LLC':'NASDAQ'}
CASES={
 'AMWD':{'cik':'0000794619','exchange':'NASDAQ','conversion_date':'2026-05-28','kind':'stock_merger',
  'consideration':'5.150 MasterBrand common shares for each AMWD share, plus cash in lieu of fractional shares.',
  'filing':{'form':'8-K','accessionNumber':'0001193125-26-243155','filingDate':'2026-05-28','reportDate':'2026-05-28','primaryDocument':'d50625d8k.htm'},
  'anchors':['Effective as of May 28, 2026','American Woodmark completed its previously announced transaction with MasterBrand',
             '5.150 shares of MasterBrand common stock','plus cash in lieu of any fractional shares', 'suspend trading of American Woodmark common stock']},
 'TBRG':{'cik':'0001169445','exchange':'NASDAQ','conversion_date':'2026-07-09','kind':'cash_merger',
  'consideration':'USD 26.25 per common share in cash, without interest; holder-specific taxes and settlement need records.',
  'filing':{'form':'8-K','accessionNumber':'0001193125-26-299668','filingDate':'2026-07-09','reportDate':'2026-07-09','primaryDocument':'d153395d8k.htm'},
  'anchors':['On July 9, 2026','completed the previously announced acquisition of TruBridge, Inc.', '$26.25 per share in cash, without interest','Item 3.01 Notice of Delisting']},
 'TMHC':{'cik':'0001562476','exchange':'N','conversion_date':'2026-07-24','kind':'cash_merger',
  'consideration':'USD 72.50 per common share in cash, without interest and subject to applicable withholding taxes.',
  'filing':{'form':'8-K','accessionNumber':'0001193125-26-316037','filingDate':'2026-07-24','reportDate':'2026-07-20','primaryDocument':'d148123d8k.htm'},
  'anchors':['On July 24, 2026, pursuant to the Merger Agreement','Merger Sub merged with and into TMHC',
             'converted into the right to receive $72.50 in cash','subject to any applicable withholding taxes','TMHC notified the New York Stock Exchange']}}
LIMIT='Issuer-reported contractual conversion, observed retrospectively. No broker receipt, cash payment, successor shares, tax, fractional-share proceeds, execution price, portfolio value or return is certified. Other corporate actions may be missing. Original records remain unchanged.'


def locations(spec):
    f=spec['filing'];return ('https://www.sec.gov/Archives/edgar/data/'+str(int(spec['cik']))+'/'+f['accessionNumber'].replace('-','')+'/'+f['primaryDocument'],
                           'https://data.sec.gov/submissions/CIK'+spec['cik']+'.json')


def init_actions(path):
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS completed_action_reports (
          id TEXT PRIMARY KEY,ticker TEXT NOT NULL UNIQUE,captured_at TEXT NOT NULL,
          input_json TEXT NOT NULL,report_json TEXT NOT NULL,report_sha256 TEXT NOT NULL)''')
        immutable(db,'completed_action_reports')


def reproduce(db,inputs):
    ticker=inputs['ticker'];spec=CASES.get(ticker);at=aware(inputs['captured_at'])
    if inputs.get('methodology')!=METHOD or not spec or not at: raise ValueError('Unsupported completed-action scope.')
    notice_url,index_url=locations(spec)
    notice,body=source(db,inputs['notice_id'],ticker,'sec_completed_action_notice',notice_url)
    index,index_body=source(db,inputs['index_id'],ticker,'sec_completed_action_submissions',index_url)
    for r in (notice,index):
        if r['request_json']!='{}' or not aware(r['observed_at']) or aware(r['observed_at'])>at: raise ValueError('Completed-action source time or request differs.')
    if not 0<=(at-aware(index['observed_at'])).total_seconds()<=7*86400: raise ValueError('Issuer index was stale at capture.')
    data=json.loads(index_body);recent=data['filings']['recent'];fields=('form','accessionNumber','filingDate','reportDate','primaryDocument')
    if data.get('cik')!=spec['cik'] or any(not isinstance(recent.get(k),list) for k in fields) or len({len(recent[k]) for k in fields})!=1:
        raise ValueError('Completed-action issuer or index arrays differ.')
    rows=[{k:recent[k][i] for k in fields} for i,a in enumerate(recent['accessionNumber']) if a==spec['filing']['accessionNumber']]
    if rows!=[spec['filing']]: raise ValueError('Completed-action exact filing identity differs.')
    if not spec['conversion_date']<=spec['filing']['filingDate']<=at.astimezone(ZoneInfo('America/New_York')).date().isoformat():
        raise ValueError('Reported conversion or filing is future at capture.')
    text=notice_text(body)
    if any(normal(anchor) not in text for anchor in spec['anchors']): raise ValueError('Completed-action notice text requires review.')
    share=cover_class(body,ticker,spec['cik'],spec['filing'],{'exchange':spec['exchange']},exchange_aliases=ALIASES)
    return {'methodology':METHOD,'ticker':ticker,'cik':spec['cik'],'captured_at':inputs['captured_at'],
            'reported_conversion_date':spec['conversion_date'],'date_precision':'calendar_date_only; intraday timing not certified',
            'kind':spec['kind'],'reported_consideration':spec['consideration'],'filing':spec['filing'],
            'former_registered_class':share,'sources':[notice,index],
            'broker_receipt_verified':False,'account_return_verified':False,'complete_action_history_verified':False,'limits':LIMIT}


def replay(db,row):
    row=dict(row);inputs=json.loads(row['input_json'])
    if sha(row['input_json'])!=row['id'] or any(row[k]!=inputs[k] for k in ('ticker','captured_at')): raise ValueError('Completed-action capture metadata differs.')
    report=reproduce(db,inputs)
    if encoded(report)!=row['report_json'] or sha(row['report_json'])!=row['report_sha256']: raise ValueError('Completed-action result differs.')
    return {'id':row['id'],'input_sha256':row['id'],'report_sha256':row['report_sha256'],'report':report}


def reports(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='completed_action_reports'").fetchone(): return {}
    return {r['ticker']:replay(db,r) for r in db.execute('SELECT * FROM completed_action_reports ORDER BY ticker')}


def position_reviews(position,items):
    """Literal record dates only; same-day and post-conversion entries need review."""
    row=items.get(position['ticker'])
    if not row: return []
    r=row['report']
    try:first=date.fromisoformat(position['entry_date'][:10]);last=date.fromisoformat(position['end_date'][:10]);day=date.fromisoformat(r['reported_conversion_date'])
    except (TypeError,ValueError,KeyError): return []
    if last<first or last<day: return []
    return [{'report_id':row['id'],'ticker':r['ticker'],'reported_conversion_date':day.isoformat(),
             'reported_consideration':r['reported_consideration'],'kind':r['kind'],
             'state':'entry_on_or_after_reported_conversion' if first>=day else 'conversion_during_recorded_holding',
             'account_return_verified':False,'limits':LIMIT}]


def capture(path,ticker,client=requests):
    spec=CASES.get(ticker)
    if not spec: raise ValueError('No supported exact completed-action case.')
    if not LOCK.acquire(False): raise ValueError('Another bounded public-source capture is running.')
    try:
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row
            old=db.execute('SELECT * FROM completed_action_reports WHERE ticker=?',(ticker,)).fetchone()
            if old:return replay(db,old)
            if not db.execute('SELECT 1 FROM paper_positions WHERE ticker=?',(ticker,)).fetchone(): raise ValueError('No original portfolio position.')
            if shutil.disk_usage(str(path)).free<256*1024*1024+12_000_000: raise ValueError('Source storage reserve reached.')
            for table in ('market_source_documents','identity_source_documents','financial_source_documents','statement_source_documents'):
                if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
                denied=db.execute('SELECT observed_at FROM '+table+' WHERE status_code IN (403,429,503) ORDER BY observed_at DESC LIMIT 1').fetchone()
                if denied and aware(denied[0]) and 0<=(aware(now())-aware(denied[0])).total_seconds()<900:raise ValueError('Source access cooldown remains active.')
        contact=os.environ.get('EDGAR_USER_AGENT')
        if not contact:raise ValueError('Configured SEC contact is required.')
        notice_url,index_url=locations(spec);headers={'User-Agent':contact}
        notice=download(path,ticker,notice_url,'sec_completed_action_notice',headers,client)
        index=download(path,ticker,index_url,'sec_completed_action_submissions',headers,client)
        inputs={'methodology':METHOD,'ticker':ticker,'captured_at':now(),'notice_id':notice,'index_id':index};raw=encoded(inputs);identifier=sha(raw)
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row;report=encoded(reproduce(db,inputs))
            db.execute('INSERT INTO completed_action_reports VALUES(?,?,?,?,?,?)',(identifier,ticker,inputs['captured_at'],raw,report,sha(report)))
            return replay(db,db.execute('SELECT * FROM completed_action_reports WHERE id=?',(identifier,)).fetchone())
    finally: LOCK.release()


def overview(path):
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN');saved=reports(db)
    return {'methodology':METHOD,'supported_cases':[{'ticker':t,'reported_conversion_date':s['conversion_date']} for t,s in CASES.items()],
            'captures':list(saved.values()),'limits':LIMIT}


def make_actions_router(path):
    router=APIRouter(prefix='/api/completed-actions',tags=['Reported corporate-action review'])
    @router.get('')
    def inspect():
        try:return overview(path)
        except (ValueError,KeyError,TypeError,etree.LxmlError):raise HTTPException(409,'Completed-action evidence failed replay.') from None
    @router.get('/reports/{identifier}')
    def document(identifier:str):
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM completed_action_reports WHERE id=?',(identifier,)).fetchone()
            if not row:raise HTTPException(404,'Completed-action report not archived.')
            try:return replay(db,row)
            except (ValueError,KeyError,TypeError,etree.LxmlError):raise HTTPException(409,'Completed-action evidence failed replay.') from None
    @router.post('/{ticker}/capture')
    def collect(ticker:str,body:EmptyCapture):
        try:return capture(path,ticker)
        except (ValueError,KeyError,TypeError,etree.LxmlError,requests.RequestException):raise HTTPException(409,'Completed-action capture unavailable or requires review; earlier evidence retained.') from None
    return router
