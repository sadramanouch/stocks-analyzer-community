import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_evidence import assess, init_store, make_router, normalize, save_records

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
CONGRESS = dict(Representative='Test Official', BioGuideID='TEST001', Ticker='TEST',
                TransactionDate='2026-10-01', ReportDate='2026-10-08',
                Transaction='Purchase', Amount='15001', Range='$15,001 - $50,000', TickerType='CS')
INSIDER = dict(Name='Test Officer', Ticker='TEST', Date='2026-10-07', fileDate='2026-10-08',
               TransactionCode='P', Shares=100, PricePerShare=20)
CRYPTO = dict(chain='ethereum', transaction_hash='0x'+'1'*64, trader_address='0x'+'2'*40,
              block_timestamp='2026-10-09T10:00:00Z', token_sold_symbol='USDC',
              token_bought_symbol='TEST', token_bought_address='0x'+'3'*40,
              token_sold_address='0x'+'4'*40, trade_value_usd=500)
FUNDAMENTALS = dict(quant_score=75, fcf_yield=0.06, red_flags_high=0, analyzed_at=NOW.isoformat(), validation_status='documented_inputs', currently_fresh=True)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        init_store(self.path)
        app = FastAPI()
        app.include_router(make_router(self.path))
        self.client = TestClient(app)

    def tearDown(self):
        self.temp.cleanup()

    def test_availability_is_observation_not_trade_or_disclosure(self):
        event = normalize('congress', CONGRESS, NOW)
        self.assertEqual(event['available_at'], NOW.isoformat())
        self.assertEqual(event['disclosure_lag_days'], 7)
        self.assertEqual(event['amount_label'], '$15,001 - $50,000')
        self.assertEqual(event['traded_at_display'], '2026-10-01 (date only)')

    def test_dedup_preserves_first_observation_and_watch_state(self):
        save_records(self.path, 'congress', [CONGRESS, CONGRESS], NOW)
        event_id = normalize('congress', CONGRESS, NOW)['id']
        self.client.put(f'/api/trade-evidence/{event_id}/watch', json={'watched': True})
        save_records(self.path, 'congress', [{**CONGRESS, 'ExcessReturn': 999}], NOW+timedelta(days=1))
        event = self.client.get('/api/trade-evidence').json()['events'][0]
        self.assertEqual(event['first_seen_at'], NOW.isoformat())
        self.assertTrue(event['watched'])
        evidence = self.client.get(f'/api/trade-evidence/{event_id}/evidence').json()
        self.assertEqual(len(evidence['observations']), 2)
        self.assertNotIn('ExcessReturn', event)

    def test_server_search_and_pagination_reach_records_outside_the_latest_page(self):
        rows=[{**CONGRESS,'Representative':f'Owner {n:03}','BioGuideID':f'ID{n:03}'} for n in range(105)]
        save_records(self.path,'congress',rows,NOW)
        a=self.client.get('/api/trade-evidence?limit=100').json()
        b=self.client.get('/api/trade-evidence?limit=100&offset=100').json()
        self.assertEqual(a['total_saved'],105);self.assertEqual(a['total'],105)
        self.assertEqual(len(a['events'])+len(b['events']),105)
        self.assertFalse(set(e['id'] for e in a['events']) & set(e['id'] for e in b['events']))
        missing=b['events'][0]['actor']
        found=self.client.get('/api/trade-evidence',params={'search':missing}).json()
        self.assertEqual(found['total'],1);self.assertEqual(found['events'][0]['actor'],missing)
        self.assertEqual(self.client.get('/api/trade-evidence?search=%25').json()['total'],0)
        self.assertEqual(self.client.get('/api/trade-evidence?search=_').json()['total'],0)
        self.client.put(f"/api/trade-evidence/{found['events'][0]['id']}/watch",json={'watched':True})
        watched=self.client.get('/api/trade-evidence?watched=true').json()
        self.assertEqual(watched['total'],1);self.assertEqual(watched['total_saved'],105)

    def test_dates_and_missing_identity_are_rejected(self):
        bad = [{**CONGRESS, 'ReportDate': '2026-09-01'}, {**CONGRESS, 'ReportDate': None},
               {**CONGRESS, 'TransactionDate': 'bad'}, {**CONGRESS, 'ReportDate': '2027-01-01'},
               {**CONGRESS, 'Representative': None}, {**CONGRESS, 'Ticker': 'N/A'}, None]
        self.assertEqual(save_records(self.path, 'congress', bad, NOW), (0, len(bad)))

    def test_grants_are_not_purchases(self):
        for code in ['A', 'F', 'M', 'G', 'unknown']:
            event = normalize('insiders', {**INSIDER, 'TransactionCode': code}, NOW)
            self.assertEqual(event['side'], 'other')
            self.assertEqual(assess(event, FUNDAMENTALS, NOW)['priority'], 'context_only')

    def test_sale_never_becomes_purchase_or_short_recommendation(self):
        event = normalize('congress', {**CONGRESS, 'Transaction': 'Sale (Partial)'}, NOW)
        self.assertEqual(assess(event, FUNDAMENTALS, NOW)['priority'], 'context_only')

    def test_crypto_keeps_both_swap_legs_and_never_uses_stock_fundamentals(self):
        event = normalize('crypto', CRYPTO, NOW)
        self.assertEqual(event['side'], 'swap')
        self.assertEqual(event['asset'], 'USDC → TEST')
        self.assertEqual(assess(event, None, NOW)['priority'], 'needs_evidence')
        save_records(self.path, 'crypto', [CRYPTO], NOW)
        self.assertIsNone(self.client.get('/api/trade-evidence').json()['events'][0]['fundamentals'])

    def test_crypto_explorer_url_requires_ethereum_hash(self):
        for change in [{'chain': 'solana'}, {'transaction_hash': 'javascript:alert(1)'}, {'trader_address': 'bad'}]:
            with self.assertRaises(ValueError):
                normalize('crypto', {**CRYPTO, **change}, NOW)

    def test_fresh_purchase_is_research_only_edge_still_unverified(self):
        event = normalize('congress', CONGRESS, NOW)
        self.assertEqual(assess(event, FUNDAMENTALS, NOW)['priority'], 'needs_evidence')
        result = assess({**event, 'source_verification': 'primary_parsed'}, FUNDAMENTALS, NOW)
        self.assertEqual(result['priority'], 'research')
        self.assertEqual(result['performance_status'], 'unverified')

    def test_missing_stale_future_and_incomplete_fundamentals_fail_closed(self):
        event = normalize('congress', CONGRESS, NOW)
        for fundamentals in [None, {**FUNDAMENTALS, 'red_flags_high': None},
                             {**FUNDAMENTALS, 'analyzed_at': (NOW-timedelta(days=8)).isoformat()},
                             {**FUNDAMENTALS, 'analyzed_at': (NOW+timedelta(days=1)).isoformat()},
                             {**FUNDAMENTALS, 'fcf_yield': None}, {**FUNDAMENTALS, 'fcf_yield': -0.05}]:
            self.assertEqual(assess(event, fundamentals, NOW)['priority'], 'needs_evidence')

    def test_high_financial_risk_overrides_good_score(self):
        event = {**normalize('congress', CONGRESS, NOW), 'source_verification': 'primary_parsed'}
        self.assertEqual(assess(event, {**FUNDAMENTALS, 'red_flags_high': 2}, NOW)['priority'], 'risk_flag')

    def test_delayed_signal_not_revived_by_new_import(self):
        event = normalize('congress', {**CONGRESS, 'TransactionDate': '2026-08-01'}, NOW)
        self.assertEqual(assess(event, FUNDAMENTALS, NOW)['priority'], 'delayed')
        self.assertEqual(assess(normalize('congress', CONGRESS, NOW), FUNDAMENTALS, NOW+timedelta(days=8))['priority'], 'delayed')

    def test_missing_amount_is_not_fabricated_zero(self):
        event = normalize('insiders', {**INSIDER, 'PricePerShare': None}, NOW)
        self.assertIsNone(event['amount_usd'])

    def test_options_and_unknown_instruments_need_review(self):
        for kind in ['OP', 'Option', '', None]:
            event = normalize('congress', {**CONGRESS, 'TickerType': kind}, NOW)
            self.assertEqual(assess(event, FUNDAMENTALS, NOW)['priority'], 'needs_evidence')

    def test_nonfinite_provider_values_are_rejected_without_breaking_batch(self):
        rows = [{**CONGRESS, 'Amount': float('nan')}, CONGRESS]
        self.assertEqual(save_records(self.path, 'congress', rows, NOW), (1, 1))

    def test_amount_overflow_is_rejected(self):
        self.assertEqual(save_records(self.path, 'insiders', [{**INSIDER, 'Shares': 1e308, 'PricePerShare': 1e308}], NOW), (0, 1))

    def test_provider_schema_failure_and_http_failures_are_sanitized(self):
        for mode in ['schema', '401', '429', '500']:
            with self.subTest(mode=mode):
                with sqlite3.connect(self.path) as db:
                    db.execute('DELETE FROM trade_syncs')
                response = Mock()
                response.json.return_value = {'unexpected': 'provider response'}
                if mode != 'schema':
                    response.raise_for_status.side_effect = requests.HTTPError('private-provider-body')
                with patch.dict(os.environ, {'QUIVER_API_KEY':'test-secret'}), patch('trade_evidence.requests.get', return_value=response):
                    result = self.client.post('/api/trade-evidence/sync/congress')
                self.assertEqual(result.status_code, 502)
                self.assertNotIn('private-provider-body', result.text)

    def test_partial_import_reports_bad_rows(self):
        response = Mock(); response.json.return_value = [CONGRESS, {'invalid':'row'}]
        with patch.dict(os.environ, {'QUIVER_API_KEY':'test-secret'}), patch('trade_evidence.requests.get', return_value=response), patch('trade_evidence.now_utc', return_value=NOW):
            result = self.client.post('/api/trade-evidence/sync/congress')
        self.assertEqual(result.json()['inserted'], 1)
        self.assertEqual(result.json()['rejected'], 1)
        self.assertEqual(self.client.get('/api/trade-evidence').json()['providers'][0]['sync']['state'], 'partial')

    def test_demo_flag_requires_explicit_configuration(self):
        with patch.dict(os.environ, {'TRADE_EVIDENCE_DEMO':'1'}):
            self.assertTrue(self.client.get('/api/trade-evidence').json()['demo'])
        with patch.dict(os.environ, {'TRADE_EVIDENCE_DEMO':'0'}):
            self.assertFalse(self.client.get('/api/trade-evidence').json()['demo'])

    def test_existing_company_analysis_is_joined_for_equities_only(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE company_analyses(ticker TEXT, quant_score INTEGER, fcf_yield REAL, red_flags_high INTEGER, analyzed_at TEXT)')
            db.execute('INSERT INTO company_analyses VALUES(?,?,?,?,?)', ('TEST', 75, .06, 0, NOW.isoformat()))
        save_records(self.path, 'congress', [CONGRESS], NOW)
        save_records(self.path, 'crypto', [CRYPTO], NOW)
        with patch('trade_evidence.now_utc', return_value=NOW):
            events = self.client.get('/api/trade-evidence').json()['events']
        stocks = [e for e in events if e['kind'] == 'congress']
        self.assertEqual(stocks[0]['priority'], 'needs_evidence')
        self.assertEqual(stocks[0]['fundamentals']['fcf_yield'], .06)
        self.assertIsNone(next(e for e in events if e['kind'] == 'crypto')['fundamentals'])

    def test_unconfigured_provider_no_request_no_fake_events(self):
        with patch.dict(os.environ, {}, clear=True), patch('trade_evidence.requests.get') as fetch:
            response = self.client.post('/api/trade-evidence/sync/congress')
            self.assertEqual(response.status_code, 409)
            fetch.assert_not_called()
            data = self.client.get('/api/trade-evidence').json()
            self.assertEqual(data['events'], [])
            self.assertFalse(any(p['configured'] for p in data['providers']))

    def test_sync_auth_schema_and_cooldown(self):
        response = Mock(); response.json.return_value = [CONGRESS]
        with patch.dict(os.environ, {'QUIVER_API_KEY': 'test-secret'}), patch('trade_evidence.requests.get', return_value=response) as fetch, patch('trade_evidence.now_utc', return_value=NOW):
            result = self.client.post('/api/trade-evidence/sync/congress')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['inserted'], 1)
            self.assertEqual(fetch.call_args.kwargs['headers']['Authorization'], 'Bearer test-secret')
            self.assertEqual(self.client.post('/api/trade-evidence/sync/congress').status_code, 429)
            self.assertNotIn('test-secret', self.client.get('/api/trade-evidence').text)

    def test_failed_sync_preserves_records_and_does_not_leak_error(self):
        save_records(self.path, 'congress', [CONGRESS], NOW)
        with patch.dict(os.environ, {'QUIVER_API_KEY': 'test-secret'}), patch('trade_evidence.requests.get', side_effect=requests.Timeout('test-secret')):
            response = self.client.post('/api/trade-evidence/sync/congress')
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('test-secret', response.text)
            data = self.client.get('/api/trade-evidence').json()
            self.assertEqual(data['total'], 1)
            self.assertEqual(data['providers'][0]['sync']['state'], 'error')

    def test_crypto_request_and_wrapped_response(self):
        response = Mock(); response.json.return_value = {'data': [CRYPTO], 'pagination': {'is_last_page': False}}
        with patch.dict(os.environ, {'NANSEN_API_KEY': 'test-secret'}), patch('trade_evidence.requests.post', return_value=response) as fetch, patch('trade_evidence.now_utc', return_value=NOW):
            self.assertEqual(self.client.post('/api/trade-evidence/sync/crypto').json()['inserted'], 1)
            self.assertEqual(fetch.call_args.kwargs['json']['chains'], ['ethereum'])
            self.assertEqual(fetch.call_args.kwargs['headers']['apikey'], 'test-secret')

    def test_endpoint_validation_and_unknown_ids(self):
        self.assertEqual(self.client.get('/api/trade-evidence?limit=10000').status_code, 422)
        self.assertEqual(self.client.get('/api/trade-evidence?kind=invalid').status_code, 422)
        self.assertEqual(self.client.post('/api/trade-evidence/sync/invalid').status_code, 404)
        self.assertEqual(self.client.put('/api/trade-evidence/missing/watch', json={'watched': True}).status_code, 404)
        self.assertEqual(self.client.get('/api/trade-evidence/missing/evidence').status_code, 404)

    def test_company_filter_applies_before_limit_and_excludes_token_symbols(self):
        save_records(self.path, 'congress', [CONGRESS], NOW)
        save_records(self.path, 'crypto', [CRYPTO], NOW + timedelta(seconds=1))
        save_records(self.path, 'congress', [{**CONGRESS, 'Ticker': 'OTHER'}], NOW + timedelta(seconds=2))
        result = self.client.get('/api/trade-evidence?ticker=test&limit=1').json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['asset'], 'TEST')
        self.assertEqual(result['events'][0]['kind'], 'congress')
        self.assertEqual(self.client.get('/api/trade-evidence?ticker=TEST&kind=crypto').json()['total'], 0)
        self.assertEqual(self.client.get('/api/trade-evidence?ticker=UNKNOWN').json()['events'], [])

    def test_company_filter_rejects_invalid_tickers(self):
        self.assertEqual(self.client.get('/api/trade-evidence', params={'ticker': "TEST' OR 1=1"}).status_code, 422)
        self.assertEqual(self.client.get('/api/trade-evidence', params={'ticker': 'A' * 21}).status_code, 422)
        self.assertEqual(self.client.get('/api/trade-evidence?ticker=BRK.B').status_code, 200)


if __name__ == '__main__':
    unittest.main()
