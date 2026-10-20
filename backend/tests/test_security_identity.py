import copy
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from research_store import DIRECTORIES, encoded, identities, identity_eligibility, init_research, parse_directory
from security_identity import (METHOD, MAPPING_URL, apply_verified, class_evidence_fresh, collect,
    cover_class, evaluate, filing_reference, make_identity_router, replay, save_source, sha)
from quote_checks import identity_check

AT = '2026-10-15T21:00:00+00:00'
CIK = '0001234567'
FILING = {'form': '10-Q', 'accessionNumber': '0001234567-26-000001', 'filingDate': '2026-08-01',
          'primaryDocument': 'example.htm', 'reportDate': '2026-06-30',
          'url': 'https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/example.htm'}


def fact(name, text, context='c1'):
    return f'<ix:nonNumeric name="dei:{name}" contextRef="{context}">{text}</ix:nonNumeric>'


def security_row(ticker='TEST', title='Common Stock', exchange='New York Stock Exchange', context='c1'):
    return '<tr><td>'+fact('Security12bTitle', title, context)+'</td><td>'+fact('TradingSymbol', ticker, context)+'</td><td>'+fact('SecurityExchangeName', exchange, context)+'</td></tr>'


def cover(rows=None, extra='', cik=CIK):
    return (f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:xbrli="http://www.xbrl.org/2003/instance">'
            f'<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>'
            +fact('EntityCentralIndexKey', cik)+fact('DocumentType', '10-Q')+'<table>'+(rows or security_row())+'</table>'+extra+'</html>').encode()


def index():
    recent = {k: [FILING[k]] for k in ('form', 'accessionNumber', 'filingDate', 'primaryDocument', 'reportDate')}
    return {'cik': int(CIK), 'tickers': ['TEST'], 'filings': {'recent': recent}}


class SecurityIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        init_research(self.path)
        docs = {'nasdaqlisted': 'Symbol|Security Name|Test Issue|ETF\nOTHER|Other Common Stock|N|N\nFile Creation Time: 10152026\n',
                'otherlisted': 'ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|NASDAQ Symbol|Test Issue\nTEST|Example Corp|N|TEST|N|TEST|N\nFile Creation Time: 10152026\n'}
        bundle = {'algorithm_version': 'nasdaq-directory-v1', 'documents': {k: {'url': DIRECTORIES[k], 'text': text, 'sha256': sha(text)} for k, text in docs.items()}}
        self.listing = parse_directory(docs['otherlisted'], 'otherlisted')['TEST']
        with self.db() as db:
            batch = sha(encoded(bundle))
            db.execute('INSERT INTO identity_batches VALUES(?,?,?)', (batch, AT, encoded(bundle)))
            db.execute('INSERT INTO instrument_revisions VALUES(1,?,?,?,?)', ('TEST', batch, AT, encoded(self.listing)))
        self.listing.update(revision_id=1, retrieved_at=AT)
        self.sources = {
            'mapping': {'source_url': MAPPING_URL, 'body': encoded({'0': {'ticker': 'TEST', 'cik_str': int(CIK)}}).encode()},
            'submissions': {'source_url': f'https://data.sec.gov/submissions/CIK{CIK}.json', 'body': encoded(index()).encode()},
            'filing': {'source_url': FILING['url'], 'body': cover()}}
        self.inputs = {'methodology': METHOD, 'ticker': 'TEST', 'captured_at': AT, 'directory_identity': self.listing, 'source_ids': {}}
        for kind, source in self.sources.items():
            source.update(status_code=200, observed_at=AT, sha256=sha(source['body']), body_gzip=gzip.compress(source['body']))
            identifier = save_source(self.path, source['source_url'], source['body'], 200, AT)
            source['id'] = identifier
            self.inputs['source_ids'][kind] = identifier
        app = FastAPI(); app.include_router(make_identity_router(self.path, lambda: {'User-Agent': 'fixture contact'}))
        self.client = TestClient(app)

    def tearDown(self): self.temp.cleanup()

    def db(self):
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row
        return db

    def save_report(self, inputs=None):
        inputs = inputs or self.inputs
        with self.db() as db:
            sources = {k: db.execute('SELECT * FROM identity_source_documents WHERE id=?', (v,)).fetchone() for k, v in inputs['source_ids'].items()}
            raw, report = encoded(inputs), encoded(evaluate(inputs, sources))
            db.execute('INSERT INTO security_class_reports VALUES(?,?,?,?,?,?)', (sha(raw), 'TEST', inputs['captured_at'], raw, report, sha(report)))
        return sha(raw)

    def test_exact_common_row_and_original_source_replay(self):
        identifier = self.save_report()
        with self.db() as db:
            result = replay(db, db.execute('SELECT * FROM security_class_reports').fetchone())
        self.assertEqual(result['id'], identifier)
        self.assertEqual(result['report']['status'], 'matched_common_share_class')
        self.assertEqual(result['report']['cik'], CIK)
        self.assertEqual(result['report']['share_class']['title'], 'Common Stock')

    def test_same_context_bond_does_not_inherit_common_share_title(self):
        body = cover(security_row('OTHER')+security_row('TEST', '5.5% Notes due 2030'))
        with self.assertRaisesRegex(ValueError, 'explicitly establish'):
            cover_class(body, 'TEST', CIK, FILING, self.listing)

    def test_multiclass_same_context_pairs_exact_row(self):
        result = cover_class(cover(security_row('TEST')+security_row('TESTP', 'Preferred Stock')), 'TEST', CIK, FILING, self.listing)
        self.assertEqual(result['security_type'], 'CS')

    def test_ambiguous_same_symbol_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            cover_class(cover(security_row()+security_row(title='Class B Common Stock')), 'TEST', CIK, FILING, self.listing)

    def test_different_context_exchange_case_and_missing_row_fail(self):
        cases = [cover(security_row('TESTX')), cover(security_row('test')), cover(security_row(exchange='NASDAQ')),
                 cover().replace(b'name="dei:Security12bTitle" contextRef="c1"', b'name="dei:Security12bTitle" contextRef="c2"'),
                 cover().replace(b'<tr>', b'<div>').replace(b'</tr>', b'</div>')]
        for body in cases:
            with self.subTest(body=body[-120:]), self.assertRaises(ValueError):
                cover_class(body, 'TEST', CIK, FILING, self.listing)

    def test_cik_namespace_context_period_and_entity_injection_fail(self):
        cases = [cover(cik='0007654321'), cover().replace(b'http://xbrl.sec.gov/dei/2026', b'http://example.com/dei/2026'),
                 cover().replace(b'2026-06-30', b'2025-06-30'), cover().replace(b'http://www.sec.gov/CIK', b'http://example.com/CIK'),
                 b'<!DOCTYPE html [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+cover()]
        for body in cases:
            with self.subTest(body=body[:80]), self.assertRaises(ValueError):
                cover_class(body, 'TEST', CIK, FILING, self.listing)

    def test_hidden_row_or_continuation_cannot_establish_class(self):
        for body in (cover().replace(b'<table>', b'<table style="display: none">'), cover().replace(b'<table>', b'<table hidden="hidden">'), cover().replace(b'name="dei:Security12bTitle"', b'name="dei:Security12bTitle" continuedAt="c99"')):
            with self.assertRaises(ValueError): cover_class(body, 'TEST', CIK, FILING, self.listing)

    def test_standard_hidden_issuer_metadata_is_accepted(self):
        body = cover().replace(fact('EntityCentralIndexKey', CIK).encode(), ('<ix:hidden>'+fact('EntityCentralIndexKey', CIK)+'</ix:hidden>').encode())
        self.assertEqual(cover_class(body, 'TEST', CIK, FILING, self.listing)['security_type'], 'CS')

    def test_duplicate_context_and_extra_symbol_in_same_row_fail(self):
        body = cover().replace(b'<table>', b'<xbrli:context id="c1"/><table>')
        with self.assertRaisesRegex(ValueError, 'duplicated'): cover_class(body, 'TEST', CIK, FILING, self.listing)
        body = cover(security_row().replace('</tr>', '<td>'+fact('TradingSymbol', 'OTHER')+'</td></tr>'))
        with self.assertRaisesRegex(ValueError, 'unique'): cover_class(body, 'TEST', CIK, FILING, self.listing)

    def test_registered_shares_ads_warrants_and_preferred_stay_unverified(self):
        for title in ('Registered Shares', 'Class A Shares', 'American Depositary Shares representing common shares', 'Common Stock Warrants', 'Preferred Stock'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                cover_class(cover(security_row(title=title)), 'TEST', CIK, FILING, self.listing)

    def test_latest_amendment_and_malformed_or_stale_index_fail(self):
        for kind in ('amended', 'short', 'future', 'stale', 'wrong_cik', 'wrong_symbol', 'path'):
            data = index(); recent = data['filings']['recent']
            if kind == 'amended': recent['form'] = ['10-Q/A']
            if kind == 'short': recent['primaryDocument'] = []
            if kind == 'future': recent['filingDate'] = ['2026-10-16']
            if kind == 'stale': recent['filingDate'], recent['reportDate'] = ['2025-08-01'], ['2025-06-30']
            if kind == 'wrong_cik': data['cik'] = 123
            if kind == 'wrong_symbol': data['tickers'] = ['TESTP']
            if kind == 'path': recent['primaryDocument'] = ['../other.htm']
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                filing_reference(encoded(data), CIK, 'TEST', AT)

    def test_source_hash_url_time_and_listing_freshness_fail_closed(self):
        for kind in ('hash', 'url', 'future', 'old_mapping', 'old_listing', 'unknown_mapping'):
            sources, inputs = copy.deepcopy(self.sources), copy.deepcopy(self.inputs)
            if kind == 'hash': sources['filing']['sha256'] = '0'*64
            if kind == 'url': sources['filing']['source_url'] += '?other'
            if kind == 'future': sources['filing']['observed_at'] = '2026-10-16T00:00:00+00:00'
            if kind == 'old_mapping': sources['mapping']['observed_at'] = '2026-10-01T00:00:00+00:00'
            if kind == 'old_listing': inputs['directory_identity']['retrieved_at'] = '2026-10-01T00:00:00+00:00'
            if kind == 'unknown_mapping':
                body = b'{}'; sources['mapping'].update(sha256=sha(body), body_gzip=gzip.compress(body))
            self.assertEqual(evaluate(inputs, sources)['status'], 'requires_review', kind)

    def test_append_only_idempotent_application_and_expiry(self):
        self.save_report()
        with self.db() as db:
            self.assertEqual(apply_verified(db, AT), ['TEST'])
            self.assertEqual(apply_verified(db, AT), [])
            current = identities(db)['TEST']
            self.assertEqual(current['security_type'], 'CS')
            self.assertEqual(current['retrieved_at'], AT)
            self.assertEqual(json.loads(db.execute('SELECT evidence_json FROM instrument_revisions WHERE id=1').fetchone()[0])['security_type'], 'unknown')
        self.assertTrue(identity_eligibility(current, {}, AT)['model_applicable'])
        self.assertTrue(identity_check(current, 'TEST', AT))
        expired = current['class_evidence']['valid_until']
        self.assertFalse(class_evidence_fresh(current, expired))
        self.assertFalse(identity_check(current, 'TEST', expired))
        self.assertFalse(identity_eligibility(current, {}, expired)['model_applicable'])

    def test_changed_listing_does_not_reuse_prior_class(self):
        self.save_report()
        with self.db() as db:
            changed = {k:v for k,v in self.listing.items() if k not in ('revision_id', 'retrieved_at')}
            changed['title'] = 'Other Company'; changed['raw_record']['Security Name'] = 'Other Company'
            batch = db.execute('SELECT batch_id FROM instrument_revisions').fetchone()[0]
            db.execute('INSERT INTO instrument_revisions(ticker,batch_id,retrieved_at,evidence_json) VALUES(?,?,?,?)', ('TEST', batch, AT, encoded(changed)))
            self.assertEqual(apply_verified(db, AT), [])

    def test_original_report_source_and_identity_cannot_be_updated(self):
        self.save_report()
        with self.db() as db:
            for table in ('identity_source_documents', 'security_class_reports', 'instrument_revisions'):
                with self.assertRaises(sqlite3.IntegrityError): db.execute(f'DELETE FROM {table}')

    def test_hash_consistent_forged_directory_revision_still_fails_replay(self):
        forged = copy.deepcopy(self.inputs)
        forged['directory_identity']['raw_record']['Security Name'] = 'Forged Corp'
        with self.db() as db:
            batch = db.execute('SELECT batch_id FROM instrument_revisions').fetchone()[0]
            identity = forged['directory_identity']
            identity['revision_id'] = 2
            record = {k:v for k,v in identity.items() if k not in ('revision_id','retrieved_at')}
            db.execute('INSERT INTO instrument_revisions VALUES(2,?,?,?,?)', ('TEST',batch,AT,encoded(record)))
        identifier = self.save_report(forged)
        with self.db() as db, self.assertRaisesRegex(ValueError, 'original directory'):
            replay(db, db.execute('SELECT * FROM security_class_reports WHERE id=?',(identifier,)).fetchone())

    def test_network_denial_is_archived_and_stops_remaining_requests(self):
        class Denied:
            status_code = 403
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,*args): yield b'access denied'
        with patch('security_identity.now', return_value=AT), patch('security_identity.requests.get', return_value=Denied()) as get:
            with self.assertRaisesRegex(ValueError, 'HTTP 403'): collect(self.path,'TEST',{'User-Agent':'fixture'})
            self.assertEqual(get.call_count,1)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM identity_source_documents WHERE status_code=403').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM security_class_reports').fetchone()[0],0)

    def test_http_replay_raw_source_hash_and_forbidden_numbers(self):
        identifier = self.save_report()
        self.assertEqual(self.client.get('/api/security-identity/reports/'+identifier).json()['report']['status'], 'matched_common_share_class')
        source = self.client.get('/api/security-identity/sources/'+self.inputs['source_ids']['filing'])
        self.assertEqual(sha(source.content), source.headers['X-Content-SHA256'])
        self.assertTrue(source.headers['Content-Type'].startswith('application/octet-stream'))
        self.assertEqual(self.client.post('/api/security-identity/TEST/collect', json={'security_type': 'CS'}).status_code, 422)
        self.assertEqual(self.client.get('/api/security-identity/reports/missing').status_code, 404)

    def test_storage_denial_and_stale_listing_do_not_start_network(self):
        for kind in ('storage', 'denial', 'stale'):
            with patch('security_identity.now', return_value=AT), patch('security_identity.requests.get') as get:
                if kind == 'storage':
                    with patch('security_identity.shutil.disk_usage') as usage:
                        usage.return_value.free = 1
                        with self.assertRaisesRegex(ValueError, 'reserve'): collect(self.path, 'TEST', {'User-Agent': 'fixture'})
                elif kind == 'denial':
                    save_source(self.path, 'https://www.sec.gov/blocked', b'denied', 403, AT)
                    with self.assertRaisesRegex(ValueError, 'paused'): collect(self.path, 'TEST', {'User-Agent': 'fixture'})
                else:
                    # A past date is earlier than this fixture's original listing.
                    with patch('security_identity.now', return_value='2026-10-01T00:00:00+00:00'):
                        with self.assertRaisesRegex(ValueError, 'fresh'): collect(self.path, 'TEST', {'User-Agent': 'fixture'})
                get.assert_not_called()

    def test_reused_report_retains_original_capture_time_without_network(self):
        self.save_report()
        with patch('security_identity.now', return_value=AT), patch('security_identity.requests.get') as get:
            self.assertEqual(collect(self.path, 'TEST', {'User-Agent': 'fixture'})['report']['captured_at'], AT)
            get.assert_not_called()


if __name__ == '__main__': unittest.main()
