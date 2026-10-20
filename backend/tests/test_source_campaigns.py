import hashlib,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from source_campaigns import init_campaigns,make_campaign_router,build_scope
from research_store import encoded,now

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'x.db';self.started=[];init_campaigns(self.path)
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE saved_list_versions(id INTEGER PRIMARY KEY);INSERT INTO saved_list_versions VALUES(1);
            CREATE TABLE paper_positions(ticker TEXT);INSERT INTO paper_positions VALUES('AAA');INSERT INTO paper_positions VALUES('BBB');
            CREATE TABLE saved_list_items(version_id INTEGER,ticker TEXT,status TEXT,payload_json TEXT);
            CREATE TABLE research_refresh_jobs(id INTEGER PRIMARY KEY,state TEXT,completed INTEGER,failed INTEGER);
            CREATE TABLE ownership_history_runs(id INTEGER PRIMARY KEY,state TEXT,attempted INTEGER,failed INTEGER,imported INTEGER,message TEXT);''')
            for ticker in ('AAA','BBB','OUTSIDE','STALE','WARRANT'):
                item={'ticker':ticker,'status':'documented_inputs','fresh':ticker!='STALE','identity':{'security_type':'warrant' if ticker=='WARRANT' else 'CS'},'instrument_eligibility':{'status':'eligible'},'issues':[]}
                db.execute('INSERT INTO saved_list_items VALUES(?,?,?,?)',(1,ticker,'documented_inputs',encoded(item)))
        self.configure()
    def configure(self,finance=None):
        self.router=make_campaign_router(self.path,finance or self.finance,self.sec);app=FastAPI();app.include_router(self.router);self.client=TestClient(app)
    def tearDown(self):self.temp.cleanup()
    def finance(self,body):
        self.started.extend(body.tickers)
        with sqlite3.connect(self.path) as db:n=db.execute("INSERT INTO research_refresh_jobs(state,completed,failed) VALUES('completed',?,0)",(len(body.tickers),)).lastrowid
        return {'job_id':n}
    def sec(self,body):
        self.started.append(body.ticker)
        with sqlite3.connect(self.path) as db:n=db.execute("INSERT INTO ownership_history_runs(state,attempted,failed,imported,message) VALUES('bounded',0,0,0,'No new document')").lastrowid
        return {'run_id':n}
    def seed(self,kind='financial',tickers=None):
        scope={'methodology':'portfolio-source-pass-v1','kind':kind,'version':1,'tickers':tickers or ['AAA','BBB'],'basis':'fixture','batch_limit':1,'since_date':'2026-10-07' if kind=='insider' else None,'documents_per_issuer':10 if kind=='insider' else None};raw=encoded(scope)
        with sqlite3.connect(self.path) as db:return db.execute("INSERT INTO source_campaigns(kind,created_at,updated_at,state,scope_json,scope_sha256,message) VALUES(?,?,?,'queued',?,?,'fixture')",(kind,now(),now(),raw,hashlib.sha256(raw.encode()).hexdigest())).lastrowid
    def run_sync(self):
        def thread(*,target,args,daemon):return SimpleNamespace(start=lambda:target(*args))
        with patch('source_campaigns.threading.Thread',side_effect=thread),patch('source_campaigns.time.sleep'),patch('source_campaigns.currently_fresh',side_effect=lambda i,a:i.get('fresh',False)):self.router.tick()
    def row(self):return self.client.get('/api/source-campaigns').json()['campaigns'][0]
    def test_complete_pass_is_not_profitability(self):
        self.seed();self.run_sync();p=self.row();self.assertEqual((p['state'],p['cursor'],self.started),('completed',2,['AAA','BBB']));self.assertFalse(p['profitability_verified']);self.assertFalse(p['coverage_complete'])
    def test_scope_and_outcomes_immutable(self):
        self.seed();self.run_sync()
        with sqlite3.connect(self.path) as db:
            for sql in ("UPDATE source_campaigns SET scope_json='{}'","DELETE FROM source_campaigns","UPDATE source_campaign_receipts SET payload_json='{}'","DELETE FROM source_campaign_receipts"):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
    def test_busy_pauses_without_skipping(self):
        self.seed();self.configure(lambda body:(_ for _ in ()).throw(HTTPException(409,'busy')));self.run_sync();self.assertEqual((self.row()['state'],self.row()['cursor']),('paused',0))
    def test_missing_child_identifier_stops_without_retry_loop(self):
        called=[]
        self.seed();self.configure(lambda body:(called.append(body.tickers) or {}));self.run_sync()
        self.assertEqual(len(called),1);self.assertEqual((self.row()['state'],self.row()['cursor']),('paused',0))
    def test_insider_pass_obeys_storage_reserve(self):
        self.seed(kind='insider')
        with patch('source_campaigns.shutil.disk_usage',return_value=SimpleNamespace(free=0)):self.run_sync()
        self.assertEqual(self.started,[]);self.assertEqual((self.row()['state'],self.row()['cursor']),('paused',0))
    def test_blocked_child_keeps_batch_then_resumes(self):
        def block(body):
            with sqlite3.connect(self.path) as db:n=db.execute("INSERT INTO research_refresh_jobs VALUES(NULL,'blocked',0,0)").lastrowid
            return {'job_id':n}
        self.seed();self.configure(block);self.run_sync();self.assertEqual((self.row()['state'],self.row()['cursor']),('paused',0));self.assertEqual(len(self.row()['outcomes']),1)
        self.configure()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE source_campaigns SET state='queued'")
        self.run_sync();self.assertEqual(self.row()['cursor'],2)
    def test_restart_pauses(self):
        self.seed()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE source_campaigns SET state='running',current_job=42")
        init_campaigns(self.path);self.assertEqual((self.row()['state'],self.row()['cursor']),('paused',0))
    def test_tampered_hash_or_progress_fails_closed(self):
        n=self.seed()
        with sqlite3.connect(self.path) as db:db.execute('UPDATE source_campaigns SET cursor=1')
        self.assertEqual(self.client.get('/api/source-campaigns').status_code,409);self.assertEqual(self.client.post(f'/api/source-campaigns/{n}/resume').status_code,409)
        with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER campaign_scope_immutable');db.execute("UPDATE source_campaigns SET scope_sha256='corrupt'")
        self.assertEqual(self.client.get(f'/api/source-campaigns/{n}/scope').status_code,409)
    def test_insider_scope_exclusions(self):
        with sqlite3.connect(self.path) as db:
            db.executemany('INSERT INTO paper_positions VALUES(?)',[('STALE',),('WARRANT',)])
        with sqlite3.connect(self.path) as db,patch('source_campaigns.currently_fresh',side_effect=lambda i,a:i.get('fresh',False)):scope=build_scope(db,'insider',now())
        self.assertEqual(scope['tickers'],['AAA','BBB']);self.assertEqual(scope['documents_per_issuer'],10)
    def test_only_one_active_pass(self):
        n=self.seed()
        with patch('source_campaigns.coverage',return_value={'next_tickers':['AAA']}):self.assertEqual(self.client.post('/api/source-campaigns/financial/start').status_code,409)
        scope=self.client.get(f'/api/source-campaigns/{n}/scope').json();self.assertEqual(hashlib.sha256(encoded(scope['scope']).encode()).hexdigest(),scope['sha256'])
    def test_download_can_remain_unqualified(self):
        with sqlite3.connect(self.path) as db:db.execute("UPDATE saved_list_items SET payload_json=? WHERE ticker='AAA'",(encoded({'ticker':'AAA','status':'needs_evidence','issues':['Missing annual capex'],'fresh':False}),))
        self.seed(tickers=['AAA']);self.run_sync();self.assertEqual(self.row()['outcomes'][0]['outcomes'][0]['status'],'needs_evidence')
    def test_pause_cancel_resume_states(self):
        n=self.seed();self.assertEqual(self.client.post(f'/api/source-campaigns/{n}/pause').status_code,200);self.assertEqual(self.client.post(f'/api/source-campaigns/{n}/cancel').status_code,200);self.assertEqual(self.client.post(f'/api/source-campaigns/{n}/resume').status_code,409);self.assertEqual(self.client.post('/api/source-campaigns/unknown/start').status_code,422)

if __name__=='__main__':unittest.main()

class ResearchSourceGuardTests(unittest.TestCase):
    def run_case(self,denied=False,low_space=False,market_blocked=False):
        from research_store import make_research_router,RefreshRequest
        from financial_sources import init_financial_sources,save_financial_source
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'guard.db';init_financial_sources(path);calls=[]
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE company_analyses(ticker TEXT);INSERT INTO company_analyses VALUES('AAA');INSERT INTO company_analyses VALUES('BBB');
                CREATE TABLE research_refresh_jobs(id INTEGER PRIMARY KEY,started_at TEXT,updated_at TEXT,state TEXT,tickers_json TEXT,completed INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,current_ticker TEXT,errors_json TEXT DEFAULT '[]');
                CREATE TABLE research_refresh_attempts(ticker TEXT PRIMARY KEY,attempted_at TEXT,state TEXT);''')
            if denied:save_financial_source(path,'1',SimpleNamespace(content=b'denied',status_code=403))
            def gather(ticker,**kwargs):
                calls.append(ticker)
                if market_blocked:raise HTTPException(503,'Provider rate limit')
                save_financial_source(path,'2',SimpleNamespace(content=b'denied',status_code=429))
            router=make_research_router(path,gather)
            def thread(*,target,args,daemon):return SimpleNamespace(start=lambda:target(*args))
            with patch('research_store.threading.Thread',side_effect=thread),patch('research_store.rebuild_list'),patch('research_store.shutil.disk_usage',return_value=SimpleNamespace(free=0 if low_space else 10**10)):
                router.refresh_inputs(RefreshRequest(tickers=['AAA','BBB']))
            with sqlite3.connect(path) as db:state,completed=db.execute('SELECT state,completed FROM research_refresh_jobs').fetchone()
            return calls,state,completed
    def test_prior_denial_blocks_all_requests(self):self.assertEqual(self.run_case(denied=True),([], 'blocked',0))
    def test_denial_mid_batch_stops_remaining_requests(self):self.assertEqual(self.run_case(),(['AAA'],'blocked',1))
    def test_storage_reserve_blocks_requests(self):self.assertEqual(self.run_case(low_space=True),([],'blocked',0))
    def test_market_provider_denial_stops_remaining_requests(self):self.assertEqual(self.run_case(market_blocked=True),(['AAA'],'blocked',1))
