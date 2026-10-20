import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
from research_store import init_research,rebuild_item,encoded,instant
from ownership_history import init_ownership,save_form4,HistoryRequest
from equity_workspace import (init_workspace,seed_watchlist,decision,make_preview,commit_preview,make_workspace_router,check_actions)
from paper_ledger import append_event,read_ledger
from test_source_history import inputs,XML,ACCESSION,URL,AT

class InlineThread:
    def __init__(self,target,args,**kwargs):self.target,self.args=target,args
    def start(self):self.target(*self.args)

class EquityWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE company_analyses(ticker TEXT PRIMARY KEY,company_name TEXT,analyzed_at TEXT,fcf_yield REAL,quant_score INTEGER);CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT,cik TEXT);CREATE TABLE filings(company_id INTEGER,form_type TEXT,url TEXT,accession_number TEXT,filed_date TEXT);')
        init_research(self.path);init_ownership(self.path);init_workspace(self.path)
        saved,data,identity=inputs();data['quote_source']='Explicit quote fixture'
        self.item=rebuild_item(saved,(data,AT),identity,AT);self.store(self.item)
        self.app=FastAPI();self.router=make_workspace_router(self.path,self.start_sec);self.app.include_router(self.router);self.client=TestClient(self.app)
        self.clock=patch('equity_workspace.now',return_value=AT);self.clock.start()
    def tearDown(self):self.clock.stop();self.temp.cleanup()
    def store(self,item):
        with sqlite3.connect(self.path) as db:
            v=db.execute('INSERT INTO saved_list_versions(created_at,model_version,basis,item_count,content_hash) VALUES(?,?,?,?,?)',(AT,'fixture','Explicit fixture',1,'fixture')).lastrowid
            db.execute('INSERT INTO saved_list_items VALUES(?,?,?,?)',(v,item['ticker'],item['status'],encoded(item)))
    def start_sec(self,body):
        with sqlite3.connect(self.path) as db:
            v=db.execute("INSERT INTO ownership_history_runs(ticker,started_at,state,selected,indexed_total,message) VALUES(?,?,'bounded',0,0,'Fixture empty bounded result')",(body.ticker,AT)).lastrowid
        return {'run_id':v}
    def funding(self):
        self.assertEqual(self.client.post('/api/equity-workspace/paper/fund',json={'amount':'10000'}).status_code,200)

    def test_shortlist_lookup_migration_preserves_sources_and_decision_evidence(self):
        self.parsed_purchase()
        queries=[("SELECT event_json FROM trade_events WHERE json_extract(event_json,'$.asset')=? AND json_extract(event_json,'$.transaction_code')='P' ORDER BY first_seen_at DESC",),
                 ("SELECT accession FROM ownership_documents WHERE json_extract(metadata_json,'$.ticker')=? AND json_extract(metadata_json,'$.amendment')=1",)]
        with sqlite3.connect(self.path) as db:
            db.execute('DROP INDEX equity_purchase_lookup');db.execute('DROP INDEX equity_amendment_lookup')
            db.row_factory=sqlite3.Row
            before=decision(db,self.item['ticker'],AT)
            tables=('trade_events','ownership_documents','paper_ledger_events')
            original={t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}
        init_workspace(self.path)
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            self.assertEqual(decision(db,self.item['ticker'],AT),before)
            for table in tables:
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')],original[table])
            for (query,) in queries:
                plan=' '.join(str(r[3]) for r in db.execute('EXPLAIN QUERY PLAN '+query,(self.item['ticker'],)))
                self.assertIn('SEARCH',plan)
                self.assertNotIn('SCAN ',plan)
                self.assertNotIn('TEMP B-TREE',plan)

    def test_research_response_hash_matches_exact_frozen_context_and_later_read(self):
        import hashlib
        d=self.client.post('/api/equity-workspace/decision/TEST').json()
        frozen=self.client.get('/api/equity-workspace/decisions/'+d['id']).json()
        self.assertIn('financial_context',d['decision'])
        self.assertEqual(d['decision'],frozen['inputs'])
        self.assertEqual(d['input_sha256'],frozen['input_sha256'])
        self.assertEqual(d['input_sha256'],hashlib.sha256(encoded(d['decision']).encode()).hexdigest())
    def parsed_purchase(self):save_form4(self.path,XML,ACCESSION,URL,'2026-10-10',AT)
    def get_decision(self,at=AT):
        with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return decision(db,'TEST',at)
    def test_current_inputs_ignore_legacy_score_and_purchase_absence_is_explicit(self):
        self.assertFalse(self.get_decision()['financial_gate_reasons']);self.assertEqual(self.get_decision()['state'],'documented_inputs_no_fresh_purchase')
        self.assertIsNone(self.get_decision()['expected_return']);self.assertFalse(self.get_decision()['trader_profitability_verified'])
        item={**self.item,'legacy_quant_score':0};self.store(item)
        self.assertFalse(self.get_decision()['financial_gate_reasons'])
    def test_original_purchase_receipt_matches_and_staleness_is_not_retroactive(self):
        self.parsed_purchase();self.assertEqual(self.get_decision()['fresh_purchases'],1)
        old=self.get_decision('2026-10-25T18:00:00+00:00');self.assertEqual(old['fresh_purchases'],0)
        self.assertIn('stale',' '.join(old['purchase_evidence'][0]['reasons']))
    def test_filing_transaction_tampering_cannot_qualify(self):
        self.parsed_purchase()
        with sqlite3.connect(self.path) as db:
            raw=json.loads(db.execute('SELECT event_json FROM trade_events').fetchone()[0]);raw['reported_shares']='999'
            db.execute('DROP TRIGGER immutable_trade_identity')
            db.execute('UPDATE trade_events SET event_json=?',(json.dumps(raw),))
        d=self.get_decision();self.assertEqual(d['fresh_purchases'],0);self.assertIsNone(d['purchase_evidence'][0]['source_sha256'])
    def test_later_amendment_blocks_original_purchase_and_pending_paper_preview(self):
        self.funding();self.parsed_purchase()
        preview=make_preview(self.path,'TEST','buy','1','public_purchase',AT)
        amended=XML.replace('<documentType>4</documentType>','<documentType>4/A</documentType><dateOfOriginalSubmission>2026-10-10</dateOfOriginalSubmission>')
        accession=ACCESSION.replace('000001','000002')
        save_form4(self.path,amended,accession,URL.replace('000001','000002'),'2026-10-11',AT)
        result=self.get_decision();self.assertEqual(result['fresh_purchases'],0)
        original=next(p for p in result['purchase_evidence'] if p['event']['accession']==ACCESSION)
        self.assertEqual(original['amendment_evidence'][0]['accession'],accession)
        with self.assertRaisesRegex(ValueError,'purchase evidence changed'):commit_preview(self.path,preview['id'],AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)
    def test_missing_old_date_only_annotation_does_not_invalidate_reported_source_fields(self):
        self.parsed_purchase()
        with sqlite3.connect(self.path) as db:
            raw=json.loads(db.execute('SELECT event_json FROM trade_events').fetchone()[0]);raw.pop('date_status')
            db.execute('DROP TRIGGER immutable_trade_identity');db.execute('UPDATE trade_events SET event_json=?',(json.dumps(raw),))
        result=self.get_decision();self.assertEqual(result['fresh_purchases'],1)
        self.assertIsNotNone(result['purchase_evidence'][0]['source_sha256'])
    def test_funding_is_explicit_simulated_once_only_and_invalid_amounts_do_not_write(self):
        for value in ['NaN','-1','0','1e1000']:
            self.assertEqual(self.client.post('/api/equity-workspace/paper/fund',json={'amount':value}).status_code,422)
        self.funding();self.assertEqual(self.client.post('/api/equity-workspace/paper/fund',json={'amount':'10000'}).status_code,422)
        self.assertEqual(read_ledger(self.path)['event_count'],1);self.assertIn('simulated',read_ledger(self.path)['events'][0]['source'])
    def test_buy_costs_mark_cash_and_atomic_idempotent_record(self):
        self.funding();preview=make_preview(self.path,'TEST','buy','10','fundamentals',AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)
        self.assertEqual(preview['event']['price'],'10.010000');self.assertEqual(preview['event']['fee'],'0.10')
        self.assertEqual(preview['result']['cash'],'9899.800000');self.assertEqual(float(preview['result']['pnl']),-.2)
        self.assertTrue(commit_preview(self.path,preview['id'],AT)['created']);self.assertFalse(commit_preview(self.path,preview['id'],'2026-10-25T18:00:00Z')['created'])
        self.assertEqual(read_ledger(self.path)['event_count'],3)
    def test_risk_cash_and_fresh_purchase_guards(self):
        self.funding()
        for side,shares,strategy in [('buy','1001','fundamentals'),('buy','201','fundamentals'),('sell','1','fundamentals'),('buy','1','public_purchase'),('buy','NaN','fundamentals'),('buy','1e15','fundamentals')]:
            with self.subTest(side=side,shares=shares),self.assertRaises(ValueError):make_preview(self.path,'TEST',side,shares,strategy,AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)
        self.parsed_purchase();preview=make_preview(self.path,'TEST','buy','1','public_purchase',AT)
        self.assertTrue(commit_preview(self.path,preview['id'],AT)['created'])
    def test_quote_currency_and_time_gate_prevent_recording(self):
        self.funding()
        for change in [{'quote_currency':'JPY'},{'quote_as_of':'2026-10-11T18:00:00Z'},{'quote_as_of':'2026-10-14T18:00:00Z'},{'price':0}]:
            self.store({**self.item,**change})
            with self.assertRaises(ValueError):make_preview(self.path,'TEST','buy','1','fundamentals',AT)
    def test_expired_source_changed_and_ledger_changed_previews_are_rejected(self):
        self.funding();preview=make_preview(self.path,'TEST','buy','1','fundamentals',AT)
        with self.assertRaisesRegex(ValueError,'expired'):commit_preview(self.path,preview['id'],'2026-10-13T18:11:00Z')
        self.store({**self.item,'price':11})
        with self.assertRaisesRegex(ValueError,'Source edition'):commit_preview(self.path,preview['id'],AT)
        second=make_preview(self.path,'TEST','buy','1','fundamentals',AT)
        append_event(self.path,{'event_id':'fixture-fee','kind':'fee','amount':'1','occurred_at':AT,'currency':'USD','source':'Explicit fixture fee'})
        with self.assertRaisesRegex(ValueError,'Ledger changed'):commit_preview(self.path,second['id'],AT)
        self.assertEqual(read_ledger(self.path)['event_count'],2)
    def test_storage_failure_rolls_back_trade_and_mark_as_one_batch(self):
        self.funding();preview=make_preview(self.path,'TEST','buy','1','fundamentals',AT)
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER fail_mark BEFORE INSERT ON paper_ledger_events WHEN NEW.event_id LIKE 'forward-mark-%' BEGIN SELECT RAISE(ABORT,'Fixture storage failure');END")
        with self.assertRaises(sqlite3.IntegrityError):commit_preview(self.path,preview['id'],AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)
    def test_changed_cost_policy_invalidates_unrecorded_preview(self):
        from equity_workspace import POLICY
        self.funding();preview=make_preview(self.path,'TEST','buy','1','fundamentals',AT)
        with patch('equity_workspace.POLICY',{**POLICY,'fees_bps':99}):
            with self.assertRaisesRegex(ValueError,'Policy'):commit_preview(self.path,preview['id'],AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)
    def test_paper_exit_and_new_marks_preserve_cash_share_accounting(self):
        self.funding();commit_preview(self.path,make_preview(self.path,'TEST','buy','10','fundamentals',AT)['id'],AT)
        self.assertEqual(self.client.post('/api/equity-workspace/paper/marks').status_code,200)
        exit_=make_preview(self.path,'TEST','sell','10','fundamentals',AT)
        self.assertEqual(len(exit_['events']),1);commit_preview(self.path,exit_['id'],AT)
        state=read_ledger(self.path);self.assertEqual(state['positions'],[]);self.assertAlmostEqual(float(state['cash']),9999.6)
    def test_decision_snapshot_is_immutable_and_export_hash_checked(self):
        d=self.client.post('/api/equity-workspace/decision/TEST').json();identifier=d['id']
        self.assertEqual(self.client.get('/api/equity-workspace/decisions/'+identifier).json()['inputs']['research_version'],1)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE equity_decisions SET input_sha256=? WHERE id=?',('changed',identifier))
            db.execute('DROP TRIGGER immutable_equity_decisions_UPDATE');db.execute('UPDATE equity_decisions SET input_sha256=? WHERE id=?',('changed',identifier))
        self.assertEqual(self.client.get('/api/equity-workspace/decisions/'+identifier).status_code,409)
    def test_focused_monitor_cutoff_status_and_disabled_scheduler(self):
        seed_watchlist(self.path)
        with patch('equity_workspace.threading.Thread',InlineThread):
            result=self.client.post('/api/equity-workspace/monitor/run');self.assertEqual(result.status_code,200)
        d=self.client.get('/api/equity-workspace').json();self.assertEqual(d['monitor_runs'][0]['state'],'bounded');self.assertEqual(d['monitor_runs'][0]['completed'],1)
        self.client.put('/api/equity-workspace/monitor',json={'enabled':False})
        self.router.scheduled_monitor()
        self.assertEqual(len(self.client.get('/api/equity-workspace').json()['monitor_runs']),1)
    def test_monitor_denial_stops_and_pauses_instead_of_hammering_other_issuers(self):
        seed_watchlist(self.path)
        def denied(body):
            r=self.start_sec(body)
            with sqlite3.connect(self.path) as db:db.execute("UPDATE ownership_history_runs SET state='blocked',message='HTTP 429 fixture denial' WHERE id=?",(r['run_id'],))
            return r
        app=FastAPI();app.include_router(make_workspace_router(self.path,denied));client=TestClient(app)
        with patch('equity_workspace.threading.Thread',InlineThread):self.assertEqual(client.post('/api/equity-workspace/monitor/run').status_code,200)
        d=client.get('/api/equity-workspace').json();self.assertFalse(d['monitor_enabled']);self.assertEqual(d['monitor_runs'][0]['state'],'blocked')
    def test_known_split_and_distribution_require_economic_reconciliation(self):
        split={'ticker':'TEST','effective_date':'2026-10-12','new_shares':1,'old_shares':10,'source':'https://www.sec.gov/fixture'}
        events=[{'ticker':'TEST','kind':'buy','shares':'10','quote_at':'2026-10-10T18:00:00Z','occurred_at':'2026-10-10T18:00:00Z'}]
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            with patch('equity_workspace.CONFIRMED_SPLITS',[split]),patch('equity_workspace.REVIEWED_DISTRIBUTIONS',[]):
                with self.assertRaisesRegex(ValueError,'matching'):check_actions(db,events,'TEST',AT,AT)
                events.append({'ticker':'TEST','kind':'split','new_shares':'1','old_shares':'10','source':split['source'],'occurred_at':'2026-10-12T13:30:00Z'})
                check_actions(db,events,'TEST',AT,AT)
                events.append({'ticker':'TEST','kind':'sell','shares':'1','occurred_at':AT})
                check_actions(db,events,'TEST',AT,AT)
            with patch('equity_workspace.CONFIRMED_SPLITS',[]),patch('equity_workspace.REVIEWED_DISTRIBUTIONS',[split]):
                with self.assertRaisesRegex(ValueError,'distribution'):check_actions(db,events[:1],'TEST',AT,AT)
    def test_sec_recent_cutoff_skips_old_unparsed_filings_without_requesting_them(self):
        from ownership_history import make_ownership_router
        from unittest.mock import Mock
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
            db.execute('INSERT INTO filings VALUES(?,?,?,?,?)',(1,'4',URL,ACCESSION,'2026-10-10'))
            db.execute('INSERT INTO filings VALUES(?,?,?,?,?)',(1,'4',URL.replace('000001','000000'),ACCESSION.replace('000001','000000'),'2026-09-01'))
        app=FastAPI();app.include_router(make_ownership_router(self.path));client=TestClient(app)
        response=Mock(status_code=200,content=XML.encode())
        with patch('ownership_history.threading.Thread',InlineThread),patch('ownership_history.requests.get',return_value=response) as fetch:
            r=client.post('/api/trade-history/sec/sync',json={'ticker':'TEST','limit':10,'refresh_index':False,'since_date':'2026-10-01'})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['selected'],1);self.assertEqual(fetch.call_count,1)
        self.assertEqual(client.get('/api/trade-history').json()['parsed_documents'],1)
        self.assertEqual(client.post('/api/trade-history/sec/sync',json={'ticker':'TEST','since_date':'2099-01-01'}).status_code,422)
    def test_source_worker_thread_failure_is_explicit_and_can_retry(self):
        seed_watchlist(self.path)
        with patch('equity_workspace.threading.Thread') as thread:
            thread.return_value.start.side_effect=RuntimeError('Fixture failed start')
            with self.assertRaises(RuntimeError):self.client.post('/api/equity-workspace/monitor/run')
        self.assertEqual(self.client.get('/api/equity-workspace').json()['monitor_runs'][0]['state'],'blocked')
        with patch('equity_workspace.threading.Thread',InlineThread):
            self.assertEqual(self.client.post('/api/equity-workspace/monitor/run').status_code,200)
    def test_current_sec_index_with_pre_xml_history_does_not_block_fresh_xml(self):
        from ownership_history import refresh_issuer_index
        from unittest.mock import Mock
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        recent={'form':['4','4','4'],'accessionNumber':[ACCESSION,'0001234567-01-000002','0001234567-00-000003'],
                'filingDate':['2026-10-10','2001-01-01','2000-01-01'],'primaryDocument':['form4.xml','old.txt','']}
        data={'cik':'1234567','tickers':['TEST'],'filings':{'recent':recent,'files':[]}}
        response=Mock(status_code=200,content=json.dumps(data).encode());response.json.return_value=data
        result=refresh_issuer_index(self.path,'TEST',fetch=Mock(return_value=response))
        self.assertEqual(result['source_rows'],1);self.assertEqual(result['unsupported_current_references'],2)
        with sqlite3.connect(self.path) as db:
            raw,count=db.execute("SELECT document_json,unsupported_references FROM ownership_index_documents WHERE kind='current'").fetchone()
            self.assertEqual(json.loads(raw),data);self.assertEqual(count,2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM filings').fetchone()[0],1)

if __name__=='__main__':unittest.main()
