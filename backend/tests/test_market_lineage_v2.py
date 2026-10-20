"""Synthetic former/current class fixtures; no invented production history."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from market_history import init_market_history
from market_provenance import save_source
from market_lineage import reproduce,LINKS as ORIGINAL_LINKS,METHOD as V1
from market_lineage_v2 import LINKS,METHOD
from research_store import encoded
from test_completed_actions import filing_body
from test_market_provenance import packet
AT='2026-10-16T05:00:00+00:00'
class LineageV2Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';init_market_history(self.path);self.spec=LINKS['LSH'];s=self.spec
  self.inputs={'methodology':METHOD,'ticker':'LSH','provider_symbol':'QNME','captured_at':AT,'start_date':'2026-10-05','end_date':'2026-10-13','positions':[{'ticker':'LSH','entry_date':'2026-10-05','close_date':'2026-10-07'}]}
  self.bodies={'notice':filing_body('QNME',s['cik'],s['filing'],s['anchors'],exchange='NASDAQ'),'former':filing_body('LSH',s['cik'],s['former_filing'],exchange='NASDAQ')}
  self.index={'cik':s['cik'],'tickers':['QNME'],'exchanges':['Nasdaq'],'filings':{'recent':{k:[s['filing'][k],s['former_filing'][k]] for k in ('form','accessionNumber','filingDate','reportDate','primaryDocument')}}}
  self.bodies['index']=encoded(self.index).encode();raw=packet();raw['chart']['result'][0]['meta'].update(symbol='QNME',exchangeName='NCM');self.bodies['chart']=encoded(raw).encode()
  ny=ZoneInfo('America/New_York');self.params={'period1':int(datetime(2026,10,5,tzinfo=ny).timestamp()),'period2':int(datetime(2026,10,14,tzinfo=ny).timestamp()),'interval':'1d','includePrePost':False,'events':'div,splits,capitalGains'}
  for key,body in self.bodies.items():self.store(key,body)
 def tearDown(self):self.tmp.cleanup()
 def store(self,key,body,at='2026-10-15T23:00:00+00:00'):
  s=self.spec;url={'notice':s['notice_url'],'former':s['former_filing']['url'],'index':'https://data.sec.gov/submissions/CIK'+s['cik']+'.json','chart':'https://query2.finance.yahoo.com/v8/finance/chart/QNME'}[key]
  kind={'notice':'sec_symbol_notice','former':'sec_former_symbol_filing','index':'sec_symbol_submissions','chart':'yahoo_original_chart'}[key]
  self.inputs[key+'_id']=save_source(self.path,'QNME' if key=='chart' else 'LSH',url,kind,200,self.params if key=='chart' else {},body,at)
 def report(self):
  with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return reproduce(db,self.inputs)
 def test_both_registered_classes_are_bound_without_relabeling_provider(self):
  r=self.report();self.assertEqual(r['methodology'],METHOD);self.assertEqual(len(r['sources']),4);self.assertEqual(r['history']['metadata']['symbol'],'QNME');self.assertEqual(r['former_registered_class']['security_type'],'CS');self.assertEqual(r['current_registered_class']['security_type'],'CS');self.assertFalse(r['account_return_verified'])
 def test_former_symbol_cannot_be_inferred_from_issuer_name(self):
  self.store('former',self.bodies['former'].replace(b'>LSH<',b'>OTHER<'))
  with self.assertRaises(ValueError):self.report()
 def test_wrong_former_issuer_or_noncommon_class_is_not_accepted(self):
  for old,new in [(b'0001996192',b'0001234567'),(b'Common Stock',b'Warrant')]:
   self.store('former',self.bodies['former'].replace(old,new))
   with self.assertRaises(ValueError):self.report()
 def test_former_filing_index_date_and_document_must_match(self):
  self.index['filings']['recent']['filingDate'][1]='2026-02-20';self.store('index',encoded(self.index).encode())
  with self.assertRaises(ValueError):self.report()
 def test_future_former_observation_is_withheld(self):
  self.store('former',self.bodies['former']+b'\n',at='2026-10-16T06:00:00+00:00')
  with self.assertRaises(ValueError):self.report()
 def test_v1_catalog_and_method_cannot_silently_admit_new_symbol(self):
  self.assertNotIn('LSH',ORIGINAL_LINKS);self.inputs['methodology']=V1
  with self.assertRaises(ValueError):self.report()
 def test_continuity_notice_and_current_registered_symbol_are_both_required(self):
  self.store('notice',self.bodies['notice'].replace(b'>QNME<',b'>OTHER<'))
  with self.assertRaises(ValueError):self.report()
if __name__=='__main__':unittest.main()
