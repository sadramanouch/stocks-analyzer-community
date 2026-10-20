"""Isolated regression tests for accounting labels, cache generations and auth."""
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from financial_integrity import SCHEMA_VERSION


class AuditIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATA_DIR': self.temp.name}):
            self.main = importlib.import_module('main')
        self.db_patch = patch.object(self.main, 'DB_PATH', self.temp.name + '/audit.db')
        self.db_patch.start()
        self.main.init_db()
        self.main._mem_cache.clear()
        self.client = TestClient(self.main.app)

    def tearDown(self):
        self.main._mem_cache.clear()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_header_auth_no_longer_accepts_query_keys_and_fails_closed_on_fly(self):
        with patch.object(self.main, 'API_SECRET', 'audit-key'):
            self.assertEqual(self.client.get('/api/dashboard?api_key=audit-key').status_code, 401)
            self.assertEqual(self.client.get('/api/dashboard', headers={'X-API-Key': 'audit-key'}).status_code, 200)
        with patch.object(self.main, 'API_SECRET', ''), patch.dict(os.environ, {'FLY_APP_NAME': 'test'}):
            self.assertEqual(self.client.get('/api/dashboard').status_code, 503)
            self.assertEqual(self.client.get('/health').status_code, 200)

    def test_market_cache_rejects_old_generation_and_future_dates(self):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.executemany('INSERT INTO market_data_cache VALUES(?,?,?)', [
                ('OLD', json.dumps({'_fw_version': 1}), now),
                ('FUTURE', json.dumps({'_fw_version': SCHEMA_VERSION}), (datetime.now()+timedelta(days=2)).isoformat()),
            ])
        for ticker in ['OLD', 'FUTURE']:
            self.assertIsNone(self.main._get_cached_market_data(ticker, allow_stale=True))
        self.main._save_market_data_cache('NEW', {'price': 12}, sync_sqlite=True)
        self.main._mem_cache.clear()
        data = self.main._get_cached_market_data('NEW')
        self.assertEqual(data['price'], 12)
        self.assertIn('market_data_fetched_at', data)
        self.assertLess(data['cache_age_hours'], 1)

    def test_xbrl_old_cache_does_not_bypass_fixed_parser(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.execute('INSERT INTO xbrl_cache VALUES(?,?,?)', ('123', '{"revenue":999}', datetime.now().isoformat()))
        with patch.object(self.main.requests, 'get', side_effect=RuntimeError('offline')) as fetch:
            self.assertEqual(self.main.fetch_xbrl_fundamentals('123'), {})
            fetch.assert_called_once()

    def test_annual_freshness_is_not_claimed_to_be_a_recent_quote(self):
        annual = self.main.calc_freshness((datetime.now()-timedelta(days=375)).date().isoformat(), '10-K')
        self.assertEqual(annual['date_basis'], 'period_end')
        self.assertEqual(annual['score'], 75)
        self.assertIn('not filing or quote', annual['warning'])
        self.assertEqual(self.main.calc_freshness('2099-01-01', '10-K')['score'], 0)

    def test_summary_separates_open_cost_from_cumulative_allocations_and_flags_outlier(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.execute("INSERT INTO company_analyses(ticker,current_price,analyzed_at) VALUES('OPEN',110,'2020-01-01')")
            db.execute("INSERT INTO paper_positions(ticker,entry_price,entry_date,entry_signal) VALUES('OPEN',100,'2020-01-01','BUY')")
            db.execute("INSERT INTO paper_positions(ticker,entry_price,entry_date,entry_signal) VALUES('MISSING',100,'2020-01-01','HOLD')")
            db.execute("INSERT INTO paper_positions(ticker,status,entry_price,entry_date,entry_signal,close_date,realized_return_pct) VALUES('SPLIT','closed',1,'2020-01-01','BUY','2020-02-01',5000)")
        summary = self.main.get_portfolio_summary()
        self.assertEqual(summary['open_deployed_usd'], 200)
        self.assertEqual(summary['open_value_usd'], 210)
        self.assertEqual(summary['open_return_pct'], 5)
        integrity = summary['integrity']
        self.assertEqual(integrity['status'], 'unverified')
        self.assertEqual(integrity['cumulative_position_allocations_usd'], 300)
        self.assertEqual(integrity['missing_price_count'], 1)
        self.assertEqual(integrity['older_analysis_count'], 2)
        self.assertEqual(integrity['outlier_count'], 1)

    def test_learning_min_days_excludes_short_holds(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            for days in [2, 60]:
                for i in range(10):
                    db.execute("INSERT INTO paper_positions(ticker,status,entry_price,entry_date,entry_signal,close_date,realized_return_pct) VALUES(?,'closed',1,?,'BUY','2026-01-01',10)",
                               (f'T{days}_{i}', (datetime(2026,1,1)-timedelta(days=days)).date().isoformat()))
        self.assertEqual(self.main.run_learning_analysis(0)['positions_analyzed'], 20)
        filtered = self.main.run_learning_analysis(30)
        self.assertEqual(filtered['positions_analyzed'], 10)
        self.assertIn('not recommended', filtered['interpretation'])
        with self.assertRaises(HTTPException): self.main.run_learning_analysis(90)
        with self.assertRaises(HTTPException): self.main.run_learning_analysis(-1)

    def test_missing_polygon_key_makes_no_external_request(self):
        with patch.object(self.main, 'POLYGON_API_KEY', ''), patch.object(self.main.requests, 'get') as fetch:
            self.assertIsNone(self.main._polygon_price('TEST'))
            fetch.assert_not_called()

    def test_missing_debt_disables_dependent_valuation_models(self):
        data = self.main.score_from_xbrl_and_market('TEST', {'operating_cf': 100, 'capex': 10, 'cash': 20, 'long_term_debt': 10, 'shares': 100, 'ebit': 30}, {'currentPrice': 5, 'marketCap': 100_000_000})
        self.assertIsNone(data['total_debt'])
        self.assertIsNone(data['dcf_price'])
        self.assertIsNone(data['epv'])
        self.assertIsNone(data['roic_wacc'])

    def test_ledger_api_validation_idempotency_and_persistence(self):
        event={'event_id':'api-funding','kind':'deposit','amount':'100','occurred_at':'2026-01-01T00:00:00Z','source':'API test fixture'}
        self.assertEqual(self.client.post('/api/paper-ledger/events',json=event).status_code,200)
        self.assertFalse(self.client.post('/api/paper-ledger/events',json=event).json()['created'])
        self.assertEqual(self.client.post('/api/paper-ledger/events',json=dict(event,amount='200')).status_code,422)
        state=self.client.get('/api/paper-ledger').json()
        self.assertEqual(state['event_count'],1); self.assertEqual(float(state['cash']),100)
        self.assertEqual(self.client.get('/api/portfolio/reconciliation').json()['total'],0)

    def test_rankings_share_filtered_universe_and_tied_ranks(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            for ticker,name,score,cap in [('NOTE','Example 8% Senior Notes',99,100e6),('AAA','Common Alpha',70,100e6),('BBB','Common Beta',70,100e6),('LOW','Microcap',90,10e6)]:
                db.execute("INSERT INTO company_analyses(ticker,company_name,quant_score,market_cap,analyzed_at) VALUES(?,?,?,?,datetime('now'))",(ticker,name,score,cap))
        board=self.main.get_leaderboard()
        self.assertEqual([r['ticker'] for r in board['results']],['AAA','BBB'])
        self.assertEqual([r['rank'] for r in board['results']],[1,1])
        self.assertEqual(self.main.get_ticker_rankings('BBB')['rankings']['quant_score']['percentile'],board['results'][1]['percentile'])
        self.assertFalse(self.main.get_ticker_rankings('NOTE')['eligible'])
        self.assertEqual(len(self.main.get_dashboard()['companies']),4)
        with self.assertRaises(HTTPException): self.main.get_leaderboard(limit=-1)

    def test_legacy_allocator_is_opt_in_and_excludes_hold_and_notes(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            for ticker,name,signal in [('BUY','Example Common Stock','BUY'),('HOLD','Example Common','HOLD'),('NOTE','Example Senior Notes','BUY')]:
                db.execute("INSERT INTO company_analyses(ticker,company_name,signal,current_price,analyzed_at) VALUES(?,?,?,?,datetime('now'))",(ticker,name,signal,10))
        with patch.dict(os.environ,{'ENABLE_LEGACY_PAPER_ALLOCATION':'0'}):
            self.assertEqual(self.main._open_portfolio_positions(),0)
        with patch.dict(os.environ,{'ENABLE_LEGACY_PAPER_ALLOCATION':'1'}):
            self.assertEqual(self.main._open_portfolio_positions(),1)
            self.assertEqual(self.main._open_portfolio_positions(),0)

    def test_current_shares_replace_stale_annual_shares_in_per_share_models(self):
        info=dict(longName='Example Common Stock',quoteType='EQUITY',sharesOutstanding=100,currentPrice=20,marketCap=2000,beta=1)
        facts=dict(revenue=1100,revenue_prior=1000,net_income=110,net_income_prior=100,operating_cf=120,operating_cf_prior=100,capex=20,shares=10000,ebit=150,ebit_prior=140,cash=20,debt_noncurrent=10,debt_current_portion=0,short_term_borrowings=0)
        data=self.main.score_from_xbrl_and_market('TEST',facts,info)
        self.assertEqual(data['valuation_scenario']['shares'],100)
        self.assertEqual(data['dcf_price'],data['valuation_scenario']['price'])
        excluded=self.main.score_from_xbrl_and_market('NOTE',facts,dict(info,longName='Example Preferred Shares'))
        self.assertIsNone(excluded['dcf_price']); self.assertIsNone(excluded['avg_fair_value'])
        self.assertEqual(self.main.calc_quant_signal(excluded)[0],'UNAVAILABLE')

    def test_sec_refresh_failure_retains_stored_filings_without_claiming_freshness(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.execute("INSERT INTO companies(ticker,cik,last_updated) VALUES('TEST','0000000123','2020-01-01T00:00:00')")
            company_id=db.execute("SELECT id FROM companies WHERE ticker='TEST'").fetchone()[0]
            db.execute("INSERT INTO filings(company_id,form_type,filed_date,period_of_report,accession_number,url) VALUES(?,'10-K','2019-12-01','2019-09-30','fixture-accession','https://www.sec.gov/fixture')",(company_id,))
        with patch.object(self.main,'fetch_edgar_filings',side_effect=HTTPException(503,'SEC unavailable')):
            response=self.client.get('/api/filings/test')
        self.assertEqual(response.status_code,200)
        result=response.json()
        self.assertEqual(result['total'],1); self.assertTrue(result['stale']); self.assertTrue(result['cached'])
        self.assertEqual(result['cache_as_of'],'2020-01-01T00:00:00')
        self.assertEqual(result['summary']['last_10k']['filed_date'],'2019-12-01')
        self.assertIn('newer filings may be missing',result['notice'])
        with sqlite3.connect(self.main.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT last_updated FROM companies WHERE ticker='TEST'").fetchone()[0],'2020-01-01T00:00:00')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM filings').fetchone()[0],1)

    def test_sec_failure_without_saved_filings_remains_an_error(self):
        with patch.object(self.main,'fetch_edgar_filings',side_effect=HTTPException(503,'SEC unavailable')):
            self.assertEqual(self.client.get('/api/filings/UNKNOWN').status_code,503)
