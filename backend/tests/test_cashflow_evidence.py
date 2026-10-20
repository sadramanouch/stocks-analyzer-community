import copy,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from financial_context import financial_context
from research_store import encoded
from cashflow_evidence import build,init_cashflow_evidence,make_cashflow_router,DEFINITIONS,sha
import test_financial_context as fixture
from test_financial_context import AT

class CashflowEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.fx=fixture.FinancialContextTests();self.fx.setUp();self.path=self.fx.path;init_cashflow_evidence(self.path)
  with sqlite3.connect(self.path) as db:db.executescript('CREATE TABLE saved_list_versions(id INTEGER PRIMARY KEY);INSERT INTO saved_list_versions VALUES(33);CREATE TABLE saved_list_items(version_id INTEGER,ticker TEXT,payload_json TEXT);')
  self.add('productive_asset_payments',35);self.publish();self.app=FastAPI();self.app.include_router(make_cashflow_router(self.path));self.client=TestClient(self.app)
 def tearDown(self):self.fx.tearDown()
 def add(self,field,value,years=(2025,2024,2023)):
  concept=DEFINITIONS[field][0];self.fx.payload['facts']['us-gaap'][concept]={'units':{'USD':[dict(self.fx.fact(y),val=value) for y in years]}}
 def entries(self,field):return self.fx.entries(DEFINITIONS[field][0])
 def publish(self):
  self.fx.publish()
  with sqlite3.connect(self.path) as db:db.execute('DELETE FROM saved_list_items WHERE version_id=33');db.execute('INSERT INTO saved_list_items VALUES(?,?,?)',(33,'TEST',encoded(self.fx.item)))
 def report(self):
  with sqlite3.connect(self.path) as db:db.row_factory=sqlite3.Row;return build(db,self.fx.item,33)
 def payload(self,r=None):
  r=r or self.report();return {'research_version':r['research_version'],'content_sha256':r['content_sha256'],'request_id':'a'*32}
 def capture(self):return self.client.post('/api/cashflow-evidence/TEST/capture',json=self.payload())
 def test_separate_cash_scopes_have_different_values_without_double_counting(self):
  self.add('software_payments',4);self.add('intangible_payments',6);self.add('acquisition_payments_net',50);self.publish();r=self.report();p=r['periods'][0]
  self.assertEqual(p['cash_measures']['cash_after_ppe_payments']['value'],'180');self.assertEqual(p['cash_measures']['cash_after_productive_asset_payments']['value'],'165')
  self.assertEqual(p['facts']['software_payments']['value'],'4');self.assertEqual(p['facts']['intangible_payments']['value'],'6');self.assertEqual(p['facts']['acquisition_payments_net']['value'],'50');self.assertFalse(r['legacy_capex_replaced']);self.assertTrue(r['selection_policy_unchanged'])
 def test_broader_measure_can_be_available_without_narrow_capex(self):
  del self.fx.payload['facts']['us-gaap'][DEFINITIONS['ppe_payments'][0]];self.publish();p=self.report()['periods'][0]
  self.assertIsNone(p['cash_measures']['cash_after_ppe_payments']['value']);self.assertEqual(p['cash_measures']['cash_after_productive_asset_payments']['value'],'165')
 def test_missing_discontinued_operations_is_never_zero(self):
  del self.fx.payload['facts']['us-gaap'][DEFINITIONS['reported_operating_cf'][0]];self.add('continuing_operating_cf',190);self.publish();p=self.report()['periods'][0]
  self.assertIsNone(p['total_operating_cf']['value']);self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'])
 def test_explicit_components_reconstruct_total_with_matching_filings(self):
  del self.fx.payload['facts']['us-gaap'][DEFINITIONS['reported_operating_cf'][0]];self.add('continuing_operating_cf',190);self.add('discontinued_operating_cf',10);self.publish();p=self.report()['periods'][0]
  self.assertEqual(p['total_operating_cf']['value'],'200');self.assertEqual(p['total_operating_cf']['status'],'derived_total_from_reported_components');self.assertEqual(p['cash_measures']['cash_after_productive_asset_payments']['value'],'165')
 def test_zero_and_negative_discontinued_cash_flows_are_real_values(self):
  del self.fx.payload['facts']['us-gaap'][DEFINITIONS['reported_operating_cf'][0]];self.add('continuing_operating_cf',190)
  for value,expected in ((0,'155'),(-10,'145')):
   self.add('discontinued_operating_cf',value);self.publish();self.assertEqual(self.report()['periods'][0]['cash_measures']['cash_after_productive_asset_payments']['value'],expected)
 def test_reported_total_is_not_added_to_its_components(self):
  self.add('continuing_operating_cf',190);self.add('discontinued_operating_cf',10);self.publish();p=self.report()['periods'][0]
  self.assertEqual(p['total_operating_cf']['value'],'200');self.assertEqual(p['total_operating_cf']['status'],'reported_total');self.assertEqual(p['operating_scope_reconciliation']['status'],'components_reconcile')
 def test_conflicting_components_withhold_all_cash_measures(self):
  self.add('continuing_operating_cf',190);self.add('discontinued_operating_cf',11);self.publish();p=self.report()['periods'][0]
  self.assertEqual(p['operating_scope_reconciliation']['difference'],'-1');self.assertIsNone(p['total_operating_cf']['value']);self.assertTrue(all(m['value'] is None for m in p['cash_measures'].values()))
 def test_conflicting_reported_total_is_not_overridden_by_components(self):
  self.add('continuing_operating_cf',190);self.add('discontinued_operating_cf',10);self.entries('reported_operating_cf').append(dict(self.entries('reported_operating_cf')[0],val=201));self.publish();p=self.report()['periods'][0]
  self.assertEqual(p['facts']['reported_operating_cf']['status'],'conflict');self.assertIsNone(p['total_operating_cf']['value'])
 def test_different_accession_duration_form_and_filing_date_do_not_combine(self):
  for key,value in [('accn','0000000001-26-000002'),('start','2025-01-02'),('form','10-K/A'),('filed','2026-02-02')]:
   self.add('productive_asset_payments',35);self.entries('productive_asset_payments')[0][key]=value;self.publish();p=self.report()['periods'][0]
   self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'],key)
 def test_different_component_filing_cannot_derive_total(self):
  del self.fx.payload['facts']['us-gaap'][DEFINITIONS['reported_operating_cf'][0]];self.add('continuing_operating_cf',190);self.add('discontinued_operating_cf',10);self.entries('discontinued_operating_cf')[0]['accn']='0000000001-26-000002';self.publish();self.assertIsNone(self.report()['periods'][0]['total_operating_cf']['value'])
 def test_non_usd_quarterly_and_future_facts_do_not_enter_annual_measure(self):
  for variation in ('eur','quarter','future','boolean','nonfinite'):
   self.add('productive_asset_payments',35);concept=self.fx.payload['facts']['us-gaap'][DEFINITIONS['productive_asset_payments'][0]];entries=self.entries('productive_asset_payments')
   if variation=='eur':concept['units']={'EUR':entries}
   elif variation=='quarter':entries[0]['start']='2025-10-01'
   elif variation=='future':entries[0]['filed']='2026-10-15'
   elif variation=='boolean':entries[0]['val']=True
   else:entries[0]['val']='NaN'
   self.publish();self.assertIsNone(self.report()['periods'][0]['cash_measures']['cash_after_productive_asset_payments']['value'],variation)
 def test_negative_payment_is_not_absolutized_and_zero_is_supported(self):
  self.add('productive_asset_payments',-35);self.publish();p=self.report()['periods'][0];self.assertEqual(p['facts']['productive_asset_payments']['status'],'requires_review');self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'])
  self.add('productive_asset_payments',0);self.publish();self.assertEqual(self.report()['periods'][0]['cash_measures']['cash_after_productive_asset_payments']['value'],'200')
 def test_negative_total_and_negative_cash_remaining_are_retained(self):
  self.fx.payload['facts']['us-gaap'][DEFINITIONS['reported_operating_cf'][0]]['units']['USD'][0]['val']=-200;self.publish();self.assertEqual(self.report()['periods'][0]['cash_measures']['cash_after_productive_asset_payments']['value'],'-235')
 def test_missing_annual_years_remain_empty_without_date_substitution(self):
  self.add('productive_asset_payments',35,years=(2025,));self.publish();r=self.report();self.assertEqual(r['periods'][1]['end'],'2024-12-31');self.assertIsNone(r['periods'][1]['cash_measures']['cash_after_productive_asset_payments']['value'])
 def test_cash_scopes_do_not_mutate_v1_context_item_or_source_bytes(self):
  before=copy.deepcopy(self.fx.item);old=self.fx.context()
  with sqlite3.connect(self.path) as db:source=db.execute('SELECT id,sha256,body_gzip FROM financial_source_documents ORDER BY id').fetchall()
  r=self.report();self.assertEqual(self.fx.item,before);self.assertEqual(self.fx.context(),old)
  with sqlite3.connect(self.path) as db:self.assertEqual(source,db.execute('SELECT id,sha256,body_gzip FROM financial_source_documents ORDER BY id').fetchall())
  self.assertFalse(r['forecast_verified']);self.assertFalse(r['account_return_verified']);digest=r.pop('content_sha256');self.assertEqual(digest,sha(encoded(r)))
 def test_wrong_issuer_ticker_mapping_and_tampered_original_never_certify(self):
  self.fx.payload['cik']=2;self.publish();self.assertEqual(self.report()['status'],'unavailable');self.assertEqual(self.report()['coverage']['source_matched_facts'],0)
 def test_corrupt_source_returns_unavailable_not_false_zero(self):
  self.fx.tamper('body_gzip',b'bad');r=self.report();self.assertEqual(r['status'],'unavailable');self.assertEqual(r['periods'],[])
 def test_http_capture_replay_idempotency_and_immutable_storage(self):
  r=self.capture();self.assertEqual(r.status_code,200);a=r.json();self.assertEqual(sha(a['report_json']),a['report_sha256'])
  exported=self.client.get('/api/cashflow-evidence/reports/'+a['id']);self.assertEqual(exported.status_code,200);self.assertEqual(exported.json()['report'],a['report'])
  repeated=self.capture();self.assertFalse(repeated.json()['created']);self.assertEqual(repeated.json()['id'],a['id'])
  with sqlite3.connect(self.path) as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM cashflow_evidence_reports')
 def test_new_editions_do_not_change_old_capture_and_require_reload(self):
  a=self.capture().json()
  with sqlite3.connect(self.path) as db:db.execute('INSERT INTO saved_list_versions VALUES(34)');db.execute('INSERT INTO saved_list_items VALUES(34,?,?)',('TEST',encoded({**self.fx.item,'input_sha256':'changed'})))
  self.assertEqual(self.client.get('/api/cashflow-evidence/reports/'+a['id']).json()['report'],a['report'])
  payload=self.payload();payload['request_id']='b'*32;self.assertEqual(self.client.post('/api/cashflow-evidence/TEST/capture',json=payload).status_code,409)
 def test_forged_numeric_fields_hashes_and_request_id_reuse_are_rejected(self):
  payload=self.payload();self.assertEqual(self.client.post('/api/cashflow-evidence/TEST/capture',json={**payload,'operating_cf':1}).status_code,422)
  self.assertEqual(self.client.post('/api/cashflow-evidence/TEST/capture',json={**payload,'content_sha256':'0'*64}).status_code,409)
  self.capture();self.assertEqual(self.client.post('/api/cashflow-evidence/TEST/capture',json={**payload,'content_sha256':'0'*64}).status_code,409)
 def test_tampered_saved_report_and_item_fail_replay(self):
  a=self.capture().json()
  with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER immutable_cashflow_evidence_reports_UPDATE');db.execute("UPDATE cashflow_evidence_reports SET report_json='{}'")
  self.assertEqual(self.client.get('/api/cashflow-evidence/reports/'+a['id']).status_code,409);self.assertEqual(self.capture().status_code,409)
 def test_storage_reserve_prevents_new_capture_but_not_read(self):
  with patch('cashflow_evidence.shutil.disk_usage',return_value=SimpleNamespace(free=1)):self.assertEqual(self.capture().status_code,409)
  self.assertEqual(self.client.get('/api/cashflow-evidence/TEST').status_code,200)
 def test_unknown_invalid_and_unavailable_company_fail_explicitly(self):
  self.assertEqual(self.client.get('/api/cashflow-evidence/UNKNOWN').status_code,404);self.assertEqual(self.client.get('/api/cashflow-evidence/reports/missing').status_code,404);self.assertEqual(self.client.get('/api/cashflow-evidence/BAD!').status_code,422)
  self.fx.tamper('body_gzip',b'bad');self.assertEqual(self.client.get('/api/cashflow-evidence/TEST').json()['status'],'unavailable');self.assertEqual(self.capture().status_code,409)
 def test_same_value_different_year_does_not_become_current_evidence(self):
  self.add('productive_asset_payments',35,years=(2024,2023));self.publish();self.assertIsNone(self.report()['periods'][0]['cash_measures']['cash_after_productive_asset_payments']['value'])
 def test_saved_item_change_is_detected_before_old_report_export(self):
  a=self.capture().json()
  with sqlite3.connect(self.path) as db:db.execute('UPDATE saved_list_items SET payload_json=? WHERE version_id=33',(encoded({**self.fx.item,'input_sha256':'different'}),))
  self.assertEqual(self.client.get('/api/cashflow-evidence/reports/'+a['id']).status_code,409)
