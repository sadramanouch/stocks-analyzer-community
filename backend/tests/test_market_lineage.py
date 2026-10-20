"""Explicit fixtures; no test prices or account events are production evidence."""
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from market_history import init_market_history,latest_histories,compare_position,make_market_router
from market_lineage import (LINKS,METHOD,sha,init_lineage,reproduce,replay,linked_histories,
                            capture,make_lineage_router,download)
from market_provenance import save_source
from research_store import encoded
from test_market_provenance import packet

AT='2026-10-13T23:05:00+00:00'
class SymbolLineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.db';init_market_history(self.path)
        self.spec=LINKS['SCVL'];s=self.spec
        self.positions=[dict(id=1,ticker='SCVL',entry_date='2026-10-05',close_date='2026-10-07')]
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE paper_positions(id INTEGER PRIMARY KEY,ticker TEXT,entry_date TEXT,close_date TEXT)')
            db.execute('INSERT INTO paper_positions VALUES(1,?,?,?)',('SCVL','2026-10-05','2026-10-07'))
        notice=('<html><body><p>Explicit fixture '+' '.join(s['anchors'])+'</p></body></html>').encode()
        index=encoded({'cik':s['cik'],'tickers':['SHOE'],'exchanges':['Nasdaq'],'filings':{'recent':{'accessionNumber':[s['accession']],'form':['8-K'],'filingDate':[s['filing_date']]}}}).encode()
        data=packet();data['chart']['result'][0]['meta'].update(symbol='SHOE',exchangeName='NMS')
        params={'period1':1791172800,'period2':1791943200,'interval':'1d','includePrePost':False,'events':'div,splits,capitalGains'}
        # Request timestamps are exchange-local midnights, not UTC dates.
        from zoneinfo import ZoneInfo
        ny=ZoneInfo('America/New_York')
        params.update(period1=int(datetime(2026,10,5,tzinfo=ny).timestamp()),period2=int(datetime(2026,10,14,tzinfo=ny).timestamp()))
        self.records={
          'notice':dict(ticker='SCVL',url=s['notice_url'],kind='sec_symbol_notice',body=notice,request={}),
          'index':dict(ticker='SCVL',url='https://data.sec.gov/submissions/CIK'+s['cik']+'.json',kind='sec_symbol_submissions',body=index,request={}),
          'chart':dict(ticker='SHOE',url='https://query2.finance.yahoo.com/v8/finance/chart/SHOE',kind='yahoo_original_chart',body=encoded(data).encode(),request=params)}
        self.inputs={'methodology':METHOD,'ticker':'SCVL','provider_symbol':'SHOE','captured_at':AT,'start_date':'2026-10-05','end_date':'2026-10-13','positions':self.positions}
        for key,r in self.records.items():self.replace_source(key,r['body'])
    def tearDown(self):self.tmp.cleanup()
    def replace_source(self,key,body,**changes):
        r={**self.records[key],**changes};r['body']=body;self.records[key]=r
        self.inputs[key+'_id']=save_source(self.path,r['ticker'],r['url'],r['kind'],r.get('status',200),r['request'],body,r.get('at','2026-10-13T23:00:00+00:00'))
    def report(self):
        with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return reproduce(db,self.inputs)
    def store(self):
        report=self.report();raw=encoded(self.inputs);identifier=sha(raw);result=encoded(report)
        with sqlite3.connect(self.path) as db:db.execute('INSERT INTO market_lineage_histories VALUES(?,?,?,?,?,?,?,?,?)',(identifier,'SCVL','SHOE',AT,self.inputs['start_date'],self.inputs['end_date'],raw,result,sha(result)))
        return identifier
    def test_exact_notice_index_and_chart_replay_without_relabeling_provider(self):
        r=self.report();self.assertEqual(r['ticker'],'SCVL');self.assertEqual(r['history']['metadata']['symbol'],'SHOE')
        self.assertFalse(r['account_return_verified']);self.assertFalse(r['available_for_trading_decisions']);self.assertFalse(r['independent_prices_verified'])
        identifier=self.store()
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM market_lineage_histories').fetchone();self.assertEqual(replay(db,row)['id'],identifier)
    def test_linked_series_are_excluded_from_default_copy_and_decision_history_universe(self):
        self.store()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(latest_histories(db),{})
            self.assertEqual(set(latest_histories(db,include_linked=True)),{'SCVL'})
    def test_position_prices_are_only_reference_ratios_and_symbol_proof_is_visible(self):
        self.store()
        with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;doc=linked_histories(db)['SCVL']
        p=dict(id=1,ticker='SCVL',status='closed',entry_date='2026-10-05',close_date='2026-10-07',entry_price=100,close_price=102,realized_return_pct=2)
        r=compare_position(p,doc);self.assertEqual(r['provider_symbol'],'SHOE');self.assertFalse(r['account_return_verified']);self.assertAlmostEqual(r['reference_price_return_pct'],2)
    def test_notice_without_unchanged_cusip_is_not_enough(self):
        self.replace_source('notice',self.records['notice']['body'].replace(b'There will be no change',b'There may be a change'))
        with self.assertRaisesRegex(ValueError,'continuity notice'):self.report()
    def test_hidden_scripts_cannot_supply_notice_anchors(self):
        self.replace_source('notice',('<html><script>'+' '.join(self.spec['anchors'])+'</script><p>Unrelated</p></html>').encode())
        with self.assertRaises(ValueError):self.report()
    def test_hidden_visible_markup_cannot_supply_continuity_notice(self):
        self.replace_source('notice',('<html><div style="display: none">'+' '.join(self.spec['anchors'])+'</div><p>Unrelated</p></html>').encode())
        with self.assertRaises(ValueError):self.report()
    def test_single_sec_exhibit_sgml_envelope_is_explicitly_supported(self):
        body=b'<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>fixture.htm\n<DESCRIPTION>Explicit fixture\n<TEXT>\n'+self.records['notice']['body']+b'\n</TEXT>\n</DOCUMENT>\n'
        self.replace_source('notice',body);self.assertEqual(self.report()['continuity'],'issuer_reported_unchanged_cusip')
    def test_multiple_or_other_exhibit_envelopes_are_not_recovered_as_notice(self):
        body=b'<DOCUMENT>\n<TYPE>EX-99.2\n<SEQUENCE>2\n<FILENAME>fixture.htm\n<DESCRIPTION>Explicit fixture\n<TEXT>\n'+self.records['notice']['body']+b'\n</TEXT>\n</DOCUMENT>\n'
        self.replace_source('notice',body)
        with self.assertRaisesRegex(ValueError,'envelope'):self.report()
    def test_wrong_symbol_currency_exchange_and_issuer_fail_closed(self):
        for key,value in [('symbol','SCVL'),('currency','CAD'),('exchangeName','NYQ')]:
            with self.subTest(key=key):
                original=self.records['chart']['body'];data=json.loads(original);data['chart']['result'][0]['meta'][key]=value;self.replace_source('chart',encoded(data).encode())
                with self.assertRaises(ValueError):self.report()
                self.replace_source('chart',original)
        data=json.loads(self.records['index']['body']);data['cik']='0000000001';self.replace_source('index',encoded(data).encode())
        with self.assertRaisesRegex(ValueError,'issuer'):self.report()
    def test_index_accession_and_filing_date_cannot_be_substituted(self):
        data=json.loads(self.records['index']['body']);data['filings']['recent']['filingDate']=['2026-06-12'];self.replace_source('index',encoded(data).encode())
        with self.assertRaisesRegex(ValueError,'accession'):self.report()
    def test_timestamp_timezone_future_and_stale_sources_fail_closed(self):
        original=self.inputs['captured_at']
        for stamp in ('2026-10-13T23:05:00','2026-10-13T22:00:00+00:00','2026-10-22T23:00:00+00:00'):
            self.inputs['captured_at']=stamp
            with self.assertRaises(ValueError):self.report()
        self.inputs['captured_at']=original
    def test_exact_close_endpoint_is_required(self):
        self.inputs['positions'][0]['close_date']='2026-10-08'
        with self.assertRaisesRegex(ValueError,'Exact original'):self.report()
    def test_no_adjacent_entry_date_substitution(self):
        self.inputs['positions'][0]['entry_date']='2026-10-04';self.inputs['start_date']='2026-10-04'
        with self.assertRaises(ValueError):self.report()
    def test_chart_request_scope_is_not_inferred_from_response_bars(self):
        self.records['chart']['request']['includePrePost']=True;self.replace_source('chart',self.records['chart']['body'])
        with self.assertRaisesRegex(ValueError,'bounds'):self.report()
    def test_unmodeled_capital_gain_actions_are_not_dropped(self):
        data=json.loads(self.records['chart']['body']);data['chart']['result'][0]['events']={'capitalGains':{'test':{'amount':5}}};self.replace_source('chart',encoded(data).encode())
        with self.assertRaisesRegex(ValueError,'Capital-gain'):self.report()
    def test_report_and_original_bytes_cannot_be_modified(self):
        self.store()
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM market_lineage_histories')
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET body_bytes=X'00' WHERE id=?",(self.inputs['notice_id'],))
            db.row_factory=sqlite3.Row
            with self.assertRaisesRegex(ValueError,'fingerprint'):linked_histories(db)
    def test_idempotent_capture_returns_saved_report_without_network_or_credentials(self):
        identifier=self.store()
        with patch('market_lineage.download',side_effect=AssertionError('No new network')):
            self.assertEqual(capture(self.path,'SCVL')['id'],identifier)
    def test_unknown_ticker_and_extra_client_prices_are_rejected(self):
        app=FastAPI();app.include_router(make_lineage_router(self.path));client=TestClient(app)
        self.assertEqual(client.post('/api/market-history/lineage/ARBEW/capture',json={}).status_code,409)
        self.assertEqual(client.post('/api/market-history/lineage/SCVL/capture',json={'price':1}).status_code,422)
        self.assertEqual(client.get('/api/market-history/lineage/missing').status_code,404)
    def test_download_denial_is_retained_and_stops(self):
        class Response:
            status_code=403
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,size):return iter([b'Access denied'])
        class Client:
            def get(self,*args,**kwargs):return Response()
        with self.assertRaisesRegex(ValueError,'remaining requests stopped'):
            download(self.path,'SCVL',self.spec['notice_url'],'sec_symbol_notice',{},Client())
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM market_source_documents WHERE status_code=403').fetchone()[0],1)
    def test_saved_report_route_replays_and_tampering_returns_conflict(self):
        identifier=self.store();app=FastAPI();app.include_router(make_lineage_router(self.path));client=TestClient(app)
        self.assertEqual(client.get('/api/market-history/lineage/'+identifier).status_code,200)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_market_lineage_histories_UPDATE');db.execute("UPDATE market_lineage_histories SET report_sha256='bad'")
        self.assertEqual(client.get('/api/market-history/lineage/'+identifier).status_code,409)
        self.assertEqual(client.get('/api/market-history/lineage').status_code,409)
    def test_shared_history_document_route_resolves_linked_comparison_ids(self):
        identifier=self.store();app=FastAPI();app.include_router(make_market_router(self.path));client=TestClient(app)
        r=client.get('/api/market-history/documents/'+identifier);self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['history']['metadata']['symbol'],'SHOE');self.assertEqual(r.json()['ticker'],'SCVL')
        self.assertEqual(r.json()['symbol_lineage']['id'],identifier)
        self.assertEqual(sha(encoded(r.json()['history'])),r.json()['sha256']);self.assertFalse(r.json()['available_for_trading_decisions'])
        self.assertEqual(client.get('/api/market-history/documents/not-archived').status_code,404)

if __name__=='__main__':unittest.main()
