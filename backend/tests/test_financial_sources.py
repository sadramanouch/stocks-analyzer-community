import gzip,json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from financial_sources import init_financial_sources,save_financial_source,compare_primary,coverage
from research_store import encoded
from research_store import make_research_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch,Mock

AT='2026-10-14T18:00:00+00:00'


class FinancialSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'sources.db';init_financial_sources(self.path)
        fact={'start':'2025-01-01','end':'2025-12-31','filed':'2026-02-01','form':'10-K','accn':'0000000001-26-000001','val':100}
        self.payload={'cik':1,'facts':{'us-gaap':{k:{'units':{'USD':[{**fact,'val':v}]}} for k,v in {'Revenues':100,'NetIncomeLoss':20,'NetCashProvidedByUsedInOperatingActivities':30,'PaymentsToAcquirePropertyPlantAndEquipment':10}.items()}}}
        self.data={'ticker':'TEST','financial_period_end':'2025-12-31','financial_provenance':{},'cik':'0000000001'}
        for field,concept in [('revenue','Revenues'),('net_income','NetIncomeLoss'),('operating_cf','NetCashProvidedByUsedInOperatingActivities'),('capex','PaymentsToAcquirePropertyPlantAndEquipment')]:
            f=self.payload['facts']['us-gaap'][concept]['units']['USD'][0]
            self.data[field]=-f['val'] if field=='capex' else f['val'];self.data['financial_provenance'][field]={**f,'namespace':'us-gaap','concept':concept,'unit':'USD','value':f['val']}
        self.data['financial_source_receipt']=self.save(self.payload)
        receipt=self.save({'0':{'ticker':'TEST','title':'Test Company','cik_str':1}},'https://www.sec.gov/files/company_tickers.json')
        self.data['sec_issuer_mapping']={'cik':'0000000001','source_receipt':receipt}

    def tearDown(self):self.temp.cleanup()
    def save(self,payload,url=None,status=200):return save_financial_source(self.path,'1',SimpleNamespace(content=json.dumps(payload).encode(),status_code=status),AT,url)
    def compare(self):
        with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return compare_primary(db,self.data,None,AT)

    def test_exact_original_annual_facts_and_mapping(self):
        self.assertEqual(self.compare()['status'],'matched_original_annual_facts')
        with sqlite3.connect(self.path) as db:
            raw=db.execute('SELECT body_gzip FROM financial_source_documents WHERE id=?',(self.data['financial_source_receipt']['id'],)).fetchone()[0]
            self.assertEqual(json.loads(gzip.decompress(raw)),self.payload)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM financial_source_documents')

    def test_provider_only_fields_name_the_missing_primary_provenance(self):
        self.data['financial_provenance']['capex'] = {'method':'provider_period_label'}
        self.data['financial_provenance']['operating_cf'] = {}
        result=self.compare()
        self.assertEqual(result['status'],'mismatch')
        self.assertTrue(any(i.startswith('capex: no supported annual SEC concept') for i in result['issues']))
        self.assertTrue(any(i.startswith('operating_cf: no supported annual SEC concept') for i in result['issues']))
        self.assertNotIn("'namespace'",result['issues'])

    def test_directory_decode_shared_without_sharing_issuer_verdicts(self):
        cache={}
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            decompress=gzip.decompress
            with patch('financial_sources.gzip.decompress',wraps=decompress) as decode:
                for _ in range(2):
                    self.assertEqual(compare_primary(db,self.data,None,AT,mapping_cache=cache)['status'],'matched_original_annual_facts')
                self.assertEqual(decode.call_count,3) # each company's facts; directory once
            self.data['sec_issuer_mapping']['cik']='2'
            self.assertEqual(compare_primary(db,self.data,None,AT,mapping_cache=cache)['status'],'mismatch')

    def test_cached_directory_cannot_hide_changed_original_bytes(self):
        cache={}
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            self.assertEqual(compare_primary(db,self.data,None,AT,mapping_cache=cache)['status'],'matched_original_annual_facts')
            triggers=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='financial_source_documents'")]
            for trigger in triggers:db.execute('DROP TRIGGER "'+trigger+'"')
            identifier=self.data['sec_issuer_mapping']['source_receipt']['id']
            db.execute('UPDATE financial_source_documents SET body_gzip=? WHERE id=?',(gzip.compress(b'{}'),identifier))
            self.assertEqual(compare_primary(db,self.data,None,AT,mapping_cache=cache)['status'],'mismatch')

    def test_successful_proof_reuse_requires_identical_inputs_bytes_and_time(self):
        from financial_sources import PrimaryProofCache
        cache=PrimaryProofCache(limit=2)
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            proof=compare_primary(db,self.data,None,AT,proof_cache=cache)
            with patch('financial_sources.gzip.decompress',side_effect=AssertionError('Warm proof should verify compressed bytes')):
                self.assertEqual(compare_primary(db,self.data,None,AT,proof_cache=cache),proof)
            self.data['revenue']=101
            self.assertEqual(compare_primary(db,self.data,None,AT,proof_cache=cache)['status'],'mismatch')
            self.data['revenue']=100
            self.assertEqual(compare_primary(db,self.data,None,'2026-10-14T17:00:00+00:00',proof_cache=cache)['status'],'mismatch')
            # Reparse the same inputs for a different filing cutoff day.
            with patch('financial_sources.gzip.decompress',wraps=gzip.decompress) as decode:
                self.assertEqual(compare_primary(db,self.data,None,'2026-10-15T18:00:00+00:00',proof_cache=cache)['status'],'matched_original_annual_facts')
                self.assertEqual(decode.call_count,2)
            for trigger in [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='financial_source_documents'")]:db.execute('DROP TRIGGER "'+trigger+'"')
            db.execute('UPDATE financial_source_documents SET body_gzip=? WHERE id=?',(gzip.compress(b'{}'),self.data['financial_source_receipt']['id']))
            self.assertEqual(compare_primary(db,self.data,None,AT,proof_cache=cache)['status'],'mismatch')

    def test_proof_cache_does_not_share_mutable_results_or_grow_unbounded(self):
        from financial_sources import PrimaryProofCache
        cache=PrimaryProofCache(limit=2)
        for n in range(3):cache.put(str(n),{'status':'matched_original_annual_facts','issues':[]})
        self.assertIsNone(cache.get('0'));self.assertEqual(len(cache.values),2)
        result=cache.get('2');result['issues'].append('changed')
        self.assertEqual(cache.get('2')['issues'],[])

    def test_amount_period_currency_accession_and_issuer_tampering_block(self):
        original=json.loads(json.dumps(self.data))
        changes=[lambda d:d.update(revenue=101),lambda d:d.update(financial_period_end='2024-12-31'),
                 lambda d:d['financial_provenance']['capex'].update(unit='EUR'),lambda d:d['financial_provenance']['revenue'].update(accn='0000000002-26-000001'),
                 lambda d:d['sec_issuer_mapping'].update(cik='2'),lambda d:d.update(ticker='OTHER')]
        for change in changes:
            self.data=json.loads(json.dumps(original));change(self.data);self.assertEqual(self.compare()['status'],'mismatch')

    def test_missing_mapping_and_source_denial_are_not_certified(self):
        self.data['sec_issuer_mapping']={};self.assertEqual(self.compare()['status'],'mismatch')
        self.data['financial_source_receipt']=self.save({'error':'Denied'},status=403);self.assertEqual(self.compare()['status'],'mismatch')
        self.data['financial_source_receipt']={};self.assertEqual(self.compare()['status'],'missing_original')

    def test_future_filed_or_quarter_facts_cannot_match_annual_inputs(self):
        for mutate in [lambda f:f.update(filed='2027-01-01'),lambda f:f.update(start='2025-10-01')]:
            payload=json.loads(json.dumps(self.payload));mutate(payload['facts']['us-gaap']['Revenues']['units']['USD'][0]);self.data['financial_source_receipt']=self.save(payload)
            self.assertEqual(self.compare()['status'],'mismatch')

    def test_custody_digest_failure_blocks(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_financial_source_documents_UPDATE');db.execute("UPDATE financial_source_documents SET sha256='bad'")
        self.assertEqual(self.compare()['status'],'mismatch')

    def test_coverage_only_queues_authorized_eligible_portfolio_common_equities(self):
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE saved_list_versions(id INTEGER);INSERT INTO saved_list_versions VALUES(1);CREATE TABLE saved_list_items(version_id INTEGER,payload_json TEXT);CREATE TABLE paper_positions(ticker TEXT);CREATE TABLE research_refresh_attempts(ticker TEXT,attempted_at TEXT);')
            base={'status':'needs_evidence','identity':{'security_type':'CS'},'instrument_eligibility':{'status':'eligible'},'issues':['Missing facts']}
            for t in ['TEST','OUTSIDE','WARRANT','READY','STALEID']:
                item={**base,'ticker':t}
                if t=='WARRANT':item['identity']={'security_type':'WARRANT'}
                if t=='READY':item.update(status='documented_inputs',input_observed_at=AT,quote_as_of=AT,financial_period_end='2025-12-31',identity={'security_type':'CS','retrieved_at':AT,'source_date':'2026-10-14'})
                if t=='STALEID':item['instrument_eligibility']={'status':'needs_evidence'}
                db.execute('INSERT INTO saved_list_items VALUES(1,?)',(encoded(item),))
                if t!='OUTSIDE':db.execute('INSERT INTO paper_positions VALUES(?)',(t,))
            plan=coverage(db,AT)
        self.assertEqual(plan['scope_count'],4);self.assertEqual(plan['next_tickers'],['TEST']);self.assertEqual(plan['counts']['documented_inputs'],1)

    def test_original_download_matches_bytes_hash_and_rejects_corruption(self):
        app=FastAPI();app.include_router(make_research_router(self.path,lambda *a,**kw:None));client=TestClient(app)
        receipt=self.data['financial_source_receipt'];url='/api/research/coverage/sources/'+receipt['id']
        r=client.get(url);self.assertEqual(r.status_code,200);self.assertEqual(r.json(),self.payload)
        self.assertEqual(r.headers['X-Source-SHA256'],receipt['sha256']);self.assertEqual(client.get(url+'missing').status_code,404)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_financial_source_documents_UPDATE');db.execute("UPDATE financial_source_documents SET sha256='bad'")
        self.assertEqual(client.get(url).status_code,409)

    def test_refresh_worker_start_failure_does_not_leave_a_running_job(self):
        from research_store import init_research
        with sqlite3.connect(self.path) as db:db.executescript("CREATE TABLE company_analyses(ticker TEXT PRIMARY KEY);INSERT INTO company_analyses VALUES('TEST');")
        init_research(self.path);app=FastAPI();app.include_router(make_research_router(self.path,lambda *a,**kw:None));client=TestClient(app,raise_server_exceptions=False)
        worker=Mock();worker.start.side_effect=RuntimeError('Fixture thread failure')
        with patch('research_store.threading.Thread',return_value=worker):
            self.assertEqual(client.post('/api/research/refresh',json={'tickers':['TEST']}).status_code,500)
            self.assertEqual(client.post('/api/research/refresh',json={'tickers':['TEST']}).status_code,500)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM research_refresh_jobs WHERE state='running'").fetchone()[0],0)
