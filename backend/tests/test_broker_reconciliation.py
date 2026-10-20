import copy, json, sqlite3, tempfile, unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from broker_reconciliation import init_broker, make_broker_router, reconcile, preview, commit


def fixture():
    def holding(shares,price):
        return {'ticker':'TEST','shares':str(shares),'price':str(price),'market_value':str(shares*price),
                'quote_at':'2026-10-12T20:00:00Z','price_decimals':2,'currency':'USD','asset_type':'common_equity','source':'Fixture statement holding'}
    return {'schema_version':'broker-reconciliation-v1','broker_name':'Fixture broker','account_label':'Test account',
            'currency':'USD','completeness_asserted':True,
            'opening':{'as_of':'2026-10-12T20:00:00Z','cash':'1000','nav':'1200','source':'Fixture opening','holdings':[holding(2,100)]},
            'closing':{'as_of':'2026-10-13T20:00:00Z','cash':'893','nav':'1223','source':'Fixture closing','holdings':[holding(3,110)]},
            'transactions':[
                {'event_id':'fill','kind':'buy','occurred_at':'2026-10-13T14:00:00Z','source':'Fixture trade','currency':'USD','ticker':'TEST','shares':'1','price':'100','fee':'1'},
                {'event_id':'income','kind':'dividend','occurred_at':'2026-10-13T15:00:00Z','source':'Fixture dividend','currency':'USD','ticker':'TEST','amount':'4','entitled_shares':'2','cash_per_share':'2','entitlement_at':'2026-10-12T20:00:00Z'},
                {'event_id':'tax','kind':'tax','occurred_at':'2026-10-13T16:00:00Z','source':'Fixture tax','currency':'USD','amount':'2'},
                {'event_id':'fee','kind':'fee','occurred_at':'2026-10-13T17:00:00Z','source':'Fixture fee','currency':'USD','amount':'8'}]}


class BrokerReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'test.db');init_broker(self.path)
        # A sentinel represents the virtual ledger; none of the broker APIs touch it.
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE paper_ledger_events(event_id TEXT)');db.execute("INSERT INTO paper_ledger_events VALUES('virtual-capital')")
        app=FastAPI();app.include_router(make_broker_router(self.path));self.client=TestClient(app);self.data=fixture()
    def tearDown(self):self.temp.cleanup()
    def raw(self):return json.dumps(self.data)
    def result(self):return reconcile(self.raw(),'2026-10-14T23:00:00Z')

    def test_fills_income_fees_and_taxes_reconcile_without_authenticity_claim(self):
        r=self.result();self.assertTrue(r['arithmetic_reconciled']);self.assertEqual(r['recorded_fees'],'9');self.assertEqual(r['recorded_income'],'4');self.assertEqual(r['recorded_taxes'],'2')
        self.assertEqual(r['replayed_shares'],{'TEST':'3'});self.assertEqual(r['cash_difference'],'0');self.assertIsNone(r['verified_account_return']);self.assertFalse(r['paper_ledger_changed'])
        self.assertAlmostEqual(float(r['statement_reference_return']),23/1200)

    def test_missing_transaction_blocks_return(self):
        self.data['transactions'].pop();r=self.result();self.assertFalse(r['arithmetic_reconciled']);self.assertEqual(r['cash_difference'],'8');self.assertIsNone(r['statement_reference_return'])

    def test_missing_share_action_blocks_return(self):
        self.data['closing']['holdings'][0]['shares']='4';self.data['closing']['holdings'][0]['market_value']='440';self.data['closing']['nav']='1333'
        r=self.result();self.assertEqual(r['share_differences'],{'TEST':'-1'});self.assertIsNone(r['statement_reference_return'])

    def test_split_and_sale_preserve_cash_and_shares(self):
        self.data['transactions']=[];self.data['closing']['cash']='1049';self.data['closing']['nav']='1199';self.data['closing']['holdings'][0].update(shares='3',price='50',market_value='150')
        common={'source':'Fixture source','currency':'USD'}
        self.data['transactions']=[dict(common,event_id='split',kind='split',occurred_at='2026-10-13T14:00:00Z',ticker='TEST',new_shares='2',old_shares='1'),
                                   dict(common,event_id='sale',kind='sell',occurred_at='2026-10-13T15:00:00Z',ticker='TEST',shares='1',price='50',fee='1')]
        self.assertTrue(self.result()['arithmetic_reconciled'])

    def test_offsetting_flows_do_not_create_account_return(self):
        common={'source':'Fixture source','currency':'USD','amount':'100'}
        self.data['transactions'] += [dict(common,event_id='funding',kind='deposit',occurred_at='2026-10-13T18:00:00Z'),dict(common,event_id='withdrawal',kind='withdrawal',occurred_at='2026-10-13T19:00:00Z')]
        r=self.result();self.assertTrue(r['arithmetic_reconciled']);self.assertEqual(r['net_external_funding'],'0');self.assertIsNone(r['statement_reference_return'])

    def test_unasserted_coverage_does_not_create_return(self):
        self.data['completeness_asserted']=False;self.assertIsNone(self.result()['statement_reference_return'])

    def test_unknown_corporate_actions_and_extra_fields_are_rejected(self):
        for field,value in [('kind','transfer'),('account_number','secret')]:
            changed=copy.deepcopy(self.data);changed['transactions'][0][field]=value
            with self.assertRaises(ValueError):reconcile(json.dumps(changed),'2026-10-14T23:00:00Z')

    def test_currency_margin_invalid_dividend_and_event_order_are_rejected(self):
        for key,value in [('currency','CAD'),('shares','100'),('occurred_at','2026-10-12T19:00:00Z')]:
            changed=copy.deepcopy(self.data);changed['transactions'][0][key]=value
            with self.assertRaises(ValueError):reconcile(json.dumps(changed),'2026-10-14T23:00:00Z')
        self.data['transactions'][1]['amount']='5'
        with self.assertRaises(ValueError):self.result()

    def test_dates_and_statement_price_rounding_are_explicit(self):
        with self.assertRaises(ValueError):reconcile(self.raw(),'2026-10-13T00:00:00Z')
        self.data['closing']['holdings'][0]['quote_at']='2026-10-01T20:00:00Z'
        with self.assertRaises(ValueError):self.result()
        self.data=fixture();self.data['closing']['holdings'][0]['price']='111'
        with self.assertRaises(ValueError):self.result()

    def test_preview_commit_deduplicate_and_isolate_virtual_ledger(self):
        raw=self.raw();p=self.client.post('/api/broker-reconciliation/preview',json={'data_json':raw});self.assertEqual(p.status_code,200)
        self.assertEqual(self.client.get('/api/broker-reconciliation').json()['bundles'],[])
        self.assertEqual(self.client.post('/api/broker-reconciliation/commit',json={'data_json':raw,'preview_token':'wrong'}).status_code,422)
        saved=self.client.post('/api/broker-reconciliation/commit',json={'data_json':raw,'preview_token':p.json()['preview_token']}).json();self.assertTrue(saved['created'])
        self.assertFalse(commit(self.path,raw,preview(raw)['preview_token'])['created']);self.assertEqual(self.client.get('/api/broker-reconciliation/bundles/1').status_code,200)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT * FROM paper_ledger_events').fetchall(),[('virtual-capital',)])

    def test_immutable_bundle_and_corrupt_evidence_withheld(self):
        raw=self.raw();commit(self.path,raw,preview(raw)['preview_token'])
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM broker_record_bundles')
            db.execute('DROP TRIGGER immutable_broker_record_bundles_UPDATE');db.execute("UPDATE broker_record_bundles SET result_sha256='bad'")
        self.assertEqual(self.client.get('/api/broker-reconciliation/bundles/1').status_code,409)
        r=self.client.get('/api/broker-reconciliation').json();self.assertEqual(r['bundles'],[]);self.assertEqual(len(r['integrity_errors']),1)

    def test_duplicate_fields_nonfinite_and_sensitive_errors(self):
        for raw in ('{"x":1,"x":2}','{"x":NaN}'):
            self.assertEqual(self.client.post('/api/broker-reconciliation/preview',json={'data_json':raw}).status_code,422)
        self.data['currency']='private-secret';response=self.client.post('/api/broker-reconciliation/preview',json={'data_json':self.raw()})
        self.assertEqual(response.status_code,422);self.assertNotIn('private-secret',response.text)
