import ast,copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from decimal import Decimal
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from financial_integrity import finite
from research_integrity import valuation_scenario
from research_store import encoded
from number_evidence import (project,build,sha,init_number_evidence,make_number_router,current_report,public_values,dupont_supported)

AT='2026-10-15T16:00:00Z'

def data():
 info={'securityType':'CS','quoteType':'EQUITY','longName':'Fixture Common Stock','sector':'Technology','industry':'Software','sharesOutstanding':1000,'currentPrice':10,'marketCap':10000,'beta':1}
 s=valuation_scenario(info,1000,[(110,100),(121,110)])
 return {'ticker':'TEST','cik':'1','_fw_version':5,'financial_currency':'USD','financial_period_end':'2025-12-31',
         'price':10.,'shares_outstanding':1000.,'market_cap':10000.,'quote_as_of':'2026-10-14T20:00:00Z','quote_currency':'USD','quote_source':'Explicit unit fixture',
         'net_income':100.,'revenue':1000.,'operating_cf':1200.,'capex':-200.,'fcf':1000.,'fcf_yield':.1,
         'total_debt':500.,'cash':200.,'net_debt':300.,'total_assets':2000.,'total_equity':1000.,'de_ratio':.5,
         'dupont':{'net_margin':10.,'asset_turnover':.5,'equity_multiplier':2.,'roe':10.},
         'valuation_scenario':s,'dcf_price':s['price'],'reverse_dcf':s['reverse'],'pe_ttm':10.,'pe_fwd':8.}


class NumberEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db';self.d=data()
  with sqlite3.connect(self.path) as db:
   db.executescript('CREATE TABLE market_data_cache(ticker TEXT PRIMARY KEY,data_json TEXT,fetched_at TEXT);CREATE TABLE saved_list_items(version_id INTEGER,ticker TEXT,payload_json TEXT);CREATE TABLE paper_ledger_events(id INTEGER);')
  self.store(self.d);init_number_evidence(self.path)
  app=FastAPI();app.include_router(make_number_router(self.path));self.client=TestClient(app)
 def tearDown(self):self.temp.cleanup()
 def store(self,d):
  with sqlite3.connect(self.path) as db:db.execute('INSERT OR REPLACE INTO market_data_cache VALUES(?,?,?)',(d['ticker'],json.dumps(d),AT))
 def report(self,d=None,context=None):return build(project(d or self.d),context,1 if context else None,AT)
 def request(self,report,request_id='a'*32):return {'input_sha256':report['input_sha256'],'context_sha256':(report['primary_context'] or {}).get('content_sha256'),'request_id':request_id}

 def test_all_eight_arithmetic_checks_and_declared_scenario_are_independent_replays(self):
  r=self.report();self.assertEqual(r['check_counts'],{'arithmetic_consistent':8})
  self.assertEqual(r['conditional_valuation']['state'],'scenario_arithmetic_consistent');self.assertEqual(len(r['conditional_valuation']['rows']),12)
  self.assertTrue(all(not c['independently_source_verified'] for c in r['checks']))
  self.assertFalse(r['conditional_valuation']['future_forecast_verified']);self.assertFalse(r['forecast_verified'])
  self.assertFalse(r['quote']['independently_source_verified']);self.assertFalse(r['account_return_verified'])

 def test_negative_cash_flow_is_not_replaced_with_zero(self):
  d={**self.d,'operating_cf':-100.,'capex':200.,'fcf':-300.,'fcf_yield':-.03}
  r=self.report(d);self.assertEqual(r['checks'][0]['state'],'arithmetic_consistent');self.assertEqual(r['checks'][1]['state'],'arithmetic_consistent')

 def test_explicit_zero_income_is_valid_but_unknown_is_not_zero(self):
  d=copy.deepcopy(self.d);d['net_income']=0.;d['dupont']['roe']=d['dupont']['net_margin']=0.
  r=self.report(d);self.assertEqual(r['checks'][4]['state'],'arithmetic_consistent');self.assertEqual(r['display_suppressions'],[])
  d['net_income']=None;r=self.report(d)
  self.assertEqual(r['checks'][4]['state'],'not_reproducible');self.assertEqual(r['checks'][7]['state'],'not_reproducible');self.assertEqual(r['display_suppressions'][0]['field'],'dupont')

 def test_bool_nonfinite_missing_and_zero_denominators_cannot_pass(self):
  for bad in (None,True,float('nan'),float('inf'),0.,-1.):
   d={**self.d,'total_equity':bad}
   with self.subTest(bad=bad):
    r=self.report(d);self.assertEqual(r['checks'][3]['state'],'not_reproducible');self.assertTrue(r['display_suppressions']);encoded(r)

 def test_altered_values_raise_differences_without_claiming_provider_error(self):
  d={**self.d,'fcf':999.,'net_debt':299.,'de_ratio':.4}
  r=self.report(d);self.assertEqual(r['checks'][0]['state'],'arithmetic_difference')
  self.assertEqual(r['checks'][2]['state'],'arithmetic_difference');self.assertEqual(r['checks'][3]['state'],'arithmetic_difference')
  self.assertTrue(all(p['state'] in ('provider_reported_unverified','not_available') for p in r['provider_fields']))

 def test_legacy_debt_percentage_or_denominator_is_not_silently_assumed(self):
  r=self.report({**self.d,'_fw_version':1,'de_ratio':50.})
  self.assertEqual(r['checks'][3]['state'],'definition_unverified')
  self.assertEqual(r['checks'][3]['reported'],'50.0')
  self.assertIsNotNone(r['checks'][3]['expected_interval'])
  self.assertEqual(self.report({**self.d,'_fw_version':5,'de_ratio':50.})['checks'][3]['state'],'arithmetic_difference')

 def test_decimal_rounding_interval_is_tight_and_explicit(self):
  d={**self.d,'fcf_yield':.10004};self.assertEqual(self.report(d)['checks'][1]['state'],'arithmetic_consistent')
  d['fcf_yield']=.1002;self.assertEqual(self.report(d)['checks'][1]['state'],'arithmetic_difference')
  d={**self.d,'total_equity':.00001};self.assertEqual(self.report(d)['checks'][3]['state'],'not_reproducible')

 def test_source_matching_requires_exact_issuer_period_concept_accession_units_and_values(self):
  d=copy.deepcopy(self.d);fields={}
  for key in ('revenue','net_income','operating_cf','capex'):
   fields[key]={'status':'source_matched','value':str(abs(d[key]) if key=='capex' else d[key]),'concept':key,'filed':'2026-03-01','accn':'fixture','end':'2025-12-31','form':'10-K','unit':'USD'}
  d['financial_provenance']=copy.deepcopy(fields)
  context={'status':'source_context_available','sources':[{'cik':'0000000001'}],'periods':[{'end':'2025-12-31','annual_fields':fields}]}
  self.assertEqual(sum(p['state']=='matched_to_original_annual_fact' for p in self.report(d,context)['core_source_matches']),4)
  for change in ({'cik':'2'},{'financial_currency':'CAD'},{'financial_period_end':'2025-12-30'},{'financial_provenance':[]},{'net_income':101.}):
   r=self.report({**d,**change},context)
   self.assertLess(sum(p['state']=='matched_to_original_annual_fact' for p in r['core_source_matches']),4)

 def test_provider_quote_date_does_not_become_independent_price_proof(self):
  r=self.report();self.assertEqual(r['quote']['state'],'provider_dated_unverified');self.assertEqual(Decimal(r['quote']['age_hours']),20)
  for time in (None,'2026-10-14T20:00:00','2026-10-16T20:00:00Z'):
   self.assertEqual(self.report({**self.d,'quote_as_of':time})['quote']['state'],'missing_or_invalid_provider_quote')
  self.assertTrue(self.report({**self.d,'quote_as_of':'2026-10-10T20:00:00Z'})['quote']['over_24_hours'])

 def test_market_cap_reconciliation_is_not_source_verification(self):
  c=self.report()['market_cap_reconciliation'];self.assertEqual(c['state'],'provider_internal_reconciliation_only');self.assertEqual(c['implied_equity_market_cap'],'10000.00');self.assertFalse(c['independently_source_verified'])
  self.assertEqual(self.report({**self.d,'market_cap':5000})['market_cap_reconciliation']['state'],'provider_values_need_reconciliation')

 def test_scenario_differences_and_incomplete_assumptions_never_certify_forecasts(self):
  d=copy.deepcopy(self.d);d['dcf_price']+=1
  self.assertEqual(self.report(d)['conditional_valuation']['state'],'scenario_arithmetic_difference')
  d=copy.deepcopy(self.d);d['valuation_scenario']['sensitivity'].pop()
  self.assertEqual(self.report(d)['conditional_valuation']['state'],'not_reproducible')
  for scenario in (None,{},True,[],{'version':'unsupported'},{'instrument':None}):
   self.assertEqual(self.report({**self.d,'valuation_scenario':scenario})['conditional_valuation']['state'],'not_reproducible')

 def test_report_fingerprint_covers_all_supported_inputs_and_excludes_volatile_cache_age(self):
  r=self.report();content=dict(r);content.pop('content_sha256');self.assertEqual(sha(encoded(content)),r['content_sha256'])
  self.assertEqual(project({**self.d,'cache_age_hours':99}),project(self.d))
  self.assertNotEqual(self.report({**self.d,'pe_fwd':9})['input_sha256'],r['input_sha256'])

 def test_capture_exports_exact_json_and_idempotent_replay_without_account_writes(self):
  r=self.client.get('/api/number-evidence/TEST').json();body=self.request(r)
  first=self.client.post('/api/number-evidence/TEST/capture',json=body).json();again=self.client.post('/api/number-evidence/TEST/capture',json=body).json()
  self.assertTrue(first['created']);self.assertFalse(again['created'])
  result=self.client.get('/api/number-evidence/reports/'+first['id']).json()
  self.assertEqual(sha(result['report_json']),result['report_sha256']);self.assertEqual(json.loads(result['report_json']),result['report'])
  with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM paper_ledger_events').fetchone()[0],0)

 def test_changed_cache_or_context_rejects_stale_capture(self):
  r=self.client.get('/api/number-evidence/TEST').json();self.store({**self.d,'price':11.})
  self.assertEqual(self.client.post('/api/number-evidence/TEST/capture',json=self.request(r)).status_code,409)
  body=self.request(self.client.get('/api/number-evidence/TEST').json());body['context_sha256']='f'*64
  self.assertEqual(self.client.post('/api/number-evidence/TEST/capture',json=body).status_code,409)

 def test_capture_shape_and_unknown_records_fail_closed(self):
  self.assertEqual(self.client.get('/api/number-evidence/UNKNOWN').status_code,404)
  self.assertEqual(self.client.get('/api/number-evidence/reports/unknown').status_code,404)
  body=self.request(self.report());body['arbitrary_numbers']={'cash':100}
  self.assertEqual(self.client.post('/api/number-evidence/TEST/capture',json=body).status_code,422)

 def test_captured_report_is_immutable_and_tampering_returns_conflict(self):
  r=self.client.get('/api/number-evidence/TEST').json();self.client.post('/api/number-evidence/TEST/capture',json=self.request(r))
  with sqlite3.connect(self.path) as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE number_evidence_reports SET report_json='{}'")
   for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='number_evidence_reports'").fetchall():db.execute('DROP TRIGGER '+row[0])
   db.execute("UPDATE number_evidence_reports SET report_json='{}'")
  self.assertEqual(self.client.get('/api/number-evidence/reports/'+'a'*32).status_code,409)

 def test_nonfinite_public_values_are_unknown_and_original_evidence_retains_invalid_marker(self):
  d={**self.d,'price':float('nan')};r=self.report(d)
  self.assertEqual(r['retained_inputs']['price'],{'invalid_number':'nan'})
  self.assertIsNone(public_values(d)['price']);self.assertTrue(str(d['price'])=='nan')

 def test_unknown_original_context_is_not_replaced_by_future_source_evidence(self):
  r=self.client.get('/api/number-evidence/TEST').json();self.client.post('/api/number-evidence/TEST/capture',json=self.request(r))
  with sqlite3.connect(self.path) as db:db.execute('INSERT INTO saved_list_items VALUES(1,?,?)',('TEST','{}'))
  with patch('number_evidence.financial_context',side_effect=AssertionError('Future context must not be used')):
   self.assertEqual(self.client.get('/api/number-evidence/reports/'+'a'*32).status_code,200)

 def test_original_model_function_and_current_api_suppress_fabricated_zero(self):
  tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text());app=FastAPI()
  bad={**self.d,'net_income':None};self.store(bad)
  namespace={'app':app,'finite':finite,'sqlite3':sqlite3,'DB_PATH':str(self.path),'current_report':current_report,'public_values':public_values,'HTTPException':HTTPException,'gather_stock_data':Mock(return_value=bad)}
  for name in ('calc_dupont','get_stock_data'):
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[node],type_ignores=[]),'main-selected-functions','exec'),namespace)
  self.assertIsNone(namespace['calc_dupont'](None,100,100,100));self.assertIsNone(namespace['calc_dupont'](True,100,100,100))
  self.assertEqual(namespace['calc_dupont'](0,100,100,100)['roe'],0)
  result=TestClient(app).get('/api/data/TEST');self.assertEqual(result.status_code,200);self.assertIsNone(result.json()['dupont'])
  self.assertEqual(self.client.post('/api/number-evidence/TEST/capture',json=self.request(result.json()['number_evidence'])).status_code,200)
  with sqlite3.connect(self.path) as db:self.assertIsNotNone(json.loads(db.execute('SELECT data_json FROM market_data_cache').fetchone()[0])['dupont'])

 def test_ai_model_section_cannot_reintroduce_unsupported_cached_dupont(self):
  tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_advanced_frameworks_section')
  namespace={'dupont_supported':dupont_supported};exec(compile(ast.Module(body=[node],type_ignores=[]),'main-advanced-section','exec'),namespace)
  section=namespace['_advanced_frameworks_section']
  self.assertNotIn('DuPont ROE Decomposition',section({**self.d,'net_income':None}))
  self.assertNotIn('DuPont ROE Decomposition',section({**self.d,'total_equity':-1000}))
  self.assertIn('DuPont ROE Decomposition',section({**self.d,'net_income':0}))


if __name__=='__main__':unittest.main()
