import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from price_readiness import capture_window, evaluate_reference, make_price_router
from quote_checks import collect, digest, init_quote_checks
from research_store import encoded
from test_quote_checks import AT, Reply, identity, packets


class PriceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        init_quote_checks(self.path)
        self.item = {'ticker': 'TEST', 'price': 100, 'quote_as_of': '2026-10-15T17:39:30+00:00',
                     'quote_currency': 'USD', 'quote_source': 'test regular reference', 'input_sha256': digest('original input')}
        original_identity = {k:v for k,v in identity().items() if k not in ('revision_id', 'retrieved_at')}
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE instrument_revisions(id INTEGER PRIMARY KEY,ticker TEXT,retrieved_at TEXT,evidence_json TEXT);
                CREATE TABLE saved_list_versions(id INTEGER PRIMARY KEY);
                INSERT INTO saved_list_versions VALUES(1);
                CREATE TABLE saved_list_items(version_id INTEGER,ticker TEXT,payload_json TEXT);''')
            db.execute('INSERT INTO instrument_revisions VALUES(?,?,?,?)', (1, 'TEST', identity()['retrieved_at'], encoded(original_identity)))
            db.execute('INSERT INTO saved_list_items VALUES(?,?,?)', (1, 'TEST', encoded(self.item)))
        self.app = FastAPI()
        self.app.include_router(make_price_router(self.path))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, values=None):
        replies = [Reply(json.dumps(v).encode()) for v in (values or packets()).values()]
        with patch('quote_checks.now', return_value=AT):
            return collect(self.path, 'TEST', SimpleNamespace(get=lambda *a, **kw: replies.pop(0)))

    def evaluate(self, report, item=None, at=AT, **kwargs):
        return evaluate_reference(item or self.item, report, at, current_edition=kwargs.get('current_edition', True),
                                  current_identity=kwargs.get('current_identity', identity()))

    def read(self, **params):
        with patch('price_readiness.now', return_value=AT):
            return self.client.get('/api/price-readiness/TEST', params={'version':1, 'input_sha256':self.item['input_sha256'], **params})

    def test_agreement_is_bound_to_displayed_reference_without_independence_or_execution_claims(self):
        r = self.evaluate(self.capture())
        self.assertEqual(r['state'], 'displayed_reference_agrees')
        self.assertEqual(r['valid_until'], '2026-10-15T17:59:30+00:00')
        self.assertTrue(r['displayed_reference_corroborated_now'])
        self.assertTrue(r['reference']['paper_quote_requirements_met'])
        self.assertFalse(r['upstream_independence_verified'])
        self.assertFalse(r['executable_price_verified'])
        self.assertFalse(r['profitability_verified'])

    def test_new_source_agreement_does_not_certify_a_different_dashboard_price(self):
        r = self.evaluate(self.capture(), {**self.item, 'price':100.11})
        self.assertEqual(r['state'], 'displayed_reference_not_corroborated')
        self.assertFalse(r['displayed_reference_corroborated_now'])

    def test_displayed_quote_time_must_match_sources_within_declared_window(self):
        report = self.capture()
        for value, expected in [('2026-10-15T17:38:30Z', True), ('2026-10-15T17:38:29Z', False)]:
            self.assertEqual(self.evaluate(report, {**self.item,'quote_as_of':value})['displayed_reference_corroborated_now'], expected)

    def test_old_agreement_expires_without_mutating_the_frozen_capture(self):
        report = self.capture()
        before = copy.deepcopy(report)
        r = self.evaluate(report, at='2026-10-15T18:00:00Z')
        self.assertEqual(r['state'], 'comparison_expired')
        self.assertFalse(r['displayed_reference_corroborated_now'])
        self.assertEqual(report, before)

    def test_displayed_quote_can_expire_before_otherwise_fresh_sources(self):
        r = self.evaluate(self.capture(), {**self.item,'quote_as_of':'2026-10-15T17:38:31Z'}, at='2026-10-15T17:59:21Z')
        self.assertEqual(r['state'], 'displayed_reference_expired')
        self.assertIsNone(r['valid_until'])

    def test_price_tolerance_uses_exact_decimal_boundary(self):
        report = self.capture()
        self.assertTrue(self.evaluate(report, {**self.item,'price':'100.10'})['displayed_reference_corroborated_now'])
        self.assertFalse(self.evaluate(report, {**self.item,'price':'100.100001'})['displayed_reference_corroborated_now'])

    def test_paper_reference_age_remains_separate_from_corroboration(self):
        report = self.capture()
        r = self.evaluate(report, at='2026-10-16T17:39:30Z')
        self.assertTrue(r['reference']['paper_quote_requirements_met'])
        self.assertFalse(r['displayed_reference_corroborated_now'])
        r = self.evaluate(report, at='2026-10-16T17:39:31Z')
        self.assertFalse(r['reference']['paper_quote_requirements_met'])

    def test_invalid_displayed_amount_currency_source_and_clock_never_promote(self):
        report = self.capture()
        changes = [{'price':v} for v in (True, 'NaN', 'Infinity', 0, -1, None)]
        changes += [{'quote_currency':'CAD'}, {'quote_source':None}, {'quote_as_of':'2026-10-15T17:41:00Z'}, {'quote_as_of':'2026-10-15T17:39:30'}]
        for change in changes:
            with self.subTest(change=change):
                r = self.evaluate(report, {**self.item, **change})
                self.assertEqual(r['state'], 'invalid_displayed_reference')
                self.assertFalse(r['displayed_reference_corroborated_now'])

    def test_current_identity_and_current_edition_are_required(self):
        report = self.capture()
        self.assertEqual(self.evaluate(report, current_edition=False)['state'], 'earlier_research_edition')
        self.assertEqual(self.evaluate(report, current_identity={**identity(),'security_type':'ETF'})['state'], 'identity_needs_review')
        self.assertEqual(self.evaluate(report, current_identity={**identity(),'exchange':'Q'})['state'], 'displayed_reference_not_corroborated')

    def test_future_retained_comparison_does_not_promote(self):
        self.assertEqual(self.evaluate(self.capture(), at='2026-10-15T17:39:59Z')['state'], 'future_comparison')

    def test_disagreeing_sources_and_missing_comparison_stay_explicit(self):
        self.assertEqual(self.evaluate(None)['state'], 'missing_comparison')
        values = packets(); values['cnbc']['QuickQuoteResult']['QuickQuote'][0]['last'] = '105'
        self.assertEqual(self.evaluate(self.capture(values))['state'], 'source_disagreement')

    def test_read_only_route_binds_inputs_and_makes_no_source_requests(self):
        report = self.capture()
        with patch('quote_checks.requests.Session') as network:
            response = self.read()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['comparison']['id'], report['id'])
            self.assertEqual(response.json()['saved_item_sha256'], digest(encoded(self.item)))
            self.assertEqual(self.read(input_sha256='0'*64).status_code, 409)
            self.assertEqual(self.read(version=999).status_code, 404)
            self.assertEqual(self.read(input_sha256='bad').status_code, 422)
            self.assertEqual(self.read(version=-1).status_code, 422)
            self.assertEqual(self.client.get('/api/price-readiness/BAD!', params={'version':1,'input_sha256':self.item['input_sha256']}).status_code, 422)
            self.assertEqual(self.client.post('/api/price-readiness/TEST', json={}).status_code, 405)
            network.assert_not_called()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quote_check_reports').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM market_source_documents').fetchone()[0], 2)

    def test_no_fallback_to_an_older_agreement_when_latest_evidence_is_corrupt(self):
        old = self.capture()
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO quote_check_reports VALUES(?,?,?,?,?,?,?)', (digest('broken'), 'TEST', AT, '{}', digest('{}'), '{}', digest('{}')))
        r = self.read().json()
        self.assertEqual(r['state'], 'evidence_integrity_failed')
        self.assertIsNone(r['comparison'])
        self.assertFalse(r['displayed_reference_corroborated_now'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT report_json FROM quote_check_reports WHERE id=?', (old['id'],)).fetchone()[0], old['report_json'])

    def test_corrupted_originals_never_appear_as_current_corroboration(self):
        self.capture()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE')
            db.execute("UPDATE market_source_documents SET body_bytes=X'00'")
        self.assertEqual(self.read().json()['state'], 'evidence_integrity_failed')

    def test_earlier_research_edition_and_malformed_saved_item_fail_closed(self):
        self.capture()
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO saved_list_versions VALUES(2)')
        self.assertEqual(self.read().json()['state'], 'earlier_research_edition')
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE saved_list_items SET payload_json='invalid'")
        self.assertEqual(self.read().status_code, 409)

    def test_capture_window_respects_weekends_early_closes_and_calendar_limits(self):
        self.assertTrue(capture_window(AT)['available_now'])
        self.assertEqual(capture_window('2026-10-16T06:00:00Z')['next_open'], '2026-10-16T09:30:00-04:00')
        self.assertEqual(capture_window('2026-10-16T20:21:00Z')['next_open'], '2026-10-19T09:30:00-04:00')
        self.assertTrue(capture_window('2026-11-27T18:20:00Z')['available_now'])
        self.assertEqual(capture_window('2026-11-27T18:20:01Z')['next_open'], '2026-11-30T09:30:00-05:00')
        self.assertIsNone(capture_window('2027-01-04T15:00:00Z')['next_open'])
