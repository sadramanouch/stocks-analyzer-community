import hashlib,json,sqlite3,unittest
import test_equity_workspace as fixture
from market_history import gap_review,init_market_history
from research_store import identities

class MarketGapTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.EquityWorkspaceTests();self.fx.setUp();self.path=self.fx.path;init_market_history(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS paper_positions(ticker TEXT,entry_date TEXT)''')
            db.execute("INSERT INTO paper_positions(ticker,entry_date) VALUES('WTEST','2026-10-01')")
            body=json.dumps({'chart':{'result':None,'error':{'code':'Not Found'}}}).encode()
            db.execute('INSERT INTO market_source_documents VALUES(?,?,?,?,?,?,?,?,?)',('missing','WTEST','2026-10-14T00:00:00Z','https://example.invalid/chart/WTEST','yahoo_original_chart',404,'{}',hashlib.sha256(body).hexdigest(),body))
    def tearDown(self):self.fx.tearDown()
    def test_failed_request_and_unresolved_identity_remain_distinct(self):
        r=gap_review(self.path);row=next(g for g in r['gaps'] if g['ticker']=='WTEST')
        self.assertEqual(row['source_state'],'source_unavailable_for_request');self.assertEqual(row['model_state'],'historical_identity_unresolved')
        self.assertTrue(row['latest_receipt']['hash_verified']);self.assertIsNone(r['verified_account_return']);self.assertIn('does not prove delisting',r['notice'])
    def test_corrupt_receipt_withheld(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET sha256='bad' WHERE id='missing'")
        row=next(g for g in gap_review(self.path)['gaps'] if g['ticker']=='WTEST')
        self.assertEqual(row['source_state'],'source_integrity_failed');self.assertFalse(row['latest_receipt']['hash_verified']);self.assertIsNone(row['reported_chart_metadata'])
