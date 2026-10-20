"""Immutable vendor reference histories, with explicit adjustment and identity limits."""
import hashlib
import json
import math
import sqlite3
import threading
from threading import Thread
import time
from collections import Counter
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from research_store import encoded, immutable, now
from portfolio_reconciliation import CONFIRMED_SPLITS, REVIEWED_DISTRIBUTIONS, UNRESOLVED_ACTION_NOTES
from market_provenance import init_sources, calendar_check, source_references, fetch_original_chart

VERSION = 'yahoo-reference-history-v1'
NY = ZoneInfo('America/New_York')


def init_market_history(path):
    init_sources(path)
    from market_lineage import init_lineage
    init_lineage(path)
    from completed_actions import init_actions
    init_actions(path)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS market_history_documents (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, observed_at TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL, source_url TEXT NOT NULL,
            sha256 TEXT NOT NULL, payload_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS market_history_symbol ON market_history_documents(ticker,observed_at);
            CREATE TABLE IF NOT EXISTS market_history_runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            state TEXT NOT NULL, selected INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL, tickers_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS market_history_attempts (
            id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ticker TEXT NOT NULL,
            observed_at TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL);''')
        immutable(db, 'market_history_documents')
        immutable(db, 'market_history_attempts')
        if 'end_date' not in {r[1] for r in db.execute('PRAGMA table_info(market_history_runs)')}:
            db.execute('ALTER TABLE market_history_runs ADD COLUMN end_date TEXT')
        if 'original_response' not in {r[1] for r in db.execute('PRAGMA table_info(market_history_runs)')}:
            db.execute('ALTER TABLE market_history_runs ADD COLUMN original_response INTEGER NOT NULL DEFAULT 0')


def completed_day(at):
    current = datetime.fromisoformat(at.replace('Z','+00:00'))
    if current.tzinfo is None:raise ValueError('History observation requires a timezone')
    current=current.astimezone(NY)
    # Conservative daily-bar availability: use today's data only after 17:30 ET.
    return current.date() if (current.hour,current.minute)>=(17,30) else current.date()-timedelta(days=1)


def value(raw, allow_zero=False):
    if isinstance(raw,bool):raise ValueError('Boolean price')
    try:n=Decimal(str(raw))
    except Exception:raise ValueError('Invalid market value') from None
    if not n.is_finite() or n<0 or (not allow_zero and n==0) or n>Decimal('1e18') or (n!=0 and n<Decimal('1e-18')):
        raise ValueError('Nonfinite/nonpositive market value')
    return str(n)


def validate_history(payload, ticker, start, end, observed_at):
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    if last<first or (last-first).days>1100 or last>completed_day(observed_at):
        raise ValueError('Invalid or incomplete history interval')
    if payload.get('schema')!=VERSION or payload.get('price_basis')!='split_adjusted' or payload.get('adjusted_close_basis')!='vendor_total_return_proxy':
        raise ValueError('Historical price adjustment basis is required')
    meta=payload.get('metadata',{})
    if meta.get('symbol')!=ticker or meta.get('currency')!='USD' or meta.get('exchangeTimezoneName')!='America/New_York' or meta.get('instrumentType') not in ('EQUITY','ETF'):
        raise ValueError('History symbol, currency, exchange or instrument mismatch')
    bars=payload.get('bars')
    if not isinstance(bars,list) or not bars or len(bars)>800:
        raise ValueError('Missing or oversized daily history')
    previous=None;split_product=Decimal(1)
    for b in bars:
        day=date.fromisoformat(b['date'])
        if not first<=day<=last or (previous is not None and day<=previous):
            raise ValueError('Duplicate, unordered or out-of-range market date')
        previous=day
        o,h,l,c,a=[Decimal(value(b[k])) for k in ('open','high','low','close','adjusted_close')]
        if not l<=o<=h or not l<=c<=h:
            raise ValueError('OHLC range does not contain open and close')
        value(b.get('dividend'),allow_zero=True)
        value(b.get('split_ratio'),allow_zero=True)
        if b['split_ratio']:
            split_product*=Decimal(str(b['split_ratio']))
            if not Decimal('1e-18')<=split_product<=Decimal('1e18'):raise ValueError('Unsupported cumulative split scale')
    return payload


def fetch_history(ticker,start,end,observed_at,db_path=None,original_response=False):
    t=yf.Ticker(ticker)
    if original_response:
        if not db_path:raise ValueError('Original source archive path required')
        return validate_history(fetch_original_chart(ticker,start,end,t._data,db_path,observed_at),ticker,start,end,observed_at)
    # auto_adjust/repair/back_adjust stay off. Yahoo Close is STILL split-adjusted.
    try:
        h=t.history(start=start,end=(date.fromisoformat(end)+timedelta(days=1)).isoformat(),
                    auto_adjust=False,back_adjust=False,repair=False,actions=True,keepna=True,raise_errors=True,timeout=20)
    except yf.exceptions.YFTzMissingError:
        if not db_path:raise
        return validate_history(fetch_original_chart(ticker,start,end,t._data,db_path,observed_at),ticker,start,end,observed_at)
    meta=t.get_history_metadata()
    bars=[];omitted=[]
    for stamp,row in h.iterrows():
        prices=[row.get(k) for k in ('Open','High','Low','Close','Adj Close')]
        if any(v is None or not math.isfinite(float(v)) for v in prices):
            omitted.append(str(stamp.date()));continue
        bars.append({'date':str(stamp.date()), **dict(zip(('open','high','low','close','adjusted_close'),map(float,prices))),
                     'dividend':float(row.get('Dividends',0)),'split_ratio':float(row.get('Stock Splits',0))})
    payload={'schema':VERSION,'price_basis':'split_adjusted','adjusted_close_basis':'vendor_total_return_proxy',
             'metadata':{k:meta.get(k) for k in ('symbol','currency','instrumentType','exchangeTimezoneName','longName','shortName')},
             'bars':bars,'omitted_price_dates':omitted,'library_version':yf.__version__,
             'processing':'Library-normalized Yahoo daily data; adjustment and repair disabled. Not an original HTTP response or broker execution record.'}
    return validate_history(payload,ticker,start,end,observed_at)


def save_history(path,ticker,start,end,payload,observed_at):
    validate_history(payload,ticker,start,end,observed_at)
    raw=encoded(payload);sha=hashlib.sha256(raw.encode()).hexdigest()
    doc_id=hashlib.sha256(encoded([ticker,start,end,observed_at,sha]).encode()).hexdigest()
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO market_history_documents VALUES(?,?,?,?,?,?,?,?)',
                   (doc_id,ticker,observed_at,start,end,'https://finance.yahoo.com/quote/'+ticker+'/history/',sha,raw))
    return doc_id


def latest_histories(db, include_linked=False):
    db.row_factory=sqlite3.Row
    result={}
    for row in db.execute('SELECT d.* FROM market_history_documents d WHERE d.rowid=(SELECT x.rowid FROM market_history_documents x WHERE x.ticker=d.ticker ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1)'):
        doc=dict(row)
        if hashlib.sha256(doc['payload_json'].encode()).hexdigest()!=doc['sha256']:
            raise ValueError('Archived historical data hash mismatch')
        doc['history']=validate_history(json.loads(doc.pop('payload_json')),doc['ticker'],doc['start_date'],doc['end_date'],doc['observed_at'])
        result[doc['ticker']]=doc
    # Retrospective symbol links are inspection evidence only. Existing copy
    # studies and decision paths keep the original direct-symbol universe.
    if include_linked:
        from market_lineage import linked_histories
        for ticker,doc in linked_histories(db).items():
            if ticker not in result: result[ticker]=doc
    return result


def in_range(price,bar):
    if price is None:return False
    n=Decimal(str(price))
    return n.is_finite() and Decimal(str(bar['low']))*Decimal('.99')<=n<=Decimal(str(bar['high']))*Decimal('1.01')


def compare_position(p,doc):
    reported=p.get('realized_return_pct')
    try:
        if reported is not None and (isinstance(reported,bool) or not math.isfinite(float(reported))):reported=None
    except (ValueError,TypeError,OverflowError):reported=None
    result={'id':p['id'],'ticker':p['ticker'],'status':'missing_reference','recorded_return_pct':reported,
            'reference_price_return_pct':None,'reference_adjusted_close_return_pct':None,'account_return_verified':False,
            'notes':['Regular-session daily price ranges do not prove fills or extended-hours quotes. Legacy dates lack verified exchange-local quote timestamps.',
                     'Current symbol metadata does not verify every historical identity or corporate action.']}
    if reported is not None:reported=float(reported);result['recorded_return_pct']=reported
    result.update(entry_date=(p.get('entry_date') or '')[:10],exit_date=(p.get('close_date') or '')[:10] or None,
                  entry_calendar=calendar_check(p.get('entry_date')),exit_calendar=calendar_check(p.get('close_date')) if p.get('close_date') else None)
    for label,c in (('entry',result['entry_calendar']),('close',result['exit_calendar'])):
        if c and c['state']=='scheduled_closed':result['notes'].append(f"The literal recorded {label} date is {c['reason']} in the NYSE calendar. It is not a verified exchange-local quote date; no date or price is substituted.")
    if not doc:return result
    try:
        value(p.get('entry_price'))
        if p['status']=='closed':value(p.get('close_price'))
    except ValueError:
        result['status']='invalid_recorded_prices';return result
    history=doc['history'];bars={b['date']:b for b in history['bars']}
    if doc.get('symbol_lineage'):
        result.update(provider_symbol=doc['provider_symbol'],symbol_lineage=doc['symbol_lineage'])
        result['notes'].append('Issuer-reported unchanged-CUSIP symbol link: '+p['ticker']+' → '+doc['provider_symbol']+'. Retrospective vendor reference only; excluded from trading decisions and copy studies.')
    try:
        entry=date.fromisoformat(p['entry_date'][:10]).isoformat()
        exit_day=date.fromisoformat(p['close_date'][:10]).isoformat() if p.get('close_date') else None
        if exit_day and exit_day<entry:raise ValueError('Invalid holding dates')
    except (ValueError,TypeError):
        result['status']='invalid_recorded_dates';return result
    result.update(document_id=doc['id'],source_url=doc['source_url'],source_observed_at=doc['observed_at'],price_basis=history['price_basis'],
                  entry_date=entry,exit_date=exit_day,provider_name=history['metadata'].get('longName'))
    result['original_source_document_id']=history.get('original_source_document_id')
    result['action_review_notes']=[dict(a) for a in UNRESOLVED_ACTION_NOTES if a['ticker']==p['ticker']]
    a=bars.get(entry);b=bars.get(exit_day) if exit_day else None
    splits=[x for x in history['bars'] if x['split_ratio']]
    confirmed=[x for x in CONFIRMED_SPLITS if x['ticker']==p['ticker'] and doc['start_date']<=x['effective_date']<=doc['end_date']]
    result['primary_split_checks']=[{**x,'vendor_matches':any(s['date']==x['effective_date'] and abs(Decimal(str(s['split_ratio']))-Decimal(x['new_shares'])/Decimal(x['old_shares']))<Decimal('1e-8') for s in splits)} for x in confirmed]
    result['distribution_checks']=[dict(x) for x in REVIEWED_DISTRIBUTIONS if x['ticker']==p['ticker'] and entry<x['effective_date']<=doc['end_date']]
    if any(not x['vendor_matches'] for x in result['primary_split_checks']):result['notes'].append('Vendor action data does not match all confirmed SEC split notices.')
    if not a:
        result['notes'].append('No source bar on the recorded entry date. No adjacent-day substitution.');return result
    def multiplier(day):
        f=Decimal(1)
        for s in splits:
            if day<s['date']:f*=Decimal(str(s['split_ratio']))
        return f
    entry_factor=multiplier(entry)
    raw_entry=Decimal(str(p['entry_price']))
    rebased_entry=raw_entry/entry_factor
    result.update(entry_reference_close=a['close'],entry_reference_range=[a['low'],a['high']],
        entry_stored_in_reference_range=in_range(raw_entry,a),entry_rebased_in_reference_range=in_range(rebased_entry,a),
        entry_rebased_price=str(rebased_entry),entry_future_split_factor=str(entry_factor),splits=[{'date':s['date'],'ratio':s['split_ratio']} for s in splits])
    if p['status']!='closed':
        result['status']='entry_checked_exit_time_unknown'
        result['notes'].append('The open-position mark has no verified quote timestamp; no closing return inferred.');return result
    if not b:
        result['notes'].append('No source bar on the recorded close date.');return result
    exit_factor=multiplier(exit_day)
    raw_exit=Decimal(str(p['close_price'])) if p.get('close_price') else Decimal(0)
    exit_rebased=raw_exit/exit_factor
    result.update(exit_reference_close=b['close'],exit_reference_range=[b['low'],b['high']],
        exit_stored_in_reference_range=in_range(raw_exit,b),exit_rebased_in_reference_range=in_range(exit_rebased,b),
        exit_rebased_price=str(exit_rebased),exit_future_split_factor=str(exit_factor),
        reference_price_return_pct=float((Decimal(str(b['close']))/Decimal(str(a['close']))-1)*100),
        reference_adjusted_close_return_pct=float((Decimal(str(b['adjusted_close']))/Decimal(str(a['adjusted_close']))-1)*100))
    unconfirmed=[s for s in splits if entry<s['date'] and not any(c['effective_date']==s['date'] and c['vendor_matches'] for c in result['primary_split_checks'])]
    missing_confirmed=[c for c in result['primary_split_checks'] if entry<c['effective_date'] and (not c['vendor_matches'] or c.get('requires_review'))]
    if result['distribution_checks'] or unconfirmed or missing_confirmed:
        result['status']='corporate_action_review_required'
        result['notes'].append('Share-only scenarios are blocked: distributions, unmatched vendor adjustments or missing primary split matches require review. Vendor close ratios remain reference proxies.')
        return result
    if entry_factor!=exit_factor and not result['entry_stored_in_reference_range'] and result['entry_rebased_in_reference_range'] and result['exit_rebased_in_reference_range']:
        result['status']='split_share_change_missing_from_price_ratio'
        result['notes'].append('Source ranges are consistent with recorded prices as traded before/after a split. The original price ratio omits the share-count change; instrument and fill evidence remain unverified.')
    elif result['entry_stored_in_reference_range'] and result['exit_stored_in_reference_range']:
        result['status']='compatible_adjusted_reference_ranges'
    elif result['entry_rebased_in_reference_range'] and result['exit_rebased_in_reference_range']:
        result['status']='compatible_rebased_reference_ranges'
    else:result['status']='outside_reference_ranges'
    result['conditional_recorded_split_return_pct']=float((raw_exit*(entry_factor/exit_factor)/raw_entry-1)*100)
    result['conditional_basis']='Only if recorded prices are unadjusted as traded and retrieved splits are complete; excludes dividends, fees and fill evidence.'
    return result


def market_overview(path,ticker=''):
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        histories=latest_histories(db,include_linked=True)
        refs=source_references(db)
        positions=[dict(p) for p in db.execute("SELECT * FROM paper_positions WHERE (?='' OR ticker=?) ORDER BY id",(ticker,ticker))]
        comparisons=[compare_position(p,histories.get(p['ticker'])) for p in positions]
        for p in comparisons:
            for c in p.get('primary_split_checks',[])+p.get('distribution_checks',[])+p.get('action_review_notes',[]):c['source_archive']=refs.get(c['source'])
            p['calendar_source_archive']=refs.get(p['entry_calendar'].get('source_url'))
        runs=[dict(r) for r in db.execute('SELECT * FROM market_history_runs ORDER BY id DESC LIMIT 3')]
        attempts=[dict(r) for r in db.execute("SELECT * FROM market_history_attempts WHERE state!='archived' ORDER BY id DESC LIMIT 20")]
        for a in attempts:a['source_archive']=refs.get('https://query2.finance.yahoo.com/v8/finance/chart/'+a['ticker'])
    return {'documents':len(histories),'linked_symbol_histories':sum(bool(d.get('symbol_lineage')) for d in histories.values()),'positions':len(positions),'positions_with_reference':sum(p.get('entry_reference_close') is not None for p in comparisons),
        'positions_with_history':sum(p.get('source_url') is not None for p in comparisons),
        'comparison_statuses':dict(Counter(p['status'] for p in comparisons)),
        'non_session_record_dates':sum(any(c and c['state']=='scheduled_closed' for c in (p['entry_calendar'],p['exit_calendar'])) for p in comparisons),
        'positions_with_split_ratio_warning':sum(p['status']=='split_share_change_missing_from_price_ratio' for p in comparisons),
        'comparisons':comparisons,'runs':runs,'failures':attempts,
        'methodology':'Yahoo daily OHLC is split-adjusted; adjusted-close ratios are vendor return proxies. Reference studies exclude actual fills/fees and cannot certify account returns. Split factors include retrieved actions after each endpoint through this archive date.'}


class HistoryRequest(BaseModel):
    tickers:list[str]=Field(default_factory=list,max_length=25)
    limit:int=Field(default=10,ge=1,le=25)
    original_response:bool=False


def gap_review(path):
    """Exact missing portfolio histories, with source/identity causes kept distinct."""
    from research_store import identities
    with sqlite3.connect(path, timeout=30) as db:
        db.row_factory = sqlite3.Row; db.execute('BEGIN')
        identity = identities(db)
        from market_lineage import linked_histories
        linked = linked_histories(db)
        rows = db.execute('''SELECT p.ticker,MIN(substr(p.entry_date,1,10)) AS start_date,COUNT(*) AS positions
            FROM paper_positions p WHERE NOT EXISTS(SELECT 1 FROM market_history_documents m WHERE m.ticker=p.ticker)
            GROUP BY p.ticker ORDER BY p.ticker''').fetchall()
        output = []
        for row in rows:
            if row['ticker'] in linked: continue
            ticker = row['ticker']; instrument = identity.get(ticker)
            receipt = db.execute("SELECT * FROM market_source_documents WHERE ticker=? AND source_kind='yahoo_original_chart' ORDER BY observed_at DESC LIMIT 1", (ticker,)).fetchone()
            attempt = db.execute('SELECT observed_at,state,message FROM market_history_attempts WHERE ticker=? ORDER BY id DESC LIMIT 1', (ticker,)).fetchone()
            source = None; state = 'source_not_archived'; metadata = None
            if receipt:
                valid = hashlib.sha256(receipt['body_bytes']).hexdigest() == receipt['sha256']
                source = {k: receipt[k] for k in ('id','status_code','observed_at','source_url','sha256')}
                source['hash_verified'] = valid
                if not valid: state = 'source_integrity_failed'
                elif receipt['status_code'] in (403,429): state = 'source_access_blocked'
                elif receipt['status_code'] != 200: state = 'source_unavailable_for_request'
                else:
                    state = 'source_not_usable_as_validated_history'
                    try:
                        result = json.loads(receipt['body_bytes'])['chart']['result'][0]
                        metadata = {k: result['meta'].get(k) for k in ('symbol','currency','exchangeTimezoneName','instrumentType')}
                        metadata['reported_timestamp_count'] = len(result.get('timestamp') or [])
                    except (ValueError,KeyError,TypeError,IndexError): pass
            model_state = 'historical_identity_unresolved' if not instrument else 'unsupported_equity_model' if instrument['security_type'] not in ('CS','fund') else 'current_listing_identified_historical_identity_unverified'
            output.append({'ticker':ticker,'requested_start_date':row['start_date'],'positions':row['positions'],
                           'source_state':state,'model_state':model_state,'identity':{k:instrument.get(k) for k in ('title','security_type','source_url','source_date','revision_id')} if instrument else None,
                           'latest_receipt':source,'reported_chart_metadata':metadata,'latest_attempt':dict(attempt) if attempt else None,
                           'verified_account_return':None})
        runs = [dict(r) for r in db.execute('SELECT * FROM market_history_runs ORDER BY id DESC LIMIT 3')]
    return {'missing_count':len(output),'linked_symbol_histories':len(linked),'gaps':output,'runs':runs,'verified_account_return':None,
            'notice':'A failed source request does not prove delisting. No adjacent dates or underlying shares are substituted. Separately archived unchanged-CUSIP symbol links are counted explicitly. Warrants and unresolved identities require their own evidence/model. Current listing identification does not establish historical continuity.'}


def make_market_router(path):
    router=APIRouter(prefix='/api/market-history',tags=['Historical market references']);lock=threading.Lock()
    @router.get('/gaps')
    def gaps():return gap_review(path)
    @router.get('')
    def overview(ticker:str=Query('',max_length=16,pattern=r'^[A-Za-z0-9.\-]*$')):
        return market_overview(path,ticker.upper())
    @router.get('/documents/{document_id}')
    def document(document_id:str):
        with sqlite3.connect(path,timeout=15) as db:
            db.row_factory=sqlite3.Row;r=db.execute('SELECT * FROM market_history_documents WHERE id=?',(document_id,)).fetchone()
            if not r:
                from market_lineage import linked_histories
                try:
                    linked=next((d for d in linked_histories(db).values() if d['id']==document_id),None)
                except (ValueError,KeyError,TypeError): raise HTTPException(409,'Symbol-linked history failed original-source replay.') from None
                if linked: return linked
        if not r:raise HTTPException(404,'Price history not archived')
        result=dict(r);result['history']=json.loads(result.pop('payload_json'));return result
    @router.get('/sources/{source_id}')
    def source_document(source_id:str):
        import hashlib
        with sqlite3.connect(path,timeout=15) as db:
            db.row_factory=sqlite3.Row;r=db.execute('SELECT * FROM market_source_documents WHERE id=?',(source_id,)).fetchone()
        if not r:raise HTTPException(404,'Original response not archived')
        result=dict(r);body=result.pop('body_bytes')
        if hashlib.sha256(body).hexdigest()!=result['sha256']:raise HTTPException(409,'Original response hash mismatch')
        result['body_hex']=body.hex();return result
    def run(run_id,selections,end,original_response=False):
        archived=failed=0;state='bounded';message='Reference histories archived; no account or executed copy return certified.'
        try:
            for ticker,start in selections:
                at=now();outcome='archived';detail='Validated vendor reference history.'
                try:
                    payload=fetch_history(ticker,start,end,at,db_path=path,original_response=original_response)
                    save_history(path,ticker,start,end,payload,at);archived+=1
                except Exception as e:
                    failed+=1;outcome='unavailable';detail=type(e).__name__+'; source unavailable or failed identity/value validation.'
                    if 'RateLimit' in type(e).__name__:
                        state='blocked';message='Vendor rate limit; stopped remaining requests. Existing sources retained.'
                with sqlite3.connect(path,timeout=30) as db:
                    db.execute('INSERT INTO market_history_attempts(run_id,ticker,observed_at,state,message) VALUES(?,?,?,?,?)',(run_id,ticker,at,outcome,detail))
                    db.execute('UPDATE market_history_runs SET archived=?,failed=?,message=? WHERE id=?',(archived,failed,message,run_id))
                if state=='blocked':break
                time.sleep(.75)
            if failed and state!='blocked':
                state='partial';message=f'{archived} histories archived; {failed} unavailable or invalid. Prior evidence retained; no account return certified.'
        except Exception:state='blocked';message='Unexpected source/storage interruption; existing evidence retained.'
        finally:
            try:
                with sqlite3.connect(path,timeout=30) as db:db.execute('UPDATE market_history_runs SET state=?,finished_at=?,archived=?,failed=?,message=? WHERE id=?',(state,now(),archived,failed,message,run_id))
            finally:lock.release()
    @router.post('/refresh')
    def refresh(body:HistoryRequest):
        symbols=list(dict.fromkeys(s.upper() for s in body.tickers))
        import re
        if any(not re.fullmatch(r'[A-Z0-9.\-]{1,16}',s) for s in symbols):raise HTTPException(422,'Invalid market symbol')
        if not lock.acquire(False):raise HTTPException(409,'Market reference import is running')
        try:
            end=completed_day(now()).isoformat()
            with sqlite3.connect(path,timeout=15) as db:
                limited=db.execute("SELECT finished_at FROM market_history_runs WHERE state='blocked' AND message LIKE 'Vendor rate limit%' ORDER BY id DESC LIMIT 1").fetchone()
                if limited and limited[0] and (datetime.fromisoformat(now())-datetime.fromisoformat(limited[0])).total_seconds()<900:
                    raise HTTPException(429,'The history vendor was rate-limited; wait 15 minutes before retrying.')
                earliest=dict(db.execute('SELECT ticker,MIN(substr(entry_date,1,10)) FROM paper_positions GROUP BY ticker'))
                if not symbols:
                    if body.original_response:
                        from zoneinfo import ZoneInfo
                        cutoff=int(datetime.combine(date.fromisoformat(end)+timedelta(days=1),datetime.min.time(),ZoneInfo('America/New_York')).timestamp())
                        covered=set(r[0] for r in db.execute("SELECT ticker FROM market_source_documents WHERE source_kind='yahoo_original_chart' AND json_extract(request_json,'$.period2')=?",(cutoff,)))
                    else:covered=set(r[0] for r in db.execute('SELECT ticker FROM market_history_documents WHERE end_date>=?',(end,)))
                    attempted=set(r[0] for r in db.execute('SELECT a.ticker FROM market_history_attempts a JOIN market_history_runs r ON r.id=a.run_id WHERE r.end_date=? AND r.original_response=?',(end,int(body.original_response))))
                    symbols=[t for t in sorted(earliest) if t not in covered and t not in attempted][:body.limit]
                if not symbols:raise HTTPException(409,'No remaining portfolio symbols for this reference date')
                default=min(earliest.values(),default=(date.fromisoformat(end)-timedelta(days=365)).isoformat())
                selected=[(s,earliest.get(s,default)) for s in symbols[:body.limit]]
                run_id=db.execute("INSERT INTO market_history_runs(started_at,state,selected,message,tickers_json,end_date,original_response) VALUES(?,'running',?,'Fetching bounded vendor history',?,?,?)",(now(),len(selected),encoded([s for s,_ in selected]),end,int(body.original_response))).lastrowid
            Thread(target=run,args=(run_id,selected,end,body.original_response),daemon=True).start()
            return {'run_id':run_id,'selected':len(selected)}
        except Exception:lock.release();raise
    return router
