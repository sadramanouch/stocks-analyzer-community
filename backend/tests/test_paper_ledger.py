import sqlite3
import tempfile
import unittest
from paper_ledger import replay, append_event, read_ledger
from portfolio_reconciliation import reconcile_position, CONFIRMED_SPLITS

def event(kind,hour,**fields):
    stamp=f'2026-01-02T{hour:02}:00:00+00:00'
    return dict(kind=kind,event_id=f'{kind}-{hour}',occurred_at=stamp,source='Unit-test fixture',price_basis='unadjusted',quote_at=stamp,**fields)

class PaperLedgerTests(unittest.TestCase):
    def test_reverse_split_preserves_wealth_and_closed_cash(self):
        events=[event('deposit',1,amount='1000'),event('buy',2,ticker='TEST',shares='100',price='2',fee='1'),event('split',3,ticker='TEST',new_shares='1',old_shares='20')]
        state=replay(events)
        self.assertEqual(float(state['marked_value']),999)
        self.assertEqual(float(state['positions'][0]['shares']),5)
        self.assertEqual(float(state['positions'][0]['mark']),40)
        state=replay(events+[event('sell',4,ticker='TEST',shares='5',price='44',fee='1')])
        self.assertEqual(float(state['cash']),1018); self.assertEqual(float(state['pnl']),18); self.assertEqual(state['positions'],[])
    def test_contributions_dividends_and_entitlement(self):
        events=[event('deposit',1,amount='100'),event('deposit',2,amount='50'),event('withdrawal',3,amount='20'),event('dividend',4,ticker='TEST',amount='2',entitled_shares='10',cash_per_share='.2',entitlement_at='2026-01-01T00:00:00Z')]
        state=replay(events)
        self.assertEqual(float(state['net_contributions']),130); self.assertEqual(float(state['pnl']),2)
        events[-1]['amount']='3'
        with self.assertRaises(ValueError): replay(events)
    def test_rejects_shorting_overspending_future_naive_nonfinite_and_adjusted(self):
        funding=event('deposit',1,amount='100')
        invalid=[event('buy',2,ticker='TEST',shares='2',price='100'),event('sell',2,ticker='TEST',shares='1',price='10'),dict(event('deposit',2,amount='10'),occurred_at='2099-01-01T00:00:00Z'),dict(event('deposit',2,amount='10'),occurred_at='2026-01-02T02:00:00'),dict(event('buy',2,ticker='TEST',shares='1',price='10'),price_basis='split_adjusted'),event('deposit',2,amount='NaN'),event('deposit',2,amount='1e1000000'),event('deposit',2,amount=True)]
        for row in invalid:
            with self.subTest(row=row),self.assertRaises(ValueError): replay([funding,row])
    def test_quote_and_event_order_validation(self):
        funding=event('deposit',1,amount='100'); buy=event('buy',2,ticker='TEST',shares='1',price='10')
        for at in ['2026-01-02T03:00:00Z','2025-12-01T00:00:00Z']:
            with self.assertRaises(ValueError): replay([funding,dict(buy,quote_at=at)])
        with self.assertRaises(ValueError): replay([event('deposit',3,amount='100'),buy])
    def test_idempotency_atomicity_and_immutability(self):
        with tempfile.TemporaryDirectory() as temp:
            path=temp+'/ledger.db'; funding=event('deposit',1,amount='100')
            self.assertTrue(append_event(path,funding)['created']); self.assertFalse(append_event(path,funding)['created'])
            with self.assertRaises(ValueError): append_event(path,dict(funding,amount='101'))
            with self.assertRaises(ValueError): append_event(path,event('withdrawal',2,amount='200'))
            self.assertEqual(read_ledger(path)['event_count'],1)
            with sqlite3.connect(path) as db:
                with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM paper_ledger_events')
                with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE paper_ledger_events SET digest='changed'")
    def test_reconciliation_is_conditional_and_same_day_entry_not_split_twice(self):
        row=dict(id=1,ticker='SVRN',status='closed',entry_date='2026-03-24',end_date='2026-04-08',entry_price=.2121,mark_price=11.48,virtual_dollars=100)
        result=reconcile_position(row,CONFIRMED_SPLITS)
        self.assertAlmostEqual(result['split_scenario_return_pct'],170.6271,places=3)
        self.assertEqual(result['status'],'unverified'); self.assertIn('Historical price basis',result['missing'])
        self.assertEqual(row['entry_price'],.2121)
        self.assertIsNone(reconcile_position(dict(row,entry_date='2026-03-30'),CONFIRMED_SPLITS)['split_scenario_return_pct'])
    def test_distribution_blocks_a_legacy_share_only_scenario(self):
        row=dict(id=1,ticker='CTVA',status='closed',entry_date='2026-05-07',end_date='2026-10-02',entry_price=100,mark_price=15,virtual_dollars=100)
        result=reconcile_position(row,CONFIRMED_SPLITS)
        self.assertIsNone(result['split_scenario_return_pct'])
        self.assertEqual(len(result['distributions']),1)
        self.assertIn('Distributed securities and entitlements; a split-only scenario would omit assets.',result['missing'])
    def test_forward_split_dividend_sale_cash_arithmetic(self):
        events=[event('deposit',1,amount='1000'),event('buy',2,ticker='TEST',shares='10',price='20',fee='.5'),event('split',3,ticker='TEST',new_shares='2',old_shares='1'),event('dividend',4,ticker='TEST',amount='10',entitled_shares='20',cash_per_share='.5',entitlement_at='2026-01-02T03:00:00Z'),event('sell',5,ticker='TEST',shares='20',price='11',fee='.5')]
        self.assertEqual(float(replay(events)['cash']),1029)
