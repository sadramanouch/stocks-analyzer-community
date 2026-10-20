import json
import sqlite3
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta

from financial_integrity import SCHEMA_VERSION, extract_annual_financials
from research_store import (init_research, parse_directory, classify, rebuild_item, rebuild_list,
                            sync_identities, currently_fresh, make_research_router)
from ownership_history import init_ownership, parse_form4, save_form4, xml_url, make_ownership_router, refresh_issuer_index
from fastapi import FastAPI
from fastapi.testclient import TestClient

AT = '2026-10-13T18:00:00+00:00'
ACCESSION = '0001234567-26-000001'
URL = 'https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/xslF345X06/form4.xml'
XML = '''<ownershipDocument><documentType>4</documentType><issuer><issuerCik>0001234567</issuerCik><issuerTradingSymbol>TEST</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0009999999</rptOwnerCik><rptOwnerName>Fixture Officer</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer></reportingOwnerRelationship></reportingOwner><nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-10-08</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>10.5</value></transactionShares><transactionPricePerShare><value>20.25</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts><ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'''


def inputs():
    reference = {'title': 'Example Common Stock', 'security_type': 'CS', 'source_url': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', 'source': 'Nasdaq Trader official symbol directory', 'retrieved_at': AT, 'source_date': AT[:10], 'revision_id': 1}
    saved = {'ticker': 'TEST', 'company_name': 'Example', 'analyzed_at': '2020-01-01', 'fcf_yield': 1.0, 'quant_score': 99}
    data = dict(_fw_version=SCHEMA_VERSION, sector='Technology', industry='Software', price=10,
                market_cap=100_000_000, shares_outstanding=10_000_000, revenue=90_000_000,
                net_income=5_000_000, operating_cf=12_000_000, capex=-2_000_000, fcf=10_000_000,
                quote_as_of=AT, quote_currency='USD', financial_period_end='2026-06-30')
    data['financial_provenance'] = {field: {'end': '2026-06-30', 'filed': '2026-08-01', 'form': '10-K', 'accn': ACCESSION, 'value': abs(data[field]) if field == 'capex' else data[field], 'unit': 'USD'} for field in ('revenue','net_income','operating_cf','capex')}
    return saved, data, reference


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE company_analyses(ticker TEXT PRIMARY KEY,company_name TEXT,analyzed_at TEXT NOT NULL,fcf_yield REAL,quant_score INTEGER,pinned INTEGER DEFAULT 0); CREATE TABLE market_data_cache(ticker TEXT PRIMARY KEY,data_json TEXT,fetched_at TEXT); CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT,cik TEXT); CREATE TABLE filings(company_id INTEGER,form_type TEXT,url TEXT,accession_number TEXT,filed_date TEXT);')
            db.execute("INSERT INTO company_analyses(ticker,company_name,analyzed_at,fcf_yield,pinned) VALUES('TEST','Example','2020-01-01',1,1)")
        init_research(self.path); init_ownership(self.path)

    def tearDown(self): self.temp.cleanup()

    def test_rebuild_snapshot_allows_control_writes_and_preserves_read_inputs(self):
        _,data,_=inputs()
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE controls(paused INTEGER)')
            db.execute('INSERT INTO controls VALUES(0)')
            db.execute('INSERT INTO market_data_cache VALUES(?,?,?)',('TEST',json.dumps(data),AT))
        entered=threading.Event();release=threading.Event();errors=[]
        def slow_compare(*args,**kwargs):
            entered.set();release.wait(5)
            return {'status':'missing_original','issues':['Fixture source missing']}
        def build():
            try:rebuild_list(self.path)
            except Exception as error:errors.append(error)
        with patch('financial_sources.compare_primary',side_effect=slow_compare):
            worker=threading.Thread(target=build,daemon=True);worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with sqlite3.connect(self.path,timeout=.2) as db:
                    db.execute('UPDATE controls SET paused=1')
                    db.execute('UPDATE market_data_cache SET data_json=?',(json.dumps({**data,'price':20}),))
            finally:
                release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        with sqlite3.connect(self.path) as db:
            saved=json.loads(db.execute('SELECT payload_json FROM saved_list_items').fetchone()[0])
            self.assertEqual(saved['price'],10)
            self.assertEqual(json.loads(db.execute('SELECT data_json FROM market_data_cache').fetchone()[0])['price'],20)
            self.assertEqual(db.execute('SELECT paused FROM controls').fetchone()[0],1)

    def test_official_directory_classifies_alias_debt_and_ads_before_common_words(self):
        row = {'Security Name': 'Example', 'Test Issue': 'N', 'ETF': 'N'}
        for title, kind in [('4.5% Subordinated Debentures due 2060','debt'),('5.25% Subordinated Notes due 2061','debt'),('Depositary Shares representing Common Shares','depositary'),('Class A Ordinary Shares','CS'),('Common Stock','CS'),('Example Units','unit'),('Example Common Stock Warrants','warrant')]:
            self.assertEqual(classify({**row, 'Security Name': title}), kind)
        text = 'ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nAFGE|American Financial 4.5% Subordinated Debentures due 2060|N|AFGE|N|100|N|AFGE\nFile Creation Time: 10132026\n'
        self.assertEqual(parse_directory(text, 'otherlisted')['AFGE']['security_type'], 'debt')
        with self.assertRaises(ValueError): parse_directory(text.rsplit('File Creation', 1)[0], 'otherlisted')

    def test_inputs_require_identity_value_provenance_and_real_quote_time(self):
        saved, data, ref = inputs()
        result = rebuild_item(saved, (data, AT), ref, AT)
        self.assertEqual(result['status'], 'documented_inputs')
        self.assertEqual(result['fcf_yield'], .1)  # old clipped value 1.0 is not copied
        self.assertTrue(currently_fresh(result, AT))
        naive=rebuild_item(saved,(data,AT.replace('+00:00','')),ref,AT)
        self.assertEqual(naive['input_observed_at'],AT)
        for changed in [{**data, '_fw_version': 0}, {**data, 'quote_currency':'JPY'}, {**data, 'quote_as_of': None}, {**data, 'revenue': None}, {**data, 'fcf': 20_000_000}, {**data, 'financial_provenance': {}}]:
            self.assertEqual(rebuild_item(saved, (changed, AT), ref, AT)['status'], 'needs_evidence')
        mismatch = json.loads(json.dumps(data)); mismatch['financial_provenance']['revenue']['value'] = 1
        self.assertEqual(rebuild_item(saved, (mismatch, AT), ref, AT)['status'], 'needs_evidence')
        self.assertEqual(rebuild_item(saved, (data, AT), None, AT)['status'], 'needs_evidence')
        self.assertEqual(rebuild_item(saved, (data, AT), {**ref, 'security_type': 'debt'}, AT)['status'], 'excluded')
        self.assertFalse(currently_fresh(result, '2026-10-16T18:00:00Z'))
        foreign={**data,'financial_provenance':{},'financial_currency':'JPY'}
        self.assertIsNone(rebuild_item(saved,(foreign,AT),ref,AT)['fcf_yield'])

    def test_reviewed_statements_require_matching_signed_fields_and_fix_only_period_label(self):
        saved,data,ref=inputs()
        record={'period_end':'2026-06-27','source_url':'https://issuer.example/annual', 'statement_basis':'Annual primary statement', 'reviewed_on':'2026-10-13','method':'Independent comparison', 'reported_precision_usd':1000,
                'fields':{k:abs(data[k]) if k=='capex' else data[k] for k in ('revenue','net_income','operating_cf','capex')}}
        data['financial_provenance']={}
        result=rebuild_item(saved,(data,AT),ref,AT,record)
        self.assertEqual(result['status'],'documented_inputs');self.assertEqual(result['financial_period_end'],'2026-06-27')
        self.assertEqual(result['provider_period_label'],'2026-06-30')
        data['net_income']=-data['net_income']
        self.assertEqual(rebuild_item(saved,(data,AT),ref,AT,record)['status'],'needs_evidence')

    def test_bad_legacy_cache_is_quarantined_without_breaking_full_list(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO market_data_cache VALUES(?,?,?)',('TEST','invalid json',AT))
        edition=rebuild_list(self.path)
        with sqlite3.connect(self.path) as db:
            item=json.loads(db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=?',(edition['version_id'],)).fetchone()[0])
        self.assertEqual(item['status'],'needs_evidence')
        saved,data,ref=inputs();data['_fw_version']=0;data['revenue']=float('nan')
        json.dumps(rebuild_item(saved,(data,AT),ref,AT),allow_nan=False)

    def test_all_legacy_write_paths_archived_and_editions_cannot_be_modified(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE company_analyses SET fcf_yield=.05 WHERE ticker='TEST'")
            db.execute("INSERT OR REPLACE INTO company_analyses(ticker,analyzed_at) VALUES('TEST','2026-10-13')")
            db.execute("DELETE FROM company_analyses WHERE ticker='TEST'")
            rows = db.execute('SELECT operation,row_json FROM legacy_analysis_revisions ORDER BY id').fetchall()
            self.assertEqual([r[0] for r in rows], ['legacy-baseline','update','insert','delete'])
            self.assertEqual(json.loads(rows[0][1])['pinned'], 1)
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM legacy_analysis_revisions')
        edition = rebuild_list(self.path)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('UPDATE saved_list_versions SET basis=? WHERE id=?', ('fake',edition['version_id']))
        init_research(self.path)
        self.assertEqual(rebuild_list(self.path)['version_id'], edition['version_id'] + 1)

    def test_source_failures_do_not_replace_or_freshen_previous_identity(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO identity_batches VALUES('old','2020-01-01','{}')")
        response = Mock(); response.raise_for_status.side_effect = ValueError('unavailable')
        with self.assertRaises(ValueError): sync_identities(self.path, fetch=lambda *a, **kw: response)
        with sqlite3.connect(self.path) as db: self.assertEqual(db.execute('SELECT retrieved_at FROM identity_batches').fetchone()[0], '2020-01-01')

    def test_form4_exact_decimals_canonical_identity_dates_and_duplicate_import(self):
        result = parse_form4(XML, ACCESSION, URL, '2026-10-09', AT)
        event = result['events'][0]
        self.assertEqual(event['reported_shares'], '10.5'); self.assertEqual(event['reported_price'], '20.25')
        self.assertEqual(event['amount_usd'], 212.625)
        self.assertEqual(event['canonical_id'], ACCESSION + ':sec:9999999:nonDerivative:1')
        self.assertEqual(event['available_at'], AT)
        self.assertEqual(event['source_verification'], 'primary_parsed')
        first = save_form4(self.path, XML, ACCESSION, URL, '2026-10-09', AT, 'TEST', '1234567')
        second = save_form4(self.path, XML, ACCESSION, URL, '2026-10-09', AT, 'TEST', '1234567')
        self.assertEqual(first['inserted'], 1); self.assertTrue(second['duplicate'])
        with self.assertRaises(ValueError): save_form4(self.path, XML.replace('20.25','21'), ACCESSION, URL, '2026-10-09', AT)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM ownership_documents')

    def test_form4_footnotes_plans_amendments_and_derivatives_need_review(self):
        variants = [XML.replace('<documentType>4</documentType>', '<documentType>4/A</documentType>'),
                    XML.replace('<issuer>', '<aff10b5One>1</aff10b5One><issuer>'),
                    XML.replace('nonDerivative', 'derivative'),
                    XML.replace('</transactionShares>', '<footnoteId id="F1"/></transactionShares>').replace('</ownershipDocument>', '<footnotes><footnote id="F1">Includes restricted shares</footnote></footnotes></ownershipDocument>')]
        for xml in variants:
            self.assertEqual(parse_form4(xml,ACCESSION,URL,'2026-10-09',AT)['events'][0]['source_verification'], 'needs_filing_review')

    def test_award_after_filing_is_preserved_as_unconfirmed_context_never_a_purchase(self):
        xml=XML.replace('2026-10-08','2026-10-12').replace('<transactionCode>P</transactionCode>','<transactionCode>A</transactionCode>')
        event=parse_form4(xml,ACCESSION,URL,'2026-10-09',AT)['events'][0]
        self.assertEqual(event['side'],'other');self.assertEqual(event['date_status'],'unconfirmed_award')
        self.assertEqual(event['source_verification'],'needs_filing_review')
        self.assertEqual(event['traded_at'][:10],'2026-10-12');self.assertEqual(event['disclosed_at'][:10],'2026-10-09')
        self.assertIn('unconfirmed',event['traded_at_display'])
        with self.assertRaises(ValueError):parse_form4(xml.replace('<transactionCode>A</transactionCode>','<transactionCode>P</transactionCode>'),ACCESSION,URL,'2026-10-09',AT)

    def test_form4_rejects_wrong_issuer_dates_amounts_entities_and_unresolved_footnotes(self):
        for xml in [XML.replace('2026-10-08','2026-10-10'), XML.replace('10.5','NaN'), XML.replace('10.5','-1'), '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'+XML,
                    XML.replace('</transactionShares>', '<footnoteId id="missing"/></transactionShares>'), XML.replace('<value>A</value>', '<value>D</value>')]:
            with self.assertRaises(ValueError): parse_form4(xml,ACCESSION,URL,'2026-10-09',AT)
        with self.assertRaises(ValueError): save_form4(self.path,XML,ACCESSION,URL,'2026-10-09',AT,'OTHER')
        for url in ['https://evil.example/form4.xml', URL+'?redirect=evil', URL.replace('000123456726000001','000123456726000002')]:
            with self.assertRaises(ValueError): xml_url(url,ACCESSION)

    def test_real_research_routes_version_filter_history_and_rebuild(self):
        app=FastAPI(); app.include_router(make_research_router(self.path, lambda *a,**kw: {})); app.include_router(make_ownership_router(self.path))
        client=TestClient(app)
        edition=client.post('/api/research/rebuild').json()
        overview=client.get('/api/research').json()
        self.assertEqual(overview['counts'], {'needs_evidence':1})
        self.assertEqual(overview['version'], edition['version_id'])
        self.assertEqual(client.get('/api/research?status=documented_inputs').json()['items'], [])
        self.assertEqual(client.get('/api/research/history/TEST').json()['legacy_changes'][0]['legacy_analysis']['fcf_yield'],1)
        self.assertEqual(client.post('/api/research/refresh',json={'tickers':['NOT-SAVED']}).status_code,422)
        self.assertFalse(client.get('/api/trade-history').json()['complete_history'])

    def test_blocked_sec_history_retains_index_and_never_creates_trades(self):
        class InlineThread:
            def __init__(self,target,args,**kw): self.target,self.args=target,args
            def start(self): self.target(*self.args)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
            db.execute('INSERT INTO filings VALUES(?,?,?,?,?)',(1,'4',URL,ACCESSION,'2026-10-09'))
        app=FastAPI();app.include_router(make_ownership_router(self.path));client=TestClient(app)
        response=Mock();response.status_code=403
        with patch('ownership_history.threading.Thread',InlineThread),patch('ownership_history.requests.get',return_value=response):
            self.assertEqual(client.post('/api/trade-history/sec/sync',json={'ticker':'TEST'}).status_code,200)
        status=client.get('/api/trade-history?ticker=TEST').json()
        self.assertEqual(status['runs'][0]['state'],'blocked');self.assertEqual(status['indexed_filings'],1)
        self.assertEqual(status['parsed_transaction_lines'],0)

    def test_refresh_failure_retains_saved_rows_and_interrupted_job_resumes(self):
        class InlineThread:
            def __init__(self,target,args,**kw): self.target,self.args=target,args
            def start(self): self.target(*self.args)
        app=FastAPI();app.include_router(make_research_router(self.path,Mock(side_effect=ValueError('provider unavailable'))));client=TestClient(app)
        with patch('research_store.threading.Thread',InlineThread):
            first=client.post('/api/research/refresh',json={'tickers':['TEST']})
        self.assertEqual(first.status_code,200)
        overview=client.get('/api/research').json()
        self.assertEqual(overview['refresh_job']['failed'],1);self.assertEqual(overview['items'][0]['legacy_fcf_yield'],1)
        with sqlite3.connect(self.path) as db:
            job_id=db.execute("INSERT INTO research_refresh_jobs(started_at,updated_at,state,tickers_json) VALUES(?,?,'interrupted',?)",(AT,AT,'["TEST"]')).lastrowid
        with patch('research_store.threading.Thread',InlineThread):
            self.assertEqual(client.post(f'/api/research/refresh/resume/{job_id}').status_code,200)
        self.assertEqual(client.post('/api/research/refresh/resume/999999').status_code,404)

    def test_current_sec_index_adds_amendments_without_erasing_old_history(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
            db.execute('INSERT INTO filings VALUES(?,?,?,?,?)',(1,'4',URL,ACCESSION,'2026-10-09'))
        amended='0001234567-26-000002'
        payload={'cik':'0001234567','tickers':['TEST'],'filings':{'recent':{'form':['4/A'],'accessionNumber':[amended],'filingDate':['2026-10-10'],'primaryDocument':['form4.xml']},'files':[{'name':'CIK0001234567-submissions-001.json'}]}}
        response=Mock(status_code=200,content=b'primary SEC metadata');response.json.return_value=payload
        result=refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response)
        self.assertEqual(result['added'],1)
        self.assertEqual(refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response)['added'],0)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM filings').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM filings WHERE form_type='4/A'").fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM ownership_index_snapshots')
        payload['cik']='wrong-issuer'
        with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response)

    def test_sec_index_rejects_bad_lengths_future_dates_and_wrong_document_urls(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        for changes in [{'form':['4','4']},{'filingDate':['2099-01-01']},{'primaryDocument':['../evil.xml']}]:
            recent={'form':['4'],'accessionNumber':[ACCESSION],'filingDate':['2026-10-09'],'primaryDocument':['form4.xml'],**changes}
            response=Mock(status_code=200,content=b'fixture');response.json.return_value={'cik':'1234567','tickers':['TEST'],'filings':{'recent':recent}}
            with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM filings').fetchone()[0],0)

    def test_missing_issuer_registered_only_after_matching_current_sec_record(self):
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE companies ADD COLUMN name TEXT')
            db.execute('ALTER TABLE companies ADD COLUMN last_updated TEXT')
        response=Mock(status_code=200,content=b'current issuer submissions')
        response.json.return_value={'cik':'1234567','name':'Current Example','tickers':['OTHER'],
                                   'filings':{'recent':{'form':['4'],'accessionNumber':[ACCESSION],'filingDate':['2026-10-09'],'primaryDocument':['form4.xml']}}}
        resolver=Mock(return_value={'cik':'0001234567','name':'Ticker map example'})
        with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response,resolver)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],0)
        response.json.return_value['tickers']=['TEST']
        self.assertEqual(refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response,resolver)['added'],1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT ticker,cik,name FROM companies').fetchone(),('TEST','1234567','Current Example'))
        resolver.reset_mock()
        self.assertEqual(refresh_issuer_index(self.path,'TEST',lambda *a,**kw:response,resolver)['added'],0)
        resolver.assert_not_called()

    def test_missing_issuer_lookup_failure_cannot_report_success_or_create_records(self):
        from fastapi import HTTPException
        class InlineThread:
            def __init__(self,target,args,**kw):self.target,self.args=target,args
            def start(self):self.target(*self.args)
        app=FastAPI();app.include_router(make_ownership_router(self.path,Mock(side_effect=HTTPException(503,'Source unavailable'))))
        client=TestClient(app)
        with patch('ownership_history.threading.Thread',InlineThread):
            self.assertEqual(client.post('/api/trade-history/sec/sync',json={'ticker':'TEST'}).status_code,200)
        overview=client.get('/api/trade-history').json()
        self.assertEqual(overview['runs'][0]['state'],'blocked')
        self.assertEqual(overview['parsed_documents'],0)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],0)

    def test_actor_coverage_never_fabricates_net_or_copy_returns(self):
        save_form4(self.path,XML,ACCESSION,URL,'2026-10-09',AT)
        app=FastAPI();app.include_router(make_ownership_router(self.path));client=TestClient(app)
        actor=client.get('/api/trade-history/actors?ticker=TEST').json()['actors'][0]
        self.assertEqual(actor['actor_id'],'sec:9999999');self.assertEqual(actor['transaction_codes'],{'P':1})
        self.assertIsNone(actor['net_return']);self.assertIsNone(actor['copy_return'])
        self.assertEqual(actor['performance_status'],'unverified');self.assertEqual(len(actor['blocking_reasons']),3)

    def test_historical_index_backfill_is_bounded_resumable_and_archives_raw_bytes(self):
        import hashlib
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        descriptors=[{'name':f'CIK0001234567-submissions-{n:03}.json','filingFrom':f'202{n}-01-01','filingTo':f'202{n}-12-31','filingCount':1} for n in (1,2)]
        current={'cik':'1234567','tickers':['TEST'],'filings':{'recent':{'form':[],'accessionNumber':[],'filingDate':[],'primaryDocument':[]},'files':descriptors}}
        historical={d['name']:{'form':['4'],'accessionNumber':[f'0001234567-2{n}-000001'],'filingDate':[f'202{n}-06-01'],'primaryDocument':['form4.xml']} for n,d in zip((1,2),descriptors)}
        calls=[]
        def fetch(url,**kw):
            calls.append(url)
            payload=current if url.endswith('CIK0001234567.json') else historical[url.rsplit('/',1)[1]]
            return Mock(status_code=200,content=json.dumps(payload).encode(),json=Mock(return_value=payload))
        with patch('time.sleep'):
            first=refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
            second=refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
            third=refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
        self.assertEqual([first['added'],second['added'],third['added']],[1,1,0])
        self.assertEqual(sum('submissions-' in url for url in calls),2)
        with sqlite3.connect(self.path) as db:
            for raw,sha in db.execute('SELECT document_json,sha256 FROM ownership_index_documents'):
                self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),sha)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM ownership_index_documents')
        app=FastAPI();app.include_router(make_ownership_router(self.path));coverage=TestClient(app).get('/api/trade-history').json()
        self.assertEqual(coverage['index_archives'][0]['older_files_archived'],2)
        self.assertFalse(coverage['complete_history']);self.assertEqual(coverage['parsed_documents'],0)

    def test_historical_manifest_cannot_request_another_issuer_or_path(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        for name in ('../credentials.json','CIK0007654321-submissions-001.json','https://evil.example/data.json'):
            payload={'cik':'1234567','tickers':['TEST'],'filings':{'recent':{'form':[],'accessionNumber':[],'filingDate':[],'primaryDocument':[]},'files':[{'name':name,'filingFrom':'2020-01-01','filingTo':'2020-12-31','filingCount':1}]}}
            fetch=Mock(return_value=Mock(status_code=200,content=json.dumps(payload).encode(),json=Mock(return_value=payload)))
            with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=2)
            self.assertEqual(fetch.call_count,1)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ownership_index_documents').fetchone()[0],0)

    def test_old_index_mismatched_count_or_dates_never_persist_and_legacy_text_is_explicit(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        descriptor={'name':'CIK0001234567-submissions-001.json','filingFrom':'2001-01-01','filingTo':'2001-12-31','filingCount':1}
        base={'cik':'1234567','tickers':['TEST'],'filings':{'recent':{'form':[],'accessionNumber':[],'filingDate':[],'primaryDocument':[]},'files':[descriptor]}}
        old={'form':['4'],'accessionNumber':['0001234567-01-000001'],'filingDate':['2002-01-01'],'primaryDocument':['old.txt']}
        def fetch(url,**kw):
            payload=base if url.endswith('CIK0001234567.json') else old
            return Mock(status_code=200,content=json.dumps(payload).encode(),json=Mock(return_value=payload))
        with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
        old['filingDate']=['2001-06-01'];descriptor['filingCount']=2
        with self.assertRaises(ValueError):refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
        descriptor['filingCount']=1
        with patch('time.sleep'):refresh_issuer_index(self.path,'TEST',fetch,older_files_limit=1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM filings').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT unsupported_references FROM ownership_index_documents WHERE kind='historical'").fetchone()[0],1)

    def test_actor_search_and_pages_cover_all_owners_without_duplicates(self):
        for n in range(25):
            xml=XML.replace('0009999999',str(9000000+n)).replace('Fixture Officer',f'Owner {n:02}')
            save_form4(self.path,xml,f'0001234567-26-{n+1:06}',URL.replace('000123456726000001',f'000123456726{n+1:06}'),'2026-10-09',AT)
        app=FastAPI();app.include_router(make_ownership_router(self.path));client=TestClient(app)
        a=client.get('/api/trade-history/actors?limit=20').json();b=client.get('/api/trade-history/actors?offset=20&limit=20').json()
        self.assertEqual(a['total'],25);self.assertEqual(len(a['actors'])+len(b['actors']),25)
        self.assertFalse(set(x['actor_id'] for x in a['actors']) & set(x['actor_id'] for x in b['actors']))
        searched=client.get('/api/trade-history/actors?search=Owner%2024').json()
        self.assertEqual(searched['total'],1);self.assertEqual(searched['actors'][0]['name'],'Owner 24')

    def test_unexpected_import_failure_is_not_success_and_does_not_lock_future_jobs(self):
        class InlineThread:
            def __init__(self,target,args,**kw):self.target,self.args=target,args
            def start(self):self.target(*self.args)
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO companies VALUES(1,'TEST','1234567')")
        app=FastAPI();app.include_router(make_ownership_router(self.path));client=TestClient(app)
        with patch('ownership_history.threading.Thread',InlineThread),patch('ownership_history.requests.get',side_effect=RuntimeError('Unexpected failure')):
            for _ in range(2):
                self.assertEqual(client.post('/api/trade-history/sec/sync',json={'ticker':'TEST'}).status_code,200)
        runs=client.get('/api/trade-history').json()['runs']
        self.assertEqual(len(runs),2);self.assertTrue(all(r['state']=='blocked' for r in runs))

    def test_original_published_apple_filing_is_planned_sale_not_buy_signal(self):
        xml=(Path(__file__).parent/'fixtures/sec-aapl-0001140361-26-017175.xml').read_text()
        source='https://www.sec.gov/Archives/edgar/data/320193/000114036126017175/form4.xml'
        event=parse_form4(xml,'0001140361-26-017175',source,'2026-04-27',AT)['events'][0]
        self.assertEqual(event['actor_id'],'sec:2050912');self.assertEqual(event['reported_shares'],'1534')
        self.assertEqual(event['reported_price'],'275');self.assertEqual(event['amount_usd'],421850)
        self.assertEqual(event['transaction_code'],'S');self.assertEqual(event['trading_plan'],'true')
        self.assertEqual(event['source_verification'],'needs_filing_review')

    def test_sec_amount_units_cannot_inherit_the_last_shares_iterator(self):
        fact={'val':100,'start':'2025-01-01','end':'2025-12-31','filed':'2026-02-01','form':'10-K','accn':ACCESSION}
        payload={'cik':1234567,'facts':{'us-gaap':{'Revenue':{'units':{'USD':[fact]}}},'dei':{'EntityCommonStockSharesOutstanding':{'units':{'shares':[{k:v for k,v in fact.items() if k!='start'}]}}}}}
        result=extract_annual_financials(payload,{'revenue':['Revenue']},['EntityCommonStockSharesOutstanding'])
        self.assertEqual(result['_provenance']['revenue']['unit'],'USD')
        self.assertEqual(result['_provenance']['shares']['unit'],'shares')
        self.assertEqual(result['_schema_version'],5)


if __name__ == '__main__': unittest.main()
