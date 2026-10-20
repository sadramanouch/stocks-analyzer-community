"""Explicit synthetic filings exercise custody, dates and unverified settlement."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from html import escape
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from market_history import init_market_history
from market_provenance import save_source
from research_store import encoded
from security_identity import cover_class
from test_security_identity import cover,security_row
from completed_actions import (CASES,METHOD,locations,reproduce,replay,capture,make_actions_router,position_reviews,sha)
from portfolio_reconciliation import reconcile_position

AT='2026-10-16T05:00:00+00:00'
def filing_body(ticker,cik,filing,anchors=(),exchange='New York Stock Exchange'):
 body=cover(rows=security_row(ticker=ticker,exchange=exchange),extra='<p>'+escape(' '.join(anchors))+'</p>',cik=cik)
 return body.replace(b'2026-06-30',filing['reportDate'].encode()).replace(b'2026-01-01',filing['reportDate'].encode()).replace(b'>10-Q<',('>'+filing['form']+'<').encode())

class CompletedActionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';init_market_history(self.path);self.spec=CASES['TMHC'];self.ticker='TMHC'
  with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE paper_positions(ticker TEXT)');db.execute("INSERT INTO paper_positions VALUES('TMHC')")
  self.inputs={'methodology':METHOD,'ticker':'TMHC','captured_at':AT}
  self.notice=filing_body('TMHC',self.spec['cik'],self.spec['filing'],self.spec['anchors'])
  self.index={'cik':self.spec['cik'],'tickers':[],'filings':{'recent':{k:[v] for k,v in self.spec['filing'].items()}}}
  self.set_source('notice',self.notice);self.set_source('index',encoded(self.index).encode())
 def tearDown(self):self.tmp.cleanup()
 def set_source(self,key,body,at=AT,**changes):
  url=locations(self.spec)[0 if key=='notice' else 1]
  self.inputs[key+'_id']=save_source(self.path,changes.get('ticker',self.ticker),url,'sec_completed_action_'+('notice' if key=='notice' else 'submissions'),changes.get('status',200),changes.get('request',{}),body,at)
 def report(self):
  with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return reproduce(db,self.inputs)
 def store(self):
  raw=encoded(self.inputs);report=encoded(self.report());identifier=sha(raw)
  with sqlite3.connect(self.path) as db:db.execute('INSERT INTO completed_action_reports VALUES(?,?,?,?,?,?)',(identifier,self.ticker,AT,raw,report,sha(report)))
  return identifier
 def test_conversion_date_is_not_earliest_filing_event(self):
  r=self.report();self.assertEqual(r['reported_conversion_date'],'2026-07-24');self.assertEqual(r['filing']['reportDate'],'2026-07-20');self.assertFalse(r['account_return_verified']);self.assertFalse(r['broker_receipt_verified'])
 def test_delisted_current_index_can_be_empty_but_exact_cik_required(self):
  self.assertEqual(self.report()['ticker'],'TMHC');self.index['cik']='0000000001';self.set_source('index',encoded(self.index).encode())
  with self.assertRaisesRegex(ValueError,'issuer'):self.report()
 def test_announcement_without_completion_or_different_consideration_is_not_enough(self):
  for old,new in [(b'On July 24, 2026',b'On July 20, 2026'),(b'$72.50',b'$99.99')]:
   self.set_source('notice',self.notice.replace(old,new))
   with self.assertRaises(ValueError):self.report()
 def test_registered_bond_or_wrong_ticker_cannot_supply_common_share_evidence(self):
  for title,ticker in [('Senior Notes due 2031','TMHC'),('Common Stock','OTHER')]:
   self.set_source('notice',filing_body(ticker,self.spec['cik'],self.spec['filing'],self.spec['anchors']).replace(b'Common Stock',title.encode()))
   with self.assertRaises(ValueError):self.report()
 def test_future_and_unzoned_observations_are_withheld(self):
  for i,at in enumerate(['2026-10-16T06:00:00+00:00','2026-10-16T05:00:00'],1):
   self.set_source('notice',self.notice+b'\n'*i,at=at)
   with self.assertRaises(ValueError):self.report()
 def test_stale_index_and_wrong_request_scope_are_withheld(self):
  self.set_source('index',encoded(self.index).encode()+b'\n',at='2026-10-01T05:00:00+00:00')
  with self.assertRaises(ValueError):self.report()
  self.set_source('index',encoded(self.index).encode(),request={'ticker':'OTHER'})
  with self.assertRaises(ValueError):self.report()
 def test_filing_index_arrays_and_report_date_cannot_be_mixed(self):
  self.index['filings']['recent']['reportDate']=['2026-07-24'];self.set_source('index',encoded(self.index).encode())
  with self.assertRaises(ValueError):self.report()
 def test_closed_interval_before_conversion_has_no_conversion_warning(self):
  r={'report_id':'x','report':self.report(),'id':'x'};items={'TMHC':r}
  self.assertEqual(position_reviews({'ticker':'TMHC','entry_date':'2026-05-07','end_date':'2026-07-20'},items),[])
  self.assertEqual(len(position_reviews({'ticker':'TMHC','entry_date':'2026-05-07','end_date':'2026-07-24'},items)),1)
 def test_post_conversion_entry_is_flagged_instead_of_ignored(self):
  r=position_reviews({'ticker':'TMHC','entry_date':'2026-08-01','end_date':'2026-10-15'},{'TMHC':{'id':'fixture','report':self.report()}})
  self.assertEqual(r[0]['state'],'entry_on_or_after_reported_conversion')
 def test_no_automatic_cash_or_split_only_return_is_substituted(self):
  p=dict(id=1,ticker='TMHC',status='open',entry_date='2026-05-07',end_date='2026-10-15',entry_price=50,mark_price=60,virtual_dollars=100)
  before=copy.deepcopy(p);r=reconcile_position(p,[],{'TMHC':{'id':'fixture','report':self.report()}})
  self.assertEqual(p,before);self.assertIsNone(r['split_scenario_return_pct']);self.assertEqual(r['status'],'unverified');self.assertEqual(r['legacy_return_pct'],20);self.assertEqual(len(r['primary_completed_actions']),1)
 def test_static_capture_is_idempotent_without_contact_or_network(self):
  identifier=self.store()
  with patch('completed_actions.download',side_effect=AssertionError('No network')):self.assertEqual(capture(self.path,'TMHC')['id'],identifier)
 def test_sources_reports_and_api_numbers_are_not_mutable(self):
  identifier=self.store();app=FastAPI();app.include_router(make_actions_router(self.path));client=TestClient(app)
  self.assertEqual(client.get('/api/completed-actions/reports/'+identifier).status_code,200)
  self.assertEqual(client.post('/api/completed-actions/TMHC/capture',json={'cash_received':1}).status_code,422)
  self.assertEqual(client.post('/api/completed-actions/UNKNOWN/capture',json={}).status_code,409)
  with sqlite3.connect(self.path) as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM completed_action_reports')
   db.execute('DROP TRIGGER immutable_completed_action_reports_UPDATE');db.execute("UPDATE completed_action_reports SET report_sha256='bad'")
  self.assertEqual(client.get('/api/completed-actions/reports/'+identifier).status_code,409)
 def test_replay_fails_when_original_body_changes(self):
  identifier=self.store()
  with sqlite3.connect(self.path) as db:
   db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET body_bytes=X'00' WHERE id=?",(self.inputs['notice_id'],))
   db.row_factory=sqlite3.Row
   with self.assertRaises(ValueError):replay(db,db.execute('SELECT * FROM completed_action_reports WHERE id=?',(identifier,)).fetchone())
 def test_uppercase_exchange_alias_is_explicit_and_does_not_expand_old_default(self):
  f=self.spec['filing'];body=filing_body('TMHC',self.spec['cik'],f,exchange='The NASDAQ Stock Market LLC')
  with self.assertRaises(ValueError):cover_class(body,'TMHC',self.spec['cik'],f,{'exchange':'NASDAQ'})
  self.assertEqual(cover_class(body,'TMHC',self.spec['cik'],f,{'exchange':'NASDAQ'},exchange_aliases={'NASDAQ Stock Market LLC':'NASDAQ'})['security_type'],'CS')
 def test_portfolio_banner_distinguishes_reviewed_open_mark_from_corrupt_evidence(self):
  from portfolio_integrity import portfolio_integrity
  self.store()
  with sqlite3.connect(self.path) as db:
   for name,kind in [('entry_date','TEXT'),('close_date','TEXT'),('status','TEXT'),('entry_price','REAL'),('virtual_dollars','REAL'),('realized_return_pct','REAL')]:db.execute('ALTER TABLE paper_positions ADD COLUMN '+name+' '+kind)
   db.execute("UPDATE paper_positions SET status='open',entry_date='2026-05-07',entry_price=50,virtual_dollars=100")
   db.execute('CREATE TABLE company_analyses(ticker TEXT,current_price REAL,analyzed_at TEXT)');db.execute("INSERT INTO company_analyses VALUES('TMHC',60,'2026-10-15')")
  r=portfolio_integrity(self.path);self.assertEqual(r['open_completed_action_count'],1);self.assertEqual(r['status'],'unverified')
  with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER immutable_completed_action_reports_UPDATE');db.execute("UPDATE completed_action_reports SET report_sha256='bad'")
  r=portfolio_integrity(self.path);self.assertIsNone(r['open_completed_action_count']);self.assertIsNone(r['open_completed_action_reviews']);self.assertEqual(r['completed_action_evidence_status'],'integrity_failed')
 def test_new_capture_writes_only_source_and_review_evidence_then_reuses_it(self):
  bodies=dict(zip(locations(self.spec),[self.notice,encoded(self.index).encode()]));calls=[]
  class Response:
   status_code=200
   def __init__(self,body):self.body=body
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def iter_content(self,size):return iter([self.body])
  class Client:
   def get(self,url,**kwargs):calls.append(url);return Response(bodies[url])
  with patch.dict('os.environ',{'EDGAR_USER_AGENT':'Explicit test fixture'}):
   first=capture(self.path,'TMHC',Client());again=capture(self.path,'TMHC',Client())
  self.assertEqual(first['id'],again['id']);self.assertEqual(len(calls),2)
  with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM completed_action_reports').fetchone()[0],1);self.assertEqual(db.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0],1)

if __name__=='__main__':unittest.main()
