import json, sqlite3, unittest
from datetime import timedelta
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch
from equity_workspace import make_preview, commit_preview
from forward_performance import init_forward, capture, overview, make_forward_router, fingerprint
from paper_ledger import prepare_import
from research_store import encoded, instant
import test_equity_workspace as workspace_fixture
from test_source_history import AT


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.fx=workspace_fixture.EquityWorkspaceTests();self.fx.setUp();self.path=self.fx.path;init_forward(self.path)
        self.t=instant(AT)
        self.add({'event_id':'capital','kind':'deposit','currency':'USD','amount':'10000','occurred_at':AT,'source':'Explicit fixture virtual cash'})
        app=FastAPI();app.include_router(make_forward_router(self.path));self.client=TestClient(app)

    def tearDown(self):self.fx.tearDown()

    def at(self,seconds):return (self.t+timedelta(seconds=seconds)).isoformat()

    def add(self,event):
        with sqlite3.connect(self.path) as db:
            new,_=prepare_import(db,[event])
            for e,raw,sha in new:db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)',(e['event_id'],e['occurred_at'],e['occurred_at'],raw,sha))

    def status(self):
        with sqlite3.connect(self.path) as db:return overview(db)

    def buy(self,seconds=2):
        self.fx.item['quote_as_of']=self.at(seconds-1);self.fx.store(self.fx.item)
        p=make_preview(self.path,'TEST','buy','1','fundamentals',self.at(seconds));commit_preview(self.path,p['id'],self.at(seconds))
        # Test clock: real recording occurs today; make it match the fixture clock.
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER paper_ledger_no_update');db.execute('UPDATE paper_ledger_events SET recorded_at=occurred_at')
        return p

    def test_one_observation_has_no_return_and_exports_exact_input(self):
        c=capture(self.path,AT);self.assertEqual(c['account']['marked_value'],'10000');self.assertIsNone(self.status()['reference_time_weighted_return'])
        r=self.client.get('/api/forward-performance/checkpoints/1');self.assertEqual(r.status_code,200)
        self.assertEqual(fingerprint(encoded(r.json()['inputs'])),c['input_sha256'])
        self.assertEqual(self.client.get('/api/forward-performance/checkpoints/999').status_code,404)

    def test_cash_deposits_are_not_profit_and_withdrawals_are_not_loss(self):
        capture(self.path,AT)
        self.add({'event_id':'deposit','kind':'deposit','currency':'USD','amount':'1000','occurred_at':self.at(1),'source':'Fixture external cash'})
        self.add({'event_id':'withdraw','kind':'withdrawal','currency':'USD','amount':'200','occurred_at':self.at(2),'source':'Fixture external cash'})
        capture(self.path,self.at(3));s=self.status()
        self.assertEqual(Decimal(s['reference_time_weighted_return']),0);self.assertEqual(s['intervals'][0]['external_flow'],'800')
        self.assertEqual(s['evidence_state'],'cash_only_no_strategy_results');self.assertIsNone(s['verified_account_return']);self.assertIsNone(s['benchmark_return'])

    def test_exact_costs_and_ledger_extension_produce_paper_return(self):
        capture(self.path,AT);p=self.buy();c=capture(self.path,self.at(3));s=self.status()
        self.assertEqual(c['issues'],[]);expected=(Decimal(p['result']['marked_value'])/10000)-1
        self.assertEqual(Decimal(s['reference_time_weighted_return']),expected);self.assertLess(expected,0)
        self.assertEqual(s['evidence_state'],'paper_reference_only')

    def test_flow_with_holdings_requires_boundary_valuation(self):
        self.buy();capture(self.path,self.at(3))
        self.add({'event_id':'held-flow','kind':'deposit','currency':'USD','amount':'1000','occurred_at':self.at(4),'source':'Fixture cash'})
        self.fx.item['quote_as_of']=self.at(4);self.fx.store(self.fx.item)
        with patch('equity_workspace.now',lambda:self.at(5)):
            self.assertEqual(self.fx.client.post('/api/equity-workspace/paper/marks').status_code,200)
        capture(self.path,self.at(6));s=self.status();self.assertIsNone(s['reference_time_weighted_return'])
        self.assertIn('boundary valuation',' '.join(s['intervals'][0]['issues']))

    def test_backdated_addition_never_rewrites_measured_history(self):
        capture(self.path,self.at(3));self.add({'event_id':'late','kind':'deposit','currency':'USD','amount':'100','occurred_at':self.at(2),'source':'Late recorded fixture'})
        capture(self.path,self.at(4));s=self.status();self.assertIsNone(s['reference_time_weighted_return']);self.assertIn('backdated',' '.join(s['intervals'][0]['issues']))

    def test_manual_trade_cannot_impersonate_frozen_preview(self):
        capture(self.path,AT);self.add({'event_id':'forward-paper-fake','kind':'buy','currency':'USD','ticker':'TEST','shares':'1','price':'100','fee':'0','quote_at':AT,'occurred_at':self.at(2),'source':'Claimed price','price_basis':'unadjusted'})
        c=capture(self.path,self.at(3));self.assertEqual(c['reference_status'],'blocked');self.assertIn('absent',' '.join(c['issues']))
        self.assertIsNone(self.status()['reference_time_weighted_return'])

    def test_stale_marks_block_and_tampered_ledger_aborts(self):
        self.buy();c=capture(self.path,self.at(90000));self.assertIn('24 hours',' '.join(c['issues']))
        with sqlite3.connect(self.path) as db:db.execute("UPDATE paper_ledger_events SET digest='bad' WHERE event_id='capital'")
        with self.assertRaisesRegex(ValueError,'fingerprint'):capture(self.path,self.at(90001))

    def test_snapshot_is_immutable_and_tamper_detected(self):
        capture(self.path,AT)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM forward_checkpoints')
            db.execute('DROP TRIGGER immutable_forward_checkpoints_update');db.execute("UPDATE forward_checkpoints SET result_json='{}'")
        self.assertEqual(self.client.get('/api/forward-performance/checkpoints/1').status_code,409)
        self.assertEqual(len(self.status()['integrity_errors']),1);self.assertIsNone(self.status()['reference_time_weighted_return'])

    def test_same_timestamp_and_zero_capital_block(self):
        capture(self.path,AT)
        with self.assertRaisesRegex(ValueError,'increasing'):capture(self.path,AT)
        self.add({'event_id':'all-out','kind':'withdrawal','currency':'USD','amount':'10000','occurred_at':self.at(2),'source':'Fixture withdrawal'})
        capture(self.path,self.at(3));self.assertIsNone(self.status()['reference_time_weighted_return'])

    def test_recorded_after_observation_and_unfunded_rejected(self):
        with self.assertRaisesRegex(ValueError,'after the observation'):capture(self.path,self.at(-1))
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER paper_ledger_no_delete');db.execute('DELETE FROM paper_ledger_events')
        self.assertEqual(self.client.post('/api/forward-performance/capture').status_code,422)

    def test_independent_replay_checks_arithmetic_even_with_rehashed_output(self):
        capture(self.path,AT)
        with sqlite3.connect(self.path) as db:
            r=json.loads(db.execute('SELECT result_json FROM forward_checkpoints').fetchone()[0]);r['account']['cash']='10001';raw=encoded(r)
            db.execute('DROP TRIGGER immutable_forward_checkpoints_update');db.execute('UPDATE forward_checkpoints SET result_json=?,result_sha256=?',(raw,fingerprint(raw)))
        self.assertIn('arithmetic',self.status()['integrity_errors'][0]['message'])

    def test_repeat_stale_price_observation_is_not_a_measured_zero_return(self):
        self.buy();capture(self.path,self.at(3));capture(self.path,self.at(4))
        self.assertIsNone(self.status()['reference_time_weighted_return'])
        self.assertIn('Newer dated',' '.join(self.status()['intervals'][0]['issues']))
