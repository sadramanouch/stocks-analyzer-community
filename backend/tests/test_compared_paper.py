"""Source custody, accounting and fail-closed posting for compared paper references."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from equity_workspace import COMPARED_POLICY, commit_preview, init_workspace, make_preview, make_workspace_router
from ownership_history import init_ownership, save_form4
from paper_ledger import append_event, read_ledger
from quote_checks import collect, init_quote_checks
from research_store import encoded, init_research, rebuild_item
from test_quote_checks import AT, Reply, identity as quote_identity, packets
from test_source_history import inputs, XML, ACCESSION, URL


def identity():
    return {**quote_identity(), 'source': 'Nasdaq Trader official directory fixture'}


class ComparedPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'paper.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE company_analyses(ticker TEXT PRIMARY KEY,company_name TEXT,analyzed_at TEXT,fcf_yield REAL,quant_score INTEGER); CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT,cik TEXT); CREATE TABLE filings(company_id INTEGER,form_type TEXT,url TEXT,accession_number TEXT,filed_date TEXT);')
        init_research(self.path); init_ownership(self.path); init_workspace(self.path); init_quote_checks(self.path)
        saved, data, _ = inputs()
        data['quote_source'] = 'Explicit older financial cache fixture'
        self.item = rebuild_item(saved, (data, AT), identity(), AT)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO instrument_revisions(ticker,batch_id,retrieved_at,evidence_json) VALUES(?,?,?,?)', ('TEST', 'fixture', identity()['retrieved_at'], encoded({k:v for k,v in identity().items() if k not in ('revision_id','retrieved_at')})))
            version = db.execute('INSERT INTO saved_list_versions(created_at,model_version,basis,item_count,content_hash) VALUES(?,?,?,?,?)', (AT,'fixture','Compared paper fixture',1,'fixture')).lastrowid
            db.execute('INSERT INTO saved_list_items VALUES(?,?,?,?)', (version,'TEST',self.item['status'],encoded(self.item)))
        self.app = FastAPI(); self.app.include_router(make_workspace_router(self.path, None)); self.client = TestClient(self.app)
        self.clock = patch('equity_workspace.now', return_value=AT); self.clock.start()
        append_event(self.path, {'event_id':'virtual-funding','kind':'deposit','amount':'10000','occurred_at':AT,'currency':'USD','source':'Explicit simulated test funding'})

    def tearDown(self):
        self.clock.stop(); self.temp.cleanup()

    def capture(self, values=None, at=AT):
        replies = [Reply(json.dumps(v).encode()) for v in (values or packets()).values()]
        with patch('quote_checks.now', return_value=at):
            return collect(self.path, 'TEST', SimpleNamespace(get=lambda *a, **kw: replies.pop(0)))

    def preview(self, report=None, **kwargs):
        return make_preview(self.path, 'TEST', kwargs.get('side','buy'), kwargs.get('shares','10'), kwargs.get('strategy','fundamentals'), kwargs.get('at',AT), quote_report_id=(report or self.capture())['id'])

    def test_recent_originals_replace_only_the_paper_reference_and_post_atomically(self):
        with self.assertRaisesRegex(ValueError, '24 hours'):
            make_preview(self.path,'TEST','buy','10','fundamentals',AT)
        report = self.capture()
        with patch('quote_checks.requests.Session') as network:
            preview = self.preview(report)
            self.assertEqual(preview['reference_price'], '100')
            self.assertEqual(preview['event']['price'], '100.100000')
            self.assertEqual(preview['event']['fee'], '1.00')
            self.assertEqual(preview['result']['cash'], '8998.000000')
            self.assertEqual(preview['result']['pnl'], '-2.000000')
            with sqlite3.connect(self.path) as db:
                frozen = json.loads(db.execute('SELECT input_json FROM equity_decisions WHERE id=?', (preview['decision_id'],)).fetchone()[0])
            self.assertEqual(frozen['research'], self.item)
            self.assertEqual(frozen['reference_quote_context']['report_id'], report['id'])
            self.assertFalse(frozen['reference_quote_context']['executable_price_verified'])
            self.assertTrue(commit_preview(self.path,preview['id'],AT)['created'])
            self.assertFalse(commit_preview(self.path,preview['id'],'2026-10-17T18:00:00Z')['created'])
            network.assert_not_called()
        self.assertEqual(read_ledger(self.path)['event_count'],3)

    def test_request_cannot_supply_a_price_and_rejections_do_not_write_decisions(self):
        report = self.capture()
        body = {'ticker':'TEST','side':'buy','shares':'10','strategy':'fundamentals','quote_report_id':report['id']}
        for extra in ({'price':'1'},{'quote_at':AT},{'quote_report_id':'bad'},{'quote_report_id':'0'*64},{'ticker':'OTHER'}):
            response = self.client.post('/api/equity-workspace/paper/compared-preview', json={**body,**extra})
            self.assertEqual(response.status_code,422, response.text)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM equity_decisions').fetchone()[0],0)
        self.assertEqual(self.client.post('/api/equity-workspace/paper/compared-preview',json=body).status_code,200)

    def test_expiry_is_bounded_by_both_source_timestamps(self):
        report = self.capture()
        late = self.preview(report,at='2026-10-15T17:59:00Z')
        self.assertEqual(late['expires_at'],'2026-10-15T17:59:30+00:00')
        with self.assertRaisesRegex(ValueError,'expired'):
            commit_preview(self.path,late['id'],'2026-10-15T17:59:31Z')
        with self.assertRaisesRegex(ValueError,'20-minute'):
            self.preview(report,at='2026-10-15T18:00:00Z')
        with self.assertRaisesRegex(ValueError,'later than'):
            self.preview(report,side='sell',at='2026-10-15T17:39:59Z')
        self.assertEqual(read_ledger(self.path)['event_count'],1)

    def test_disagreement_and_wrong_session_never_create_a_preview(self):
        for value, at in (('105', AT), ('-1', '2026-10-15T17:46:00Z')):
            values = packets(); values['cnbc']['QuickQuoteResult']['QuickQuote'][0]['last'] = value
            report = self.capture(values, at=at)
            with self.assertRaises(ValueError): self.preview(report, at=at)
        self.assertEqual(read_ledger(self.path)['event_count'],1)

    def test_changed_latest_comparison_invalidates_a_pending_preview(self):
        report = self.capture(); preview = self.preview(report)
        values = packets(); values['cnbc']['QuickQuoteResult']['QuickQuote'][0]['last'] = '105'
        self.capture(values,at='2026-10-15T17:46:00Z')
        with self.assertRaisesRegex(ValueError,'latest quote comparison'):
            commit_preview(self.path,preview['id'],'2026-10-15T17:46:00Z')

    def test_corrupt_originals_cannot_be_used_at_preview_or_commit(self):
        report = self.capture(); preview = self.preview(report)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE')
            db.execute("UPDATE market_source_documents SET body_bytes=X'00'")
        for operation in (lambda:self.preview(report),lambda:commit_preview(self.path,preview['id'],AT)):
            with self.assertRaisesRegex(ValueError,'custody'): operation()
        self.assertEqual(read_ledger(self.path)['event_count'],1)

    def test_current_issuer_identity_cannot_silently_change(self):
        report = self.capture(); preview = self.preview(report)
        changed = {**identity(),'title':'Another issuer Common Stock'}
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO instrument_revisions(ticker,batch_id,retrieved_at,evidence_json) VALUES(?,?,?,?)', ('TEST','fixture',changed['retrieved_at'],encoded({k:v for k,v in changed.items() if k not in ('revision_id','retrieved_at')})))
        with self.assertRaisesRegex(ValueError,'identities differ'): self.preview(report)
        with self.assertRaisesRegex(ValueError,'identities differ'): commit_preview(self.path,preview['id'],AT)

    def test_changed_policy_or_ledger_requires_another_preview(self):
        preview = self.preview()
        with patch('equity_workspace.COMPARED_POLICY',{**COMPARED_POLICY,'fees_bps':99}):
            with self.assertRaisesRegex(ValueError,'Policy'): commit_preview(self.path,preview['id'],AT)
        append_event(self.path, {'event_id':'another-funding','kind':'deposit','amount':'1','occurred_at':AT,'currency':'USD','source':'Explicit simulated funding change'})
        with self.assertRaisesRegex(ValueError,'Ledger changed'): commit_preview(self.path,preview['id'],AT)

    def test_cash_risk_shares_and_public_purchase_rules_still_apply(self):
        report = self.capture()
        for kwargs in ({'shares':'21'},{'shares':'1000'},{'shares':'NaN'},{'shares':'0'},{'side':'sell'},{'strategy':'public_purchase'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.preview(report,**kwargs)
        save_form4(self.path,XML,ACCESSION,URL,'2026-10-10',AT)
        self.assertEqual(self.preview(report,strategy='public_purchase')['event']['kind'],'buy')

    def test_failure_during_second_insert_rolls_back_both_entries(self):
        preview = self.preview()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail_paper_mark BEFORE INSERT ON paper_ledger_events WHEN json_extract(NEW.payload,'$.kind')='mark' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError): commit_preview(self.path,preview['id'],AT)
        self.assertEqual(read_ledger(self.path)['event_count'],1)

    def test_cached_marks_cannot_overwrite_a_newer_compared_reference(self):
        preview = self.preview(); commit_preview(self.path,preview['id'],AT)
        # A valid but older cached quote would otherwise replace the new mark.
        item = {**self.item,'quote_as_of':'2026-10-15T17:30:00Z'}
        with sqlite3.connect(self.path) as db:
            version = db.execute('INSERT INTO saved_list_versions(created_at,model_version,basis,item_count,content_hash) VALUES(?,?,?,?,?)',(AT,'fixture','Older cached quote',1,'fixture2')).lastrowid
            db.execute('INSERT INTO saved_list_items VALUES(?,?,?,?)',(version,'TEST',item['status'],encoded(item)))
        response = self.client.post('/api/equity-workspace/paper/marks')
        self.assertEqual(response.status_code,422)
        self.assertIn('predates',response.json()['detail'])
        with self.assertRaisesRegex(ValueError,'predates'):
            make_preview(self.path,'TEST','sell','1','fundamentals',AT)
        sold = self.preview(side='sell',shares='10')
        commit_preview(self.path,sold['id'],AT)
        ledger = read_ledger(self.path)
        self.assertEqual(ledger['positions'],[])
        self.assertEqual(ledger['cash'],'9996.000000')
