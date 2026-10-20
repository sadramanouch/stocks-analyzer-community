import copy,json,sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from quote_checks import (POLICY,parse_quote,evaluate,collect,digest,init_quote_checks,make_quote_router,export_report,identity_check)
from research_store import encoded

AT='2026-10-15T17:40:00+00:00'
QUOTE='2026-10-15T13:39:30-04:00'
STAMP=int(datetime.fromisoformat(QUOTE).timestamp())
def identity():return {'ticker':'TEST','security_type':'CS','title':'Test Common Stock','exchange':'N','source_date':'2026-10-14','retrieved_at':'2026-10-14T20:50:00+00:00','revision_id':1,'source_url':'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'}
def packets():return {
 'yahoo':{'chart':{'error':None,'result':[{'meta':{'symbol':'TEST','instrumentType':'EQUITY','exchangeTimezoneName':'America/New_York','regularMarketPrice':100,'regularMarketTime':STAMP,'currency':'USD','exchangeName':'NYQ'}}]}},
 'cnbc':{'QuickQuoteResult':{'QuickQuote':[{'symbol':'TEST','providerSymbol':'TEST','code':'0','assetType':'STOCK','assetSubType':'Common Stock','countryCode':'US','last_time':QUOTE,'reg_last_time':QUOTE,'curmktstatus':'REG_MKT','last_time_msec':str(STAMP*1000),'last':'100.01','currencyCode':'USD','exchange':'NYSE','source':'Last NASDAQ LS, VOL From CTA'}]}}}
def inputs():return {'ticker':'TEST','captured_at':AT,'identity':identity(),'source_ids':{},'failures':{}}
def sources(p=None):return {k:{'id':k,'body_bytes':json.dumps(v).encode(),'status_code':200,'sha256':digest(json.dumps(v)),'observed_at':AT} for k,v in (p or packets()).items()}
def cnbc(p):return p['cnbc']['QuickQuoteResult']['QuickQuote'][0]
def yahoo(p):return p['yahoo']['chart']['result'][0]['meta']
class Reply:
 def __init__(self,body,status=200):self.body=body;self.status_code=status;self.closed=False
 def iter_content(self,n):yield self.body
 def close(self):self.closed=True

class QuoteCheckTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db';init_quote_checks(self.path)
  i=identity();raw={k:v for k,v in i.items() if k not in ('revision_id','retrieved_at')}
  with sqlite3.connect(self.path) as db:
   db.executescript('CREATE TABLE instrument_revisions(id INTEGER PRIMARY KEY,ticker TEXT,retrieved_at TEXT,evidence_json TEXT);CREATE TABLE signal_trial_settings(id INTEGER,enabled INTEGER);INSERT INTO signal_trial_settings VALUES(1,1);CREATE TABLE signal_trials(ticker TEXT,registered_at TEXT,exit_date TEXT);')
   db.execute('INSERT INTO instrument_revisions VALUES(?,?,?,?)',(1,'TEST',i['retrieved_at'],encoded(raw)))
  self.app=FastAPI();self.router=make_quote_router(self.path);self.app.include_router(self.router);self.client=TestClient(self.app)
 def tearDown(self):self.temp.cleanup()
 def capture(self,p=None):
  responses=[Reply(json.dumps(v).encode()) for v in (p or packets()).values()]
  session=SimpleNamespace(get=lambda *a,**kw:responses.pop(0))
  with patch('quote_checks.now',return_value=AT):return collect(self.path,'TEST',session)
 def test_comparison_is_bounded_agreement_without_execution_or_independence_claim(self):
  r=evaluate(inputs(),sources());self.assertEqual(r['comparison']['state'],'within_declared_tolerance');self.assertEqual(r['comparison']['absolute_difference'],'0.01');self.assertEqual(r['comparison']['price_tolerance'],'0.100')
  self.assertFalse(r['upstream_independence_verified']);self.assertFalse(r['executable_price_verified']);self.assertFalse(r['account_return_verified']);self.assertIn('NASDAQ LS',r['quotes'][1]['reported_feed'])
 def test_price_disagreement_remains_visible(self):
  p=packets();cnbc(p)['last']='100.10001';r=evaluate(inputs(),sources(p));self.assertEqual(r['comparison']['state'],'price_disagreement')
 def test_tolerance_uses_lower_price_and_exact_decimal_boundary(self):
  p=packets();cnbc(p)['last']='100.1';self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'within_declared_tolerance')
  yahoo(p)['regularMarketPrice']='0.2';cnbc(p)['last']='0.21';self.assertEqual(evaluate(inputs(),sources(p))['comparison']['price_tolerance'],'0.01')
 def test_mismatched_identity_currency_and_instrument_never_promote(self):
  for provider,key,value in [('yahoo','symbol','OTHER'),('yahoo','currency','CAD'),('yahoo','instrumentType','ETF'),('yahoo','exchangeTimezoneName','Asia/Tokyo'),('cnbc','providerSymbol','OTHER'),('cnbc','currencyCode','CAD'),('cnbc','assetSubType','Warrant'),('cnbc','countryCode','CA')]:
   p=packets();(yahoo(p) if provider=='yahoo' else cnbc(p))[key]=value
   self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'not_comparable',key)
 def test_booleans_nonfinite_zero_and_negative_prices_are_rejected(self):
  for v in (True,'NaN','Infinity',0,-1):
   p=packets();cnbc(p)['last']=v;self.assertEqual(evaluate(inputs(),sources(p))['quotes'][1]['state'],'unavailable')
 def test_ambiguous_arrays_and_error_codes_are_rejected(self):
  p=packets();p['yahoo']['chart']['result']*=2;self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'not_comparable')
  p=packets();cnbc(p)['code']='1';self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'not_comparable')
 def test_exchange_mismatch_and_missing_identity_fail_closed(self):
  p=packets();cnbc(p)['exchange']='NASDAQ';self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'not_comparable')
  i=inputs();i['identity']=None;self.assertEqual(evaluate(i,sources())['comparison']['state'],'not_comparable')
 def test_old_identity_does_not_trigger_network_requests(self):
  with sqlite3.connect(self.path) as db:db.execute("UPDATE instrument_revisions SET retrieved_at='2026-10-01T00:00:00Z'")
  with patch('quote_checks.now',return_value=AT),patch('quote_checks.requests.Session') as session,self.assertRaisesRegex(ValueError,'fresh authoritative'):collect(self.path,'TEST')
  session.assert_not_called()
 def test_warrant_and_unknown_listing_exchange_not_accepted(self):
  for key,value in [('security_type','warrant'),('exchange','unsupported'),('source_date','2026-10-01')]:
   i=identity();i[key]=value;self.assertFalse(identity_check(i,'TEST',AT))
 def test_quote_time_gap_is_not_price_disagreement(self):
  p=packets();yahoo(p)['regularMarketTime']-=61;r=evaluate(inputs(),sources(p));self.assertEqual(r['comparison']['state'],'not_comparable');self.assertEqual(r['comparison']['time_difference_seconds'],'61.0')
 def test_stale_future_naive_and_inconsistent_millisecond_timestamps_rejected(self):
  for key,value in [('last_time',QUOTE[:19]),('last_time_msec',str((STAMP+1)*1000)),('reg_last_time','2026-10-15T13:39:29-04:00')]:
   p=packets();cnbc(p)[key]=value;self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'not_comparable')
  for seconds in (-1300,60):
   p=packets();yahoo(p)['regularMarketTime']=int(datetime.fromisoformat(AT).timestamp())+seconds
   self.assertEqual(evaluate(inputs(),sources(p))['quotes'][0]['state'],'unavailable')
 def test_source_retrieval_cannot_precede_quote_beyond_clock_allowance(self):
  s=sources();s['yahoo']['observed_at']='2026-10-15T17:39:00Z';self.assertEqual(evaluate(inputs(),s)['quotes'][0]['state'],'unavailable')
 def test_holiday_early_close_and_unknown_year_fail_session_check(self):
  for at,stamp in [('2026-11-26T17:40:00Z','2026-11-26T12:39:30-05:00'),('2026-11-27T18:40:00Z','2026-11-27T13:39:30-05:00'),('2027-10-15T17:40:00Z','2027-10-15T13:39:30-04:00')]:
   i=inputs();i['captured_at']=at;p=packets();yahoo(p)['regularMarketTime']=int(datetime.fromisoformat(stamp).timestamp());s=sources(p);s['yahoo']['observed_at']=at
   self.assertEqual(evaluate(i,s)['quotes'][0]['state'],'unavailable')
 def test_raw_bytes_report_hashes_and_original_identity_replay(self):
  r=self.capture();self.assertTrue(r['created']);self.assertEqual(digest(r['report_json']),r['report_sha256'])
  exported=self.client.get('/api/quote-checks/reports/'+r['id']);self.assertEqual(exported.status_code,200);self.assertEqual(exported.json()['report'],r['report'])
  for q in r['report']['quotes']:
   raw=self.client.get('/api/quote-checks/sources/'+q['source_id']);self.assertEqual(digest(raw.content),q['source_sha256']);self.assertEqual(raw.headers['x-content-sha256'],q['source_sha256'])
  with sqlite3.connect(self.path) as db:
   for table in ('quote_check_reports','market_source_documents'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
 def test_five_minute_retry_reuses_immutable_snapshot_without_network(self):
  r=self.capture()
  with patch('quote_checks.now',return_value=AT),patch('quote_checks.requests.Session') as session:again=collect(self.path,'TEST')
  self.assertFalse(again['created']);self.assertEqual(again['id'],r['id']);session.assert_not_called()
 def test_corruption_of_source_and_identity_is_detected(self):
  r=self.capture()
  with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET body_bytes=X'00'")
  self.assertEqual(self.client.get('/api/quote-checks/reports/'+r['id']).status_code,409)
  self.assertEqual(self.client.get('/api/quote-checks/sources/'+r['report']['quotes'][0]['source_id']).status_code,409)
 def test_changed_authoritative_identity_does_not_silently_replay(self):
  r=self.capture()
  with sqlite3.connect(self.path) as db:db.execute("UPDATE instrument_revisions SET evidence_json='{}'")
  self.assertEqual(self.client.get('/api/quote-checks/reports/'+r['id']).status_code,409)
 def test_corrupt_report_is_not_returned_as_success(self):
  r=self.capture()
  with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER immutable_quote_check_reports_UPDATE');db.execute("UPDATE quote_check_reports SET report_json='{}'")
  self.assertEqual(self.client.get('/api/quote-checks/TEST').status_code,409)
 def test_access_denial_is_archived_without_trying_another_endpoint(self):
  reply=Reply(b'{"denied":true}',429);calls=[]
  def get(*a,**kw):calls.append(a);return reply
  with patch('quote_checks.now',return_value=AT):r=collect(self.path,'TEST',SimpleNamespace(get=get))
  self.assertEqual(len(calls),1);self.assertTrue(reply.closed);self.assertEqual(r['report']['quotes'][0]['http_status'],429);self.assertIsNone(r['report']['quotes'][1]['source_id'])
 def test_timeouts_and_response_size_limits_remain_failures(self):
  for failure in ('timeout','large'):
   if failure=='timeout':session=SimpleNamespace(get=lambda *a,**kw:(_ for _ in ()).throw(requests.Timeout()))
   else:session=SimpleNamespace(get=lambda *a,**kw:Reply(b' '*2_000_001))
   with patch('quote_checks.now',return_value=AT):r=collect(self.path,'TEST',session)
   self.assertEqual(r['report']['comparison']['state'],'not_comparable')
   # Separate capture windows for the next failure case.
   with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER IF EXISTS immutable_quote_check_reports_DELETE');db.execute('DELETE FROM quote_check_reports')
 def test_storage_reserve_and_capture_lock_prevent_fetch(self):
  with patch('quote_checks.shutil.disk_usage',return_value=SimpleNamespace(free=1)),patch('quote_checks.requests.Session') as s,self.assertRaisesRegex(ValueError,'reserve'):collect(self.path,'TEST')
  s.assert_not_called()
 def test_http_validation_and_empty_read_do_not_fetch(self):
  with patch('quote_checks.requests.Session') as s:
   self.assertIsNone(self.client.get('/api/quote-checks/TEST').json()['report']);self.assertEqual(self.client.post('/api/quote-checks/TEST/capture',json={'price':1}).status_code,422);self.assertEqual(self.client.get('/api/quote-checks/BAD!').status_code,422)
  s.assert_not_called();self.assertEqual(self.client.get('/api/quote-checks/reports/missing').status_code,404)
 def test_scheduled_collection_is_bounded_and_honors_existing_trial_pause(self):
  with sqlite3.connect(self.path) as db:
   db.executemany('INSERT INTO signal_trials VALUES(?,?,?)',[(f'T{i}',AT,'2026-10-22') for i in range(7)])
  with patch('quote_checks.now',return_value=AT),patch('quote_checks.collect') as c:self.router.scheduled_collect()
  self.assertEqual(c.call_count,5)
  with sqlite3.connect(self.path) as db:db.execute('UPDATE signal_trial_settings SET enabled=0')
  with patch('quote_checks.now',return_value=AT),patch('quote_checks.collect') as c:self.router.scheduled_collect()
  c.assert_not_called()
 def test_later_identity_revision_preserves_original_capture_replay(self):
  r=self.capture()
  i=identity();i['title']='Later authoritative title';i.pop('revision_id');i.pop('retrieved_at')
  with sqlite3.connect(self.path) as db:db.execute('INSERT INTO instrument_revisions VALUES(?,?,?,?)',(2,'TEST',AT,encoded(i)))
  exported=self.client.get('/api/quote-checks/reports/'+r['id']);self.assertEqual(exported.status_code,200);self.assertEqual(exported.json()['report']['identity']['revision_id'],1)
 def test_capture_clock_cannot_move_backwards_and_reuse_future_snapshot(self):
  self.capture()
  with patch('quote_checks.now',return_value='2026-10-15T17:39:59Z'),self.assertRaisesRegex(ValueError,'clock precedes'):collect(self.path,'TEST')
 def test_exact_time_and_freshness_boundaries(self):
  p=packets();yahoo(p)['regularMarketTime']-=60;self.assertEqual(evaluate(inputs(),sources(p))['comparison']['state'],'within_declared_tolerance')
  p=packets();yahoo(p)['regularMarketTime']=int(datetime.fromisoformat(AT).timestamp())-1200;self.assertEqual(evaluate(inputs(),sources(p))['quotes'][0]['state'],'dated_reference')
  yahoo(p)['regularMarketTime']-=1;self.assertEqual(evaluate(inputs(),sources(p))['quotes'][0]['state'],'unavailable')
