"""Frozen, bounded source passes; progress never implies investment performance."""
import hashlib
import json
import sqlite3
import shutil
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from financial_sources import coverage, currently_fresh
from ownership_history import HistoryRequest
from research_store import RefreshRequest, encoded, now, immutable

VERSION = 'portfolio-source-pass-v1'
LIMIT = 568


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init_campaigns(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS source_campaigns (
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, state TEXT NOT NULL, scope_json TEXT NOT NULL,
          scope_sha256 TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
          current_job INTEGER, current_count INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_campaign_receipts (
          id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL, recorded_at TEXT NOT NULL,
          payload_json TEXT NOT NULL, sha256 TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_source_campaign
          ON source_campaigns((1)) WHERE state IN ('queued','running','paused');
        CREATE TRIGGER IF NOT EXISTS campaign_scope_immutable BEFORE UPDATE ON source_campaigns
          WHEN NEW.kind IS NOT OLD.kind OR NEW.created_at IS NOT OLD.created_at
          OR NEW.scope_json IS NOT OLD.scope_json OR NEW.scope_sha256 IS NOT OLD.scope_sha256
          BEGIN SELECT RAISE(ABORT,'Source campaign scope is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_delete BEFORE DELETE ON source_campaigns
          BEGIN SELECT RAISE(ABORT,'Source campaign scopes are retained'); END;
        ''')
        immutable(db, 'source_campaign_receipts')
        # A lost process cannot claim to have finished its in-flight request.
        db.execute("UPDATE source_campaigns SET state='paused',updated_at=?,message=? WHERE state='running'",
                   (now(),'Process restarted; review the interrupted child job, then resume the frozen pass.'))


def checked_scope(row):
    if hashlib.sha256(row['scope_json'].encode()).hexdigest() != row['scope_sha256']:
        raise ValueError('Frozen source scope hash mismatch')
    scope = json.loads(row['scope_json'])
    if scope.get('methodology') != VERSION or scope.get('kind') != row['kind']:
        raise ValueError('Unknown source scope')
    tickers = scope.get('tickers')
    if not isinstance(tickers,list) or not 0 < len(tickers) <= LIMIT or len(set(tickers)) != len(tickers):
        raise ValueError('Invalid bounded source scope')
    if not 0 <= row['cursor'] <= len(tickers):
        raise ValueError('Invalid source pass cursor')
    return scope


def build_scope(db, kind, at):
    db.execute('BEGIN')
    v = db.execute('SELECT id FROM saved_list_versions ORDER BY id DESC LIMIT 1').fetchone()
    if not v:
        raise HTTPException(409,'Rebuild the saved research list first.')
    universe = sorted({r[0] for r in db.execute('SELECT DISTINCT ticker FROM paper_positions')})
    if kind == 'financial':
        tickers = coverage(db,at,queue_limit=LIMIT)['next_tickers']
        basis = 'Eligible original-portfolio common equities needing annual evidence. One frozen pass; downloads may remain unqualified.'
    elif kind == 'insider':
        original = set(universe)
        items = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM saved_list_items WHERE version_id=? AND status='documented_inputs' ORDER BY ticker", (v[0],))]
        tickers = [i['ticker'] for i in items if i['ticker'] in original and currently_fresh(i,at)
                   and i.get('instrument_eligibility',{}).get('status') == 'eligible'
                   and (i.get('identity') or {}).get('security_type') == 'CS']
        basis = 'All currently documented eligible original-portfolio common equities, beyond the focused watchlist; recent issuer submissions only.'
    else:
        raise HTTPException(422,'Unknown source pass kind.')
    if not tickers:
        raise HTTPException(409,'No companies qualify for this source pass in the current saved edition.')
    if len(tickers)>LIMIT:
        raise HTTPException(409,'Source universe exceeds the approved bound.')
    return {'methodology':VERSION, 'kind':kind, 'version':v[0], 'created_at':at,
            'original_universe_sha256':hashlib.sha256(encoded(universe).encode()).hexdigest(),
            'tickers':tickers, 'basis':basis, 'batch_limit':25 if kind=='financial' else 1,
            'documents_per_issuer':10 if kind=='insider' else None,
            'since_date':(datetime.fromisoformat(at)-timedelta(days=7)).date().isoformat() if kind=='insider' else None,
            'coverage_complete':False, 'profitability_verified':False}


def make_campaign_router(path, start_financial, start_sec):
    router = APIRouter(prefix='/api/source-campaigns',tags=['Bounded source coverage'])
    lock = threading.Lock()

    def list_row(db,row):
        scope = checked_scope(row)
        receipts=[]
        advanced=0
        for r in db.execute('SELECT * FROM source_campaign_receipts WHERE campaign_id=? ORDER BY id',(row['id'],)):
            if hashlib.sha256(r['payload_json'].encode()).hexdigest() != r['sha256']:
                raise ValueError('Source outcome receipt hash mismatch')
            payload=json.loads(r['payload_json'])
            ticks=payload.get('tickers',[])
            if payload.get('kind')!=row['kind'] or payload.get('offset')!=advanced or not ticks or ticks!=scope['tickers'][advanced:advanced+len(ticks)]:
                raise ValueError('Source progress does not match its frozen scope')
            if payload.get('advanced'):
                advanced+=len(ticks)
            receipts.append(payload)
        if advanced!=row['cursor']:
            raise ValueError('Source cursor has no matching outcome receipts')
        return {k:row[k] for k in ('id','kind','created_at','updated_at','state','cursor','message','scope_sha256','current_job')} | {
            'total':len(scope['tickers']), 'scope_version':scope['version'], 'basis':scope['basis'],
            'since_date':scope['since_date'], 'next_tickers':scope['tickers'][row['cursor']:row['cursor']+25],
            'outcomes':receipts, 'coverage_complete':False, 'profitability_verified':False}

    @router.get('')
    def overview():
        try:
            with connect(path) as db:
                db.execute('BEGIN')
                return {'methodology':VERSION, 'campaigns':[list_row(db,r) for r in db.execute('SELECT * FROM source_campaigns ORDER BY id DESC LIMIT 10')],
                        'limits':'Original portfolio only. Financial pass: 25 per batch; SEC pass: recent index and up to 10 unparsed filings per issuer. No complete trader portfolio or verified returns.',
                        'restart_policy':'Process interruption pauses the pass. Resume may reattempt an unfinished batch; original receipts and first observations remain unchanged.'}
        except ValueError:
            raise HTTPException(409,'Source pass verification failed; no coverage claim is available.')

    @router.get('/{identifier}/scope')
    def export_scope(identifier:int):
        with connect(path) as db:
            row=db.execute('SELECT * FROM source_campaigns WHERE id=?',(identifier,)).fetchone()
            if not row: raise HTTPException(404,'Source pass not found.')
            try: scope=checked_scope(row)
            except ValueError: raise HTTPException(409,'Source scope verification failed.')
            return {'scope':scope,'sha256':row['scope_sha256']}

    @router.post('/{kind}/start')
    def start(kind:str):
        with connect(path) as db:
            # Resolve the scope consistently before the short exclusive insert.
            scope=build_scope(db,kind,now());db.commit()
            raw=encoded(scope)
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM source_campaigns WHERE state IN ('queued','running','paused')").fetchone():
                raise HTTPException(409,'Finish, resume or cancel the existing source pass first.')
            ident=db.execute("INSERT INTO source_campaigns(kind,created_at,updated_at,state,scope_json,scope_sha256,message) VALUES(?,?,?,'queued',?,?,?)",
                             (kind,scope['created_at'],now(),raw,hashlib.sha256(raw.encode()).hexdigest(),'Frozen pass queued; attempted companies and qualified evidence are separate.')).lastrowid
        tick()
        return {'id':ident,'state':'queued','total':len(scope['tickers'])}

    @router.post('/{identifier}/pause')
    def pause(identifier:int):
        with connect(path) as db:
            count=db.execute("UPDATE source_campaigns SET state='paused',updated_at=?,message=? WHERE id=? AND state IN ('queued','running')",
                             (now(),'Paused. The active bounded child finishes; no further batch starts.',identifier)).rowcount
        if not count:raise HTTPException(409,'Source pass is not active.')
        return {'id':identifier,'state':'paused'}

    @router.post('/{identifier}/resume')
    def resume(identifier:int):
        with connect(path) as db:
            row=db.execute('SELECT * FROM source_campaigns WHERE id=?',(identifier,)).fetchone()
            if not row:raise HTTPException(404,'Source pass not found.')
            try: list_row(db,row)
            except ValueError:raise HTTPException(409,'Source scope verification failed.')
            if row['state']!='paused':raise HTTPException(409,'Only paused source passes can resume.')
            # An unfinished child must be observed, never replaced concurrently.
            db.execute("UPDATE source_campaigns SET state='queued',updated_at=?,message=? WHERE id=?",(now(),'Resume queued from the frozen scope.',identifier))
        tick()
        return {'id':identifier,'state':'queued'}

    @router.post('/{identifier}/cancel')
    def cancel(identifier:int):
        with connect(path) as db:
            count=db.execute("UPDATE source_campaigns SET state='cancelled',updated_at=?,message=? WHERE id=? AND state IN ('queued','running','paused')",(now(),'Cancelled. Existing sources retained; the active bounded child may still finish.',identifier)).rowcount
        if not count:raise HTTPException(409,'Source pass is already final.')
        return {'id':identifier,'state':'cancelled'}

    def set_pause(ident,message):
        with connect(path) as db:
            db.execute("UPDATE source_campaigns SET state='paused',updated_at=?,message=? WHERE id=? AND state IN ('queued','running')",(now(),message,ident))

    def harvest(db,row,scope,child):
        size=row['current_count'];ticks=scope['tickers'][row['cursor']:row['cursor']+size]
        ok=child['state'] in ('completed',) if row['kind']=='financial' else child['state'] in ('bounded','partial')
        v=db.execute('SELECT id FROM saved_list_versions ORDER BY id DESC LIMIT 1').fetchone()[0]
        outcomes=[]
        if row['kind']=='financial':
            for ticker in ticks:
                item=db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?',(v,ticker)).fetchone()
                data=json.loads(item[0]) if item else {}
                outcomes.append({'ticker':ticker,'status':data.get('status','unknown'),
                                 'currently_fresh':currently_fresh(data,now()) if data else False,
                                 'issues':data.get('issues',[]), 'input_sha256':data.get('input_sha256'),
                                 'receipt':(data.get('primary_fact_comparison') or {}).get('receipt')})
        payload={'kind':row['kind'],'offset':row['cursor'],'tickers':ticks,'child_job_id':child['id'],
                 'child_state':child['state'], 'completed':child['completed'] if row['kind']=='financial' else child['attempted'],
                 'failed':child['failed'],'imported':child.get('imported',0), 'scope_version':scope['version'],
                 'result_version':v, 'outcomes':outcomes, 'source_message':child.get('message'),
                 'recorded_at':now(),'advanced':ok,'profitability_verified':False}
        raw=encoded(payload)
        db.execute('INSERT INTO source_campaign_receipts(campaign_id,recorded_at,payload_json,sha256) VALUES(?,?,?,?)',(row['id'],payload['recorded_at'],raw,hashlib.sha256(raw.encode()).hexdigest()))
        db.execute('UPDATE source_campaigns SET cursor=cursor+?,current_job=NULL,current_count=0,updated_at=? WHERE id=?', (size if ok else 0,now(),row['id']))
        return ok

    def work(ident):
        try:
            while True:
                with connect(path) as db:
                    row=db.execute('SELECT * FROM source_campaigns WHERE id=?',(ident,)).fetchone()
                    scope=checked_scope(row)
                    if row['current_job'] is not None:
                        table='research_refresh_jobs' if row['kind']=='financial' else 'ownership_history_runs'
                        child=db.execute(f'SELECT * FROM {table} WHERE id=?',(row['current_job'],)).fetchone()
                        if not child:raise ValueError('Child source job is missing')
                        if child['state']!='running':
                            list_row(db,row)
                            ok=harvest(db,row,scope,dict(child))
                            if not ok:
                                set_message='Child source check stopped. Review access, identity, storage or interrupted requests before resuming; the unfinished batch stays pending.'
                                db.execute("UPDATE source_campaigns SET state=CASE WHEN state='cancelled' THEN state ELSE 'paused' END,message=?,updated_at=? WHERE id=?",(set_message,now(),ident))
                                return
                            continue
                    elif row['state'] not in ('queued','running'):
                        return
                    elif row['cursor']==len(scope['tickers']):
                        db.execute("UPDATE source_campaigns SET state='completed',updated_at=?,message=? WHERE id=?", (now(),'Frozen pass finished. Unqualified companies and bounded insider coverage remain explicit; no profitability established.',ident))
                        return
                    else:
                        list_row(db,row)
                        if shutil.disk_usage(Path(path).parent).free < 256_000_000:
                            db.execute("UPDATE source_campaigns SET state='paused',updated_at=?,message=? WHERE id=?",(now(),'Storage reserve reached; no further bounded child starts.',ident))
                            return
                        size=min(scope['batch_limit'],len(scope['tickers'])-row['cursor'])
                        ticks=scope['tickers'][row['cursor']:row['cursor']+size]
                        db.execute("UPDATE source_campaigns SET state='running',updated_at=? WHERE id=? AND state IN ('queued','running')",(now(),ident))
                        # Commit state before starting a child; never hold DB locks over network work.
                        db.commit()
                        try:
                            child=start_financial(RefreshRequest(tickers=ticks)) if row['kind']=='financial' else start_sec(HistoryRequest(ticker=ticks[0],limit=scope['documents_per_issuer'],since_date=scope['since_date']))
                        except HTTPException as error:
                            set_pause(ident,'Shared source worker is busy; resume after its bounded request finishes.' if error.status_code==409 else 'Source child could not start. Review the dependency before resuming.')
                            return
                        child_id=child.get('job_id',child.get('run_id'))
                        if type(child_id) is not int or child_id<=0:
                            raise ValueError('Source child did not return a valid persistent job ID')
                        db.execute('UPDATE source_campaigns SET current_job=?,current_count=?,updated_at=? WHERE id=?', (child_id,size,now(),ident))
                time.sleep(1)
        except Exception:
            set_pause(ident,'Source pass stopped after scope, worker or storage verification failed. Existing receipts retained.')
        finally:
            lock.release()

    def tick():
        if not lock.acquire(blocking=False):return
        try:
            with connect(path) as db:
                row=db.execute("SELECT id FROM source_campaigns WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:lock.release();return
            threading.Thread(target=work,args=(row['id'],),daemon=True).start()
        except Exception:
            lock.release();raise

    router.tick=tick
    router.work=work
    return router
