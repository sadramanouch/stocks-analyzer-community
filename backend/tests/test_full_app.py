"""Integration checks using the actual app, auth middleware and migrated schema."""
import importlib
import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from trade_evidence import now_utc, save_records


class FullAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATA_DIR': cls.temp.name, 'API_SECRET': 'integration-test-key'}):
            cls.main = importlib.reload(importlib.import_module('main'))
        cls.main.init_db()
        cls.client = TestClient(cls.main.app)
        cls.headers = {'X-API-Key': 'integration-test-key'}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_auth_applies_to_reads_writes_and_raw_evidence(self):
        for method, path, args in [
            ('get', '/api/system-status', {}),
            ('get', '/api/trade-evidence', {}),
            ('get', '/api/source-campaigns', {}),
            ('post', '/api/source-campaigns/financial/start', {}),
            ('post', '/api/source-campaigns/1/resume', {}),
            ('get', '/api/source-campaigns/1/scope', {}),
            ('post', '/api/trade-evidence/sync/congress', {}),
            ('put', '/api/trade-evidence/missing/watch', {'json': {'watched': True}}),
            ('get', '/api/trade-evidence/missing/evidence', {}),
            ('get', '/api/trade-history/actors', {}),
            ('post', '/api/trade-history/sec/sync', {'json': {'ticker':'AAPL'}}),
            ('get', '/api/trade-history/legacy', {}),
            ('get', '/api/trade-history/legacy/documents/missing', {}),
            ('post', '/api/trade-history/legacy/review', {'json': {'limit':1}}),
            ('get', '/api/performance-audit', {}),
            ('post', '/api/performance-audit', {}),
            ('get', '/api/performance-audit/1/inputs', {}),
            ('get', '/api/cashflow-evidence/GME', {}),
            ('get', '/api/statement-checks/GME', {}),
            ('get', '/api/statement-checks/reports/missing', {}),
            ('get', '/api/statement-checks/sources/missing', {}),
            ('post', '/api/statement-checks/GME/capture', {'json':{}}),
            ('get', '/api/security-identity/reports/missing', {}),
            ('get', '/api/security-identity/sources/missing', {}),
            ('post', '/api/security-identity/GME/collect', {'json':{}}),
            ('post', '/api/cashflow-evidence/GME/capture', {'json':{}}),
            ('get', '/api/cashflow-evidence/reports/missing', {}),
            ('get', '/api/quote-checks/GME', {}),
            ('post', '/api/quote-checks/GME/capture', {'json':{}}),
            ('get', '/api/quote-checks/reports/missing', {}),
            ('get', '/api/quote-checks/sources/missing', {}),
            ('get', '/api/market-history', {}),
            ('post', '/api/market-history/refresh', {'json':{'tickers':['MSFT']}}),
            ('get', '/api/market-history/documents/missing', {}),
            ('get', '/api/copy-study', {}),
            ('post', '/api/copy-study', {}),
            ('post', '/api/paper-ledger/import/preview', {'json':{'events':[]}}),
            ('post', '/api/paper-ledger/import/commit', {'json':{'events':[]}}),
            ('get', '/api/broker-reconciliation', {}),
            ('get', '/api/broker-flex', {}),
            ('get', '/api/market-history/gaps', {}),
            ('post', '/api/broker-flex/preview', {'json':{}}),
            ('post', '/api/broker-flex/commit', {'json':{}}),
            ('get', '/api/broker-flex/bundles/1', {}),
            ('post', '/api/broker-reconciliation/preview', {'json':{'data_json':'{}'}}),
            ('post', '/api/broker-reconciliation/commit', {'json':{'data_json':'{}'}}),
            ('get', '/api/broker-reconciliation/bundles/1', {}),
            ('get', '/api/benchmark-study', {}),
            ('post', '/api/benchmark-study/register', {}),
            ('post', '/api/benchmark-study/collect', {}),
            ('get', '/api/benchmark-study/observations/1', {}),
            ('get', '/api/forward-performance', {}),
            ('post', '/api/forward-performance/capture', {}),
            ('get', '/api/forward-performance/checkpoints/1', {}),
            ('get', '/api/research/coverage', {}),
            ('post', '/api/research/coverage/refresh', {}),
            ('get', '/api/equity-workspace', {}),
            ('get', '/api/equity-workspace/decision/TEST', {}),
            ('get', '/api/equity-workspace/decisions/missing', {}),
            ('post', '/api/equity-workspace/monitor/run', {}),
            ('put', '/api/equity-workspace/watch/TEST', {'json':{'enabled':True}}),
            ('post', '/api/equity-workspace/paper/fund', {'json':{'amount':'10000'}}),
            ('post', '/api/equity-workspace/paper/preview', {'json':{'ticker':'TEST','side':'buy','shares':'1'}}),
            ('post', '/api/equity-workspace/paper/commit/missing', {}),
            ('post', '/api/equity-workspace/paper/marks', {}),
        ]:
            self.assertEqual(getattr(self.client, method)(path, **args).status_code, 401)
        self.assertEqual(self.client.get('/api/trade-evidence', headers={'X-API-Key': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_system_status_reads_real_schema_without_starting_collectors(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            before = db.execute('SELECT COUNT(*) FROM broker_flex_downloads').fetchone()[0]
        response = self.client.get('/api/system-status', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['scheduler_state'], 'unavailable')
        self.assertFalse(data['all_expected_jobs_registered'])
        self.assertEqual(len(data['jobs']), 9)
        with sqlite3.connect(self.main.DB_PATH) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM broker_flex_downloads').fetchone()[0], before)

    def test_real_schema_dashboard_and_watch_cycle(self):
        now = now_utc()
        row = dict(Representative='Integration Fixture', Ticker='TEST', Transaction='Purchase',
                   TransactionDate=(now-timedelta(days=2)).date().isoformat(),
                   ReportDate=(now-timedelta(days=1)).date().isoformat(), TickerType='CS')
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.execute('INSERT OR REPLACE INTO company_analyses(ticker,quant_score,fcf_yield,red_flags_high,analyzed_at) VALUES(?,?,?,?,?)',
                       ('TEST', 70, .05, 0, now.isoformat()))
        self.client.get('/api/trade-evidence', headers=self.headers)
        save_records(self.main.DB_PATH, 'congress', [row], now)
        result = self.client.get('/api/trade-evidence', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        event = result.json()['events'][0]
        self.assertEqual(event['priority'], 'needs_evidence')
        self.assertTrue(any('Original filing' in reason for reason in event['reasons']))
        for watched in [True, False]:
            response = self.client.put(f"/api/trade-evidence/{event['id']}/watch", headers=self.headers, json={'watched': watched})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get('/api/trade-evidence', headers=self.headers).json()['events'][0]['watched'], watched)
        evidence = self.client.get(f"/api/trade-evidence/{event['id']}/evidence", headers=self.headers)
        self.assertEqual(evidence.json()['observations'][0]['provider_record'], row)

    def test_migrations_are_repeatable_and_legacy_routes_survive(self):
        self.main.init_db()
        for route in ['/api/dashboard', '/api/leaderboard', '/api/portfolio/summary']:
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 200, f'{route}: {response.text}')

    def test_cors_preflight_is_allowed_but_not_for_untrusted_origin(self):
        response = self.client.options('/api/trade-evidence', headers={
            'Origin':'http://localhost:3000', 'Access-Control-Request-Method':'GET',
            'Access-Control-Request-Headers':'X-API-Key'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:3000')
        denied = self.client.options('/api/trade-evidence', headers={
            'Origin':'https://untrusted.invalid', 'Access-Control-Request-Method':'GET'})
        self.assertNotIn('access-control-allow-origin', denied.headers)

    def test_dashboard_exposes_saved_fcf_and_missing_values(self):
        with sqlite3.connect(self.main.DB_PATH) as db:
            db.execute("INSERT OR REPLACE INTO company_analyses(ticker,fcf_yield,analyzed_at) VALUES(?,?,?)", ('FCFTEST', .06, now_utc().isoformat()))
            db.execute("INSERT OR REPLACE INTO company_analyses(ticker,fcf_yield,analyzed_at) VALUES(?,?,?)", ('MISSING', None, now_utc().isoformat()))
        response = self.client.get('/api/dashboard', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        rows = {row['ticker']: row for row in response.json()['companies']}
        self.assertEqual(rows['FCFTEST']['fcf_yield'], .06)
        self.assertIsNone(rows['MISSING']['fcf_yield'])

    def test_market_cache_persists_explicit_utc_and_survives_memory_clear(self):
        self.main._save_market_data_cache('CACHECLOCK', {'ticker':'CACHECLOCK'}, sync_sqlite=True)
        with self.main._mem_cache_lock:
            self.main._mem_cache.pop('CACHECLOCK', None)
        result=self.main._get_cached_market_data('CACHECLOCK')
        self.assertIsNotNone(result)
        with sqlite3.connect(self.main.DB_PATH) as db:
            timestamp=db.execute("SELECT fetched_at FROM market_data_cache WHERE ticker='CACHECLOCK'").fetchone()[0]
        self.assertTrue(timestamp.endswith('+00:00'))
