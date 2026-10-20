import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from financial_context import financial_context, make_context_router, BALANCE, MAPPING_URL
from financial_sources import init_financial_sources, save_financial_source
from research_store import encoded

AT = '2026-10-14T18:00:00+00:00'
OBS = '2026-10-14T17:30:00+00:00'
CONCEPTS = {'revenue':'Revenues','net_income':'NetIncomeLoss','operating_cf':'NetCashProvidedByUsedInOperatingActivities','capex':'PaymentsToAcquirePropertyPlantAndEquipment'}


class FinancialContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.path = Path(self.temp.name)/'test.db'
        init_financial_sources(self.path)
        self.save({'0':{'ticker':'TEST','cik_str':1}},'0','2026-10-14T17:00:00+00:00',MAPPING_URL)
        self.payload = {'cik':1,'facts':{'us-gaap':{}}}
        for field, concept in CONCEPTS.items():
            value = {'revenue':1000,'net_income':100,'operating_cf':200,'capex':20}[field]
            self.payload['facts']['us-gaap'][concept]={'units':{'USD':[dict(self.fact(y),val=value) for y in (2025,2024,2023)]}}
        for name, concept in BALANCE.items():
            if name == 'short_term_borrowings':continue
            value = {'cash':100,'current_assets':600,'current_liabilities':300,'assets':1000,'liabilities':500,'stockholders_equity':480,'equity_including_nci':500,'debt_current_portion':25,'debt_noncurrent':100}[name]
            self.payload['facts']['us-gaap'][concept]={'units':{'USD':[dict(self.fact(y,False),val=value) for y in (2025,2024,2023)]}}
        self.item = {'ticker':'TEST','rebuilt_at':AT,'financial_period_end':'2025-12-31','input_sha256':'baseline-input',
                     'financials':{'revenue':1000,'net_income':100,'operating_cf':200,'capex':-20},
                     'financial_provenance':{k:{'concept':v} for k,v in CONCEPTS.items()}}
        self.publish()

    def tearDown(self):self.temp.cleanup()
    def fact(self,y,duration=True):
        f={'end':f'{y}-12-31','filed':'2026-02-01','accn':'0000000001-26-000001','form':'10-K'}
        if duration:f['start']=f'{y}-01-01'
        return f
    def save(self,payload,cik='1',at=OBS,url=None,status=200):
        return save_financial_source(self.path,cik,SimpleNamespace(content=json.dumps(payload).encode(),status_code=status),at,url)
    def publish(self):self.item['primary_fact_comparison']={'receipt':dict(self.save(self.payload),cik='0000000001')}
    def entries(self,concept):return self.payload['facts']['us-gaap'][concept]['units']['USD']
    def context(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row;return financial_context(db,self.item,33)
    def tamper(self,field,value):
        with sqlite3.connect(self.path) as db:
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='financial_source_documents'").fetchall():db.execute('DROP TRIGGER "'+name+'"')
            db.execute('UPDATE financial_source_documents SET '+field+'=? WHERE id=?',(value,self.item['primary_fact_comparison']['receipt']['id']))

    def test_exact_three_year_history_and_source_derived_balance_metrics(self):
        d=self.context();self.assertEqual(d['status'],'source_context_available');p=d['periods'][0]
        self.assertEqual([p['end'] for p in d['periods']],['2025-12-31','2024-12-31','2023-12-31'])
        self.assertEqual(p['metrics']['ocf_less_ppe']['value'],'180');self.assertEqual(p['metrics']['current_ratio']['value'],'2')
        self.assertEqual(Decimal(p['metrics']['net_margin']['value']),Decimal('.1'));self.assertEqual(p['metrics']['balance_equation_difference']['value'],'0')
        self.assertEqual(d['three_year_summary']['ocf_less_ppe_sum'],'540');self.assertEqual(d['three_year_summary']['positive_cashflow_periods'],3)
        self.assertEqual(d['baseline_differences'],[]);self.assertEqual(len(d['sources']),2)

    def test_content_hash_is_exact_deterministic_and_preserves_input(self):
        item=copy.deepcopy(self.item);a=self.context();b=self.context();self.assertEqual(a,b);self.assertEqual(self.item,item)
        sha=a.pop('content_sha256');self.assertEqual(sha,hashlib.sha256(encoded(a).encode()).hexdigest())
        self.assertFalse(a['trader_profitability_verified']);self.assertIsNone(a['expected_return'])

    def test_quarterly_flows_and_duration_balances_are_not_annual_instant_facts(self):
        self.entries('Revenues').append(dict(self.fact(2025),start='2025-10-01',filed='2026-09-01',val=999))
        self.entries('AssetsCurrent').append(dict(self.fact(2025),filed='2026-09-01',val=999))
        self.publish();p=self.context()['periods'][0]
        self.assertEqual(p['annual_fields']['revenue']['value'],'1000');self.assertEqual(p['balance_fields']['current_assets']['value'],'600')

    def test_eur_values_are_not_converted_or_assumed_usd(self):
        v=self.payload['facts']['us-gaap']['Revenues']['units'];v['EUR']=v.pop('USD');self.publish()
        p=self.context()['periods'][0];self.assertIsNone(p['annual_fields']['revenue']['value']);self.assertIsNone(p['metrics']['net_margin']['value'])

    def test_missing_capex_and_debt_are_unknown_not_zero(self):
        del self.payload['facts']['us-gaap'][CONCEPTS['capex']];self.publish();d=self.context()
        self.assertIsNone(d['periods'][0]['metrics']['ocf_less_ppe']['value']);self.assertIsNone(d['three_year_summary']['ocf_less_ppe_sum'])
        self.assertIsNone(d['periods'][0]['balance_fields']['short_term_borrowings']['value']);self.assertNotIn('total_debt',d['periods'][0]['metrics'])

    def test_negative_ppe_payment_is_not_turned_into_a_positive_payment(self):
        self.entries(CONCEPTS['capex'])[0]['val']=-20;self.publish();self.assertIsNone(self.context()['periods'][0]['metrics']['ocf_less_ppe']['value'])

    def test_conflicting_latest_values_are_not_resolved_by_accession_sort(self):
        self.entries('Revenues').append(dict(self.fact(2025),accn='0000000001-26-999999',val=2000));self.publish()
        p=self.context()['periods'][0];self.assertEqual(p['annual_fields']['revenue']['status'],'conflict');self.assertIsNone(p['metrics']['net_margin']['value'])

    def test_numerically_equal_duplicate_values_are_compatible(self):
        self.entries('Revenues').append(dict(self.fact(2025),val=1000.0));self.publish()
        self.assertEqual(self.context()['periods'][0]['annual_fields']['revenue']['status'],'source_matched')

    def test_future_restatement_and_quarterly_forms_do_not_replace_known_annual_values(self):
        self.entries('Revenues').extend([dict(self.fact(2025),filed='2026-10-15',val=2000),dict(self.fact(2025),form='10-Q',filed='2026-09-01',val=3000)])
        self.publish();self.assertEqual(self.context()['periods'][0]['annual_fields']['revenue']['value'],'1000')

    def test_zero_payment_is_evidence_but_zero_ratio_denominator_is_not_valid(self):
        self.entries(CONCEPTS['capex'])[0]['val']=0;self.entries('LiabilitiesCurrent')[0]['val']=0;self.publish();p=self.context()['periods'][0]
        self.assertEqual(p['metrics']['ocf_less_ppe']['value'],'200');self.assertIsNone(p['metrics']['current_ratio']['value'])

    def test_different_filings_or_durations_block_combined_metrics(self):
        for mutation in ({'accn':'0000000001-26-000002'},{'start':'2025-02-01'}):
            with self.subTest(mutation=mutation):
                self.entries(CONCEPTS['capex'])[0]=dict(self.fact(2025),val=20,**mutation);self.publish()
                p=self.context()['periods'][0];self.assertFalse(p['annual_inputs_coherent']);self.assertIsNone(p['metrics']['ocf_less_ppe']['value'])

    def test_balance_ratio_does_not_mix_filing_accessions(self):
        self.entries('AssetsCurrent')[0]['accn']='0000000001-26-000002';self.publish();self.assertIsNone(self.context()['periods'][0]['metrics']['current_ratio']['value'])

    def test_missing_middle_year_is_not_filled_with_a_different_year_or_balance_date(self):
        for c in CONCEPTS.values():self.payload['facts']['us-gaap'][c]['units']['USD']=[f for f in self.entries(c) if f['end']!='2024-12-31']
        self.publish();d=self.context();self.assertIsNone(d['periods'][1]['end']);self.assertIsNone(d['periods'][1]['metrics']['current_ratio']['value']);self.assertIsNone(d['three_year_summary']['ocf_less_ppe_sum'])

    def test_ambiguous_prior_year_does_not_combine_different_annual_ends(self):
        self.entries('Revenues').append(dict(self.fact(2024),end='2024-12-28',val=1001));self.publish();p=self.context()['periods'][1]
        self.assertIsNone(p['end']);self.assertEqual(p['annual_fields']['revenue']['status'],'conflict');self.assertIsNone(p['metrics']['current_ratio']['value'])

    def test_parent_equity_is_not_substituted_for_total_equity_in_equation(self):
        del self.payload['facts']['us-gaap']['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'];self.publish();p=self.context()['periods'][0]
        self.assertEqual(p['balance_fields']['stockholders_equity']['value'],'480');self.assertIsNone(p['metrics']['balance_equation_difference']['value'])

    def test_negative_cash_is_reported_but_flagged_for_review(self):
        self.entries('CashAndCashEquivalentsAtCarryingValue')[0]['val']=-1;self.publish()
        self.assertEqual(self.context()['periods'][0]['balance_fields']['cash']['status'],'requires_review')

    def test_unknown_exact_ticker_and_duplicate_issuer_mapping_fail_closed(self):
        for directory in ({'0':{'ticker':'OTHER','cik_str':1}},{'0':{'ticker':'TEST','cik_str':2}},{'0':{'ticker':'TEST','cik_str':1},'1':{'ticker':'TEST','cik_str':2}}):
            with self.subTest(directory=directory):
                self.save(directory,'0','2026-10-14T17:29:00+00:00',MAPPING_URL);self.assertEqual(self.context()['status'],'unavailable')

    def test_future_directory_does_not_change_a_saved_context(self):
        before=self.context();self.save({'0':{'ticker':'TEST','cik_str':2}},'0','2026-10-14T17:45:00+00:00',MAPPING_URL)
        self.assertEqual(self.context(),before)

    def test_source_bytes_metadata_and_issuer_tampering_are_detected(self):
        for field,value in (('body_gzip',b'broken'),('sha256','x'*64),('observed_at','2026-10-14T19:00:00+00:00'),('source_url','https://invalid.example/facts')):
            with self.subTest(field=field):
                self.publish();self.tamper(field,value);self.assertEqual(self.context()['status'],'unavailable')
                with sqlite3.connect(self.path) as db:db.execute('DELETE FROM financial_source_documents WHERE id=?',(self.item['primary_fact_comparison']['receipt']['id'],))

    def test_payload_issuer_and_nonfinite_values_are_not_accepted(self):
        self.payload['cik']=2;self.publish();self.assertEqual(self.context()['status'],'unavailable')
        self.payload['cik']=1;self.entries('NetIncomeLoss')[0]['val']=float('nan');self.publish()
        self.assertIsNone(self.context()['periods'][0]['annual_fields']['net_income']['value'])

    def test_source_values_differing_from_saved_inputs_are_visible(self):
        self.item['financials']['revenue']=999;d=self.context();self.assertEqual(d['baseline_differences'],['revenue']);self.assertTrue(d['issues'])

    def test_missing_or_legacy_source_is_explicit(self):
        self.item['primary_fact_comparison']=None;self.assertEqual(self.context()['status'],'unavailable')
        self.item.pop('rebuilt_at');self.assertEqual(self.context()['status'],'unavailable')

    def test_receipt_with_no_supported_facts_is_not_called_available_evidence(self):
        self.payload['facts']={'us-gaap':{}};self.publish();d=self.context()
        self.assertEqual(d['status'],'no_supported_facts');self.assertEqual(d['coverage']['source_matched_facts'],0)

    def test_damaged_deflate_and_excessive_amounts_fail_closed(self):
        self.tamper('body_gzip',b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\xff\xff\xff\xff')
        self.assertEqual(self.context()['status'],'unavailable')
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM financial_source_documents WHERE id=?',(self.item['primary_fact_comparison']['receipt']['id'],))
        self.entries('NetIncomeLoss')[0]['val']='1e100';self.publish();self.assertIsNone(self.context()['periods'][0]['metrics']['net_margin']['value'])

    def test_complete_individual_years_with_a_gap_are_not_called_consecutive(self):
        for c in CONCEPTS.values():self.entries(c)[0]['start']='2025-01-02'
        self.publish();d=self.context();self.assertTrue(d['periods'][0]['annual_inputs_coherent'])
        self.assertIsNone(d['three_year_summary']['ocf_less_ppe_sum'])

    def test_routes_use_selected_saved_edition_and_reject_unknown_records(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE saved_list_versions(id INTEGER PRIMARY KEY)');db.execute('INSERT INTO saved_list_versions VALUES(33)')
            db.execute('CREATE TABLE saved_list_items(version_id,ticker,payload_json)');db.execute('INSERT INTO saved_list_items VALUES(?,?,?)',(33,'TEST',encoded(self.item)))
        app=FastAPI();app.include_router(make_context_router(self.path));client=TestClient(app)
        r=client.get('/api/financial-context/test?version=33');self.assertEqual(r.status_code,200);self.assertEqual(r.json(),self.context())
        self.assertEqual(client.get('/api/financial-context/TEST?version=32').status_code,404);self.assertEqual(client.get('/api/financial-context/OTHER').status_code,404)
        self.assertEqual(client.get('/api/financial-context/TEST?version=0').status_code,422)

    def test_saved_decision_freezes_context_without_mutating_caller_or_old_rows(self):
        from equity_workspace import init_workspace,save_decision
        init_workspace(self.path)
        data={'ticker':'TEST','research':self.item,'research_version':33,'observed_at':AT}
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row;identifier=save_decision(db,data,'research');row=db.execute('SELECT * FROM equity_decisions WHERE id=?',(identifier,)).fetchone()
            captured=json.loads(row['input_json']);self.assertEqual(captured['financial_context'],self.context());self.assertNotIn('financial_context',data)
            self.assertEqual(hashlib.sha256(row['input_json'].encode()).hexdigest(),row['input_sha256'])
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE equity_decisions SET ticker=? WHERE id=?',('OTHER',identifier))


if __name__=='__main__':unittest.main()
