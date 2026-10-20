import copy,hashlib,json,sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from market_history import init_market_history,fetch_history,validate_history,compare_position,make_market_router
from market_provenance import CALENDAR,calendar_check,save_source,source_references,fetch_original_chart
from yfinance.exceptions import YFTzMissingError,YFRateLimitError

AT='2026-10-13T23:00:00+00:00'
def packet():
    stamps=[int(datetime(2026,10,d,13,30,tzinfo=timezone.utc).timestamp()) for d in (5,6,7)]
    return {'chart':{'error':None,'result':[{'meta':{'symbol':'TEST','currency':'USD','instrumentType':'EQUITY','exchangeTimezoneName':'America/New_York'},
        'timestamp':stamps,'indicators':{'quote':[{'open':[100,101,102],'high':[101,102,103],'low':[99,100,101],'close':[100,101,102]}],'adjclose':[{'adjclose':[100,101,102]}]},'events':{}}]}}
def response(data,status=200):
    r=requests.Response();r.status_code=status;r._content=json.dumps(data,separators=(',',':')).encode();r.url='https://query2.finance.yahoo.com/v8/finance/chart/TEST';return r

class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';init_market_history(self.path)
    def tearDown(self):self.tmp.cleanup()
    def test_original_bytes_and_hash_are_retained_and_immutable(self):
        raw=b'<p>Original\r\nsource \xe2\x80\x94 bytes</p>'
        a=save_source(self.path,'TEST','https://example.test/primary','primary_action',200,{'ratio':[1,20]},raw,AT)
        self.assertEqual(a,save_source(self.path,'TEST','https://example.test/primary','primary_action',200,{'ratio':[1,20]},raw,AT))
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT sha256,body_bytes FROM market_source_documents').fetchone()
            self.assertEqual(row,(hashlib.sha256(raw).hexdigest(),raw))
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM market_source_documents')
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET body_bytes=X'00'")
            with self.assertRaises(ValueError):source_references(db)
    def test_historical_range_can_be_requested_without_current_timezone_metadata(self):
        r=response(packet());client=SimpleNamespace(get=lambda *args,**kwargs:r)
        fake=SimpleNamespace(_data=client,history=lambda **kw:(_ for _ in ()).throw(YFTzMissingError('TEST')))
        with patch('market_history.yf.Ticker',return_value=fake):
            p=fetch_history('TEST','2026-10-05','2026-10-13',AT,db_path=self.path)
        self.assertEqual(len(p['bars']),3);self.assertEqual(p['bars'][0]['date'],'2026-10-05')
        self.assertTrue(p['original_source_document_id'])
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT body_bytes FROM market_source_documents').fetchone()[0],r.content)
    def test_source_timezone_currency_and_symbol_are_not_inferred_from_request(self):
        for k,v in [('exchangeTimezoneName','Asia/Tokyo'),('symbol','OTHER'),('currency','EUR')]:
            p=packet();p['chart']['result'][0]['meta'][k]=v
            fake=SimpleNamespace(_data=SimpleNamespace(get=lambda *a,**kw:response(p)))
            with patch('market_history.yf.Ticker',return_value=fake),self.assertRaises(ValueError):fetch_history('TEST','2026-10-05','2026-10-13',AT,db_path=self.path,original_response=True)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM market_history_documents').fetchone()[0],0)
    def test_denial_response_is_archived_and_stops_as_rate_limit(self):
        r=response({'chart':{'error':{'code':'Too Many Requests'}}},429)
        with self.assertRaises(YFRateLimitError):fetch_original_chart('TEST','2026-10-05','2026-10-13',SimpleNamespace(get=lambda *a,**kw:r),self.path,AT)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT status_code,body_bytes FROM market_source_documents').fetchone(),(429,r.content))
    def test_missing_action_price_and_misaligned_arrays_are_rejected(self):
        p=packet();d=p['chart']['result'][0];d['indicators']['quote'][0]['open'][1]=None
        stamp=d['timestamp'][1];d['events']={'splits':{str(stamp):{'date':stamp,'numerator':1,'denominator':20}}}
        with self.assertRaises(ValueError):fetch_original_chart('TEST','2026-10-05','2026-10-13',SimpleNamespace(get=lambda *a,**kw:response(p)),self.path,AT)
        p=packet();p['chart']['result'][0]['indicators']['adjclose'][0]['adjclose'].pop()
        with self.assertRaises(ValueError):fetch_original_chart('TEST','2026-10-05','2026-10-13',SimpleNamespace(get=lambda *a,**kw:response(p)),self.path,AT)
    def test_closed_calendar_date_does_not_substitute_previous_session_price(self):
        self.assertEqual(calendar_check('2026-06-19T00:05:00Z')['reason'],'Juneteenth')
        self.assertEqual(calendar_check('2027-01-01')['state'],'unknown_year')
        self.assertEqual(calendar_check('2026-07-03')['state'],'scheduled_closed')
        self.assertEqual(calendar_check('2026-07-06')['state'],'scheduled_session')
        h={'metadata':{},'price_basis':'split_adjusted','bars':[dict(date='2026-06-18',close=110,adjusted_close=110,open=110,low=100,high=120,split_ratio=0)]}
        doc=dict(id='fixture',source_url='https://example.test',observed_at=AT,start_date='2026-06-18',end_date='2026-10-13',history=h)
        p=dict(id=1,ticker='TEST',status='closed',entry_date='2026-06-18',close_date='2026-06-19T00:05:00Z',entry_price=110,close_price=110,realized_return_pct='bad')
        r=compare_position(p,doc);self.assertIsNone(r['recorded_return_pct']);self.assertIsNone(r['reference_price_return_pct'])
        self.assertEqual(r['exit_date'],'2026-06-19');self.assertEqual(r['exit_calendar']['state'],'scheduled_closed')
        self.assertFalse(r['account_return_verified'])
    def test_original_response_endpoint_detects_tampering_and_preserves_encoding(self):
        source_id=save_source(self.path,'TEST','https://example.test/source','primary_action',200,{},b'\x00\xff',AT)
        app=FastAPI();app.include_router(make_market_router(self.path));client=TestClient(app)
        r=client.get('/api/market-history/sources/'+source_id);self.assertEqual(r.status_code,200);self.assertEqual(r.json()['body_hex'],'00ff')
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET body_bytes=X'00'")
        self.assertEqual(client.get('/api/market-history/sources/'+source_id).status_code,409)
    def test_after_exit_split_rebases_both_endpoints_without_changing_recorded_ratio(self):
        from portfolio_reconciliation import CONFIRMED_SPLITS
        bars=[dict(date='2026-03-24',close=4.5,adjusted_close=4.5,open=4.5,low=4,high=5,split_ratio=0),dict(date='2026-05-05',close=6,adjusted_close=6,open=6,low=5.5,high=6.5,split_ratio=0),dict(date='2026-07-31',close=6,adjusted_close=6,open=6,low=5.5,high=6.5,split_ratio=1/30)]
        doc=dict(id='fixture',source_url='https://example.test',observed_at=AT,start_date='2026-03-24',end_date='2026-10-13',history={'metadata':{},'price_basis':'split_adjusted','bars':bars})
        p=dict(id=1,ticker='CSAI',status='closed',entry_date='2026-03-24',close_date='2026-05-05',entry_price=.15,close_price=.2,realized_return_pct=33.33)
        r=compare_position(p,doc);self.assertEqual(r['status'],'compatible_rebased_reference_ranges');self.assertAlmostEqual(r['conditional_recorded_split_return_pct'],33.333333333)
        self.assertTrue(r['primary_split_checks'][0]['vendor_matches'])

    def test_response_annotations_do_not_mutate_fixed_action_review_inputs(self):
        from portfolio_reconciliation import REVIEWED_DISTRIBUTIONS,UNRESOLVED_ACTION_NOTES
        before=copy.deepcopy(REVIEWED_DISTRIBUTIONS+UNRESOLVED_ACTION_NOTES)
        for symbol in ('HON','APH'):
            bars=[dict(date='2026-05-07',close=100,adjusted_close=100,open=100,low=99,high=101,split_ratio=0),dict(date='2026-10-02',close=101,adjusted_close=101,open=101,low=100,high=102,split_ratio=0)]
            doc=dict(id='fixture',source_url='https://example.test',observed_at=AT,start_date='2026-05-07',end_date='2026-10-13',history={'metadata':{},'price_basis':'split_adjusted','bars':bars})
            p=dict(id=1,ticker=symbol,status='closed',entry_date='2026-05-07',close_date='2026-10-02',entry_price=100,close_price=101)
            result=compare_position(p,doc)
            for record in result.get('distribution_checks',[])+result.get('action_review_notes',[]):record['source_archive']={'id':'fixture'}
        self.assertEqual(before,REVIEWED_DISTRIBUTIONS+UNRESOLVED_ACTION_NOTES)
