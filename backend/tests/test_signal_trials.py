import copy
import json
import sqlite3
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_equity_workspace as fixture
from test_source_history import AT, XML, ACCESSION, URL
from ownership_history import save_form4
from market_history import init_market_history
from market_provenance import save_source
from research_store import encoded
from signal_trials import (init_trials, enroll, collect, overview, connect, sessions_after,
                           close_at, make_trials_router, digest, trial_inputs)


class SignalTrialTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.EquityWorkspaceTests(); self.fx.setUp(); self.path = self.fx.path
        init_market_history(self.path); init_trials(self.path); self.fx.parsed_purchase()
        # Financial parsing has its own original-receipt suite; here it supplies
        # an explicit unit fixture so these tests isolate prospective custody.
        self.context = {'status':'source_context_available','sources':[{'cik':'0001234567'}],'baseline_differences':[],
                        'periods':[{'annual_inputs_coherent':True,'annual_fields':{k:{'status':'source_matched'} for k in ('revenue','net_income','operating_cf','capex')}}]}
        self.context['content_sha256']=digest(encoded(self.context))
        self.context_patch=patch('signal_trials.financial_context',side_effect=lambda *args:copy.deepcopy(self.context));self.context_patch.start()
        self.raw_action = None; self.missing = None; self.wrong_close = False; self.changed = False
        self.bad_identity = None; self.source_at = None
        self.app = FastAPI(); self.router = make_trials_router(self.path); self.app.include_router(self.router)
        self.client = TestClient(self.app)

    def tearDown(self): self.context_patch.stop();self.fx.tearDown()

    def row(self):
        with connect(self.path) as db: return dict(db.execute('SELECT * FROM signal_trials').fetchone())

    def state(self, at=AT):
        with connect(self.path) as db: return overview(db, at)

    def history(self, ticker, start, end, at):
        days = [start] + sessions_after(start, 4)
        days = [d for d in days if d <= end and d != self.missing]
        prices = [100+i*(2 if ticker=='TEST' else 1)+(1 if self.changed and i==0 else 0) for i in range(len(days))]
        stamps = [int(close_at(d).timestamp()) for d in days]
        meta = {'symbol': ticker, 'currency': self.bad_identity or 'USD', 'exchangeTimezoneName':'America/New_York', 'instrumentType':'ETF' if ticker=='SPY' else 'EQUITY'}
        events = {self.raw_action:{'1':{'date':stamps[-1], 'amount':1, 'numerator':2, 'denominator':1}}} if self.raw_action else {}
        data = {'meta': meta,'timestamp': stamps,'indicators':{'quote':[{k:prices for k in ('open','high','low','close')}],'adjclose':[{'adjclose':prices}]},'events': events}
        body = json.dumps({'chart':{'result':[data],'error':None}}).encode()
        source = save_source(self.path,ticker,'https://query2.finance.yahoo.com/v8/finance/chart/'+ticker,'yahoo_original_chart',200,{},body,self.source_at or at)
        return {'schema':'yahoo-reference-history-v1','price_basis':'split_adjusted','adjusted_close_basis':'vendor_total_return_proxy', 'metadata':meta,
                'bars':[{'date':d,**{k:p+(1 if self.wrong_close and k=='close' else 2 if self.wrong_close and k=='high' else 0) for k in ('open','high','low','close','adjusted_close')},'dividend':0,'split_ratio':0} for d,p in zip(days,prices)],
                'omitted_price_dates':[], 'original_source_document_id':source}

    def observe(self, day): return collect(self.path, self.history, day+'T23:00:00+00:00')

    def test_enrollment_is_prospective_deduplicated_and_ledger_is_untouched(self):
        first = enroll(self.path, AT, enable=True); again = enroll(self.path, AT)
        self.assertEqual(len(first['new_trial_ids']),1); self.assertEqual(again['new_trial_ids'],[])
        row = self.row(); self.assertEqual(row['entry_date'],'2026-10-14'); self.assertEqual(row['exit_date'],'2026-10-20')
        self.assertEqual(self.state()['trials'][0]['state'],'waiting_for_entry')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paper_ledger_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM signal_trial_batches').fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE signal_trials SET ticker='OTHER'")
        self.assertTrue(self.state()['enabled'])

    def test_two_lines_in_one_filing_are_one_trial(self):
        transaction=XML.split('<nonDerivativeTable>')[1].split('</nonDerivativeTable>')[0]
        xml=XML.replace('</nonDerivativeTable>',transaction.replace('10.5','11.5')+'</nonDerivativeTable>')
        # Another accession is another filing; repeated ingestion of the same original never adds a trial.
        enroll(self.path, AT); self.fx.parsed_purchase(); self.assertEqual(enroll(self.path,AT)['new_trial_ids'],[])
        save_form4(self.path,xml,ACCESSION.replace('000001','000002'),URL.replace('000001','000002'),'2026-10-10',AT)
        self.assertEqual(len(enroll(self.path,AT)['new_trial_ids']),1)
        with connect(self.path) as db:
            rows=list(db.execute('SELECT * FROM signal_trials'));self.assertEqual(len(rows),2)
            self.assertEqual(sorted(len(trial_inputs(r)['grouped_purchase_lines']) for r in rows),[1,2])

    def test_five_timely_original_windows_replay_exact_costs(self):
        enroll(self.path, AT)
        days = json.loads(self.row()['input_json'])['sessions']
        for day in days:
            result = self.observe(day); self.assertEqual(result['blocked'],[]); self.assertEqual(len(result['collected']),1)
        state = self.state(days[-1]+'T23:00:00Z'); trial=state['trials'][0]; r=trial['result']
        factor=Decimal('0.999')**2/Decimal('1.001')**2
        self.assertEqual(Decimal(r['gross_stock_reference_return']),Decimal('.08'))
        self.assertEqual(Decimal(r['costed_stock_reference_return']),Decimal('1.08')*factor-1)
        self.assertEqual(Decimal(r['costed_benchmark_reference_return']),Decimal('1.04')*factor-1)
        self.assertEqual(Decimal(r['reference_difference']),Decimal('.04')*factor)
        self.assertEqual(trial['state'],'completed_price_reference'); self.assertEqual(state['completed_trials'],1)
        self.assertIsNone(r['verified_account_return']); self.assertIsNone(r['verified_executed_copy_return'])
        self.assertEqual(state['verified_executed_returns'],0)
        self.assertEqual(self.observe(days[-1])['collected'],[])

    def test_same_date_or_unfinished_entry_never_collects(self):
        enroll(self.path, AT)
        with patch('signal_trials.fetch_history') as fetch:
            r=collect(self.path,at='2026-10-14T20:00:00Z'); self.assertEqual(r['collected'],[]); fetch.assert_not_called()

    def test_missing_session_never_shifts_entry_or_exit(self):
        enroll(self.path, AT); self.missing='2026-10-14'
        r=self.observe('2026-10-14'); self.assertEqual(len(r['blocked']),1)
        self.assertEqual(self.row()['entry_date'],'2026-10-14')
        r=self.observe('2026-10-15'); self.assertEqual(len(r['blocked']),1)
        self.assertEqual(self.state('2026-10-15T23:00:00Z')['trials'][0]['state'],'blocked')

    def test_late_capture_is_not_backfilled(self):
        enroll(self.path,AT)
        with patch('signal_trials.fetch_history') as fetch:
            r=collect(self.path,at='2026-10-22T23:00:00Z'); self.assertEqual(len(r['blocked']),1);fetch.assert_not_called()
        self.assertIsNone(self.state('2026-10-22T23:00:00Z')['trials'][0]['result'])

    def test_request_finishing_after_deadline_cannot_use_its_earlier_start_time(self):
        enroll(self.path,AT)
        with patch('signal_trials.now',side_effect=['2026-10-14T23:00:00Z','2026-10-15T20:00:00Z']):
            r=collect(self.path,self.history)
        self.assertIn('next scheduled close',r['blocked'][0]['message'])

    def test_each_raw_action_type_blocks_even_if_normalized_action_is_zero(self):
        enroll(self.path,AT)
        for kind in ('splits','dividends','capitalGains'):
            self.raw_action=kind
            with self.subTest(kind=kind):
                r=self.observe('2026-10-14'); self.assertIn('corporate action',r['blocked'][0]['message'])

    def test_original_currency_and_instrument_are_checked(self):
        enroll(self.path,AT);self.bad_identity='CAD'
        self.assertTrue(self.observe('2026-10-14')['blocked'])

    def test_normalized_close_cannot_differ_from_original_array(self):
        enroll(self.path,AT);self.wrong_close=True
        self.assertIn('original vendor array',self.observe('2026-10-14')['blocked'][0]['message'])

    def test_partial_window_has_no_invented_zero_return(self):
        enroll(self.path,AT);self.observe('2026-10-14')
        state=self.state('2026-10-14T23:00:00Z');trial=state['trials'][0]
        self.assertEqual(trial['state'],'observing_planned_window');self.assertIsNone(trial['result'])
        self.assertIsNone(trial['observations'][0]['costed_stock_reference_return'])
        self.assertEqual(state['completed_trials'],0)

    def test_original_bytes_hash_and_source_time_are_checked(self):
        enroll(self.path,AT); self.source_at='2026-10-15T23:00:00Z'
        r=self.observe('2026-10-14');self.assertIn('after',r['blocked'][0]['message'])
        self.source_at=None
        def bad(*args):
            payload=self.history(*args)
            with connect(self.path) as db:
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='market_source_documents'").fetchall():db.execute('DROP TRIGGER '+row[0])
                db.execute("UPDATE market_source_documents SET body_bytes=x'7b7d'")
            return payload
        r=collect(self.path,bad,'2026-10-14T23:00:00Z');self.assertIn('hash',r['blocked'][0]['message'])

    def test_changed_previously_captured_close_requires_review(self):
        enroll(self.path,AT);self.observe('2026-10-14');self.changed=True
        r=self.observe('2026-10-15'); self.assertIn('changed',r['blocked'][0]['message'])

    def test_stale_financials_or_non_usd_research_cannot_enroll(self):
        for changes in ({'status':'needs_review'},{'quote_currency':'CAD'},{'fcf':0}):
            self.fx.store({**self.fx.item,**changes});self.assertEqual(enroll(self.path,AT)['new_trial_ids'],[])

    def test_unknown_calendar_and_missing_deadline_year_are_rejected(self):
        with self.assertRaises(ValueError):sessions_after('2026-12-31',5)
        self.assertEqual(sessions_after('2026-11-25',2),['2026-11-27','2026-11-30'])
        self.assertEqual(close_at('2026-11-27').hour,18) # 13:00 New York in UTC.

    def test_later_amendment_blocks_completion_without_rewriting_trial(self):
        enroll(self.path,AT);before=self.row()['input_sha256']
        amended=XML.replace('<documentType>4</documentType>','<documentType>4/A</documentType><dateOfOriginalSubmission>2026-10-10</dateOfOriginalSubmission>')
        save_form4(self.path,amended,ACCESSION.replace('000001','000002'),URL.replace('000001','000002'),'2026-10-14','2026-10-14T22:00:00Z')
        r=self.observe('2026-10-14');self.assertIn('amendment',r['blocked'][0]['message'])
        self.assertEqual(self.row()['input_sha256'],before)

    def test_tampered_trial_or_observation_is_detected(self):
        enroll(self.path,AT);self.observe('2026-10-14')
        with connect(self.path) as db:
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='signal_trial_observations'").fetchall():db.execute('DROP TRIGGER '+row[0])
            db.execute("UPDATE signal_trial_observations SET result_json='{}'")
        self.assertTrue(self.state('2026-10-14T23:00:00Z')['integrity_errors'])

    def test_storage_reserve_blocks_requests(self):
        enroll(self.path,AT)
        with patch('signal_trials.shutil.disk_usage',return_value=SimpleNamespace(free=0)),patch('signal_trials.fetch_history') as fetch:
            with self.assertRaisesRegex(ValueError,'storage reserve'):collect(self.path,at='2026-10-14T23:00:00Z')
            fetch.assert_not_called()

    def test_per_fetch_storage_guard_and_due_bound_preserve_existing_sources(self):
        enroll(self.path,AT)
        with patch('signal_trials.shutil.disk_usage',return_value=SimpleNamespace(free=258*1024*1024)),patch('signal_trials.fetch_history') as fetch:
            self.assertIn('storage reserve',collect(self.path,at='2026-10-14T23:00:00Z')['blocked'][0]['message']);fetch.assert_not_called()
        with patch.dict('signal_trials.POLICY',{'maximum_due_trials_per_pass':0}),patch('signal_trials.fetch_history') as fetch:
            # Any policy mutation must block an existing trial before a new request.
            with self.assertRaisesRegex(ValueError,'policy version'):collect(self.path,at='2026-10-14T23:00:00Z')
            fetch.assert_not_called()

    def test_unmatched_financial_issuer_cannot_enroll(self):
        self.context['sources'][0]['cik']='0000000001'
        self.context.pop('content_sha256');self.context['content_sha256']=digest(encoded(self.context))
        self.assertEqual(enroll(self.path,AT)['new_trial_ids'],[])

    def test_partial_or_incoherent_original_annual_context_cannot_enroll(self):
        self.context['periods'][0]['annual_inputs_coherent']=False
        self.context.pop('content_sha256');self.context['content_sha256']=digest(encoded(self.context))
        self.assertEqual(enroll(self.path,AT)['new_trial_ids'],[])
        self.context['periods'][0]['annual_inputs_coherent']=True
        self.context['periods'][0]['annual_fields']['capex']['status']='unavailable'
        self.context.pop('content_sha256');self.context['content_sha256']=digest(encoded(self.context))
        self.assertEqual(enroll(self.path,AT)['new_trial_ids'],[])

    def test_bounded_policy_rejects_oversized_enrollment_instead_of_selecting_subset(self):
        with patch.dict('signal_trials.POLICY',{'maximum_new_trials_per_pass':0}):
            with self.assertRaisesRegex(ValueError,'No subset'):enroll(self.path,AT)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM signal_trials').fetchone()[0],0)

    def test_router_pause_export_scheduler_and_worker_failure(self):
        enroll(self.path,AT,enable=True);identifier=self.row()['id']
        self.assertEqual(self.client.get('/api/signal-trials/trials/'+identifier).status_code,200)
        frozen=self.client.get('/api/signal-trials/trials/'+identifier).json()
        self.assertEqual(digest(frozen['input_json']),frozen['input_sha256'])
        self.observe('2026-10-14')
        exported=self.client.get('/api/signal-trials/observations/1').json()
        self.assertEqual(digest(exported['input_json']),exported['input_sha256'])
        self.assertEqual(digest(exported['result_json']),exported['result_sha256'])
        self.assertEqual(self.client.get('/api/signal-trials/observations/999').status_code,404)
        self.assertEqual(self.client.get('/api/signal-trials/trials/unknown').status_code,404)
        self.assertEqual(self.client.post('/api/signal-trials/pause').json()['enabled'],False)
        with patch('signal_trials.enroll') as frozen:self.router.scheduled_collect();frozen.assert_not_called()
        with patch('signal_trials.now',return_value=AT),patch('signal_trials.threading.Thread') as thread:
            thread.return_value.start.side_effect=RuntimeError('fixture worker cannot start')
            with self.assertRaises(RuntimeError):self.client.post('/api/signal-trials/start')
        self.assertFalse(self.state()['enabled']);self.assertEqual(self.state()['runs'][0]['state'],'blocked')

    def test_repeated_read_does_not_write_any_account_or_trial_records(self):
        enroll(self.path,AT)
        with connect(self.path) as db: before=db.total_changes;overview(db,AT);overview(db,AT);self.assertEqual(db.total_changes,before)

    def test_worker_success_releases_lock_and_does_not_duplicate_trials(self):
        with patch('signal_trials.now',return_value=AT),patch('signal_trials.threading.Thread',fixture.InlineThread):
            self.assertEqual(self.client.post('/api/signal-trials/start').status_code,200)
            self.assertEqual(self.client.post('/api/signal-trials/start').status_code,200)
        state=self.state();self.assertTrue(state['enabled']);self.assertEqual(len(state['trials']),1)
        self.assertEqual(state['runs'][0]['state'],'completed')

    def test_observed_price_reference_does_not_survive_removed_original_filing(self):
        enroll(self.path,AT);self.observe('2026-10-14')
        with connect(self.path) as db:
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='ownership_documents'").fetchall():db.execute('DROP TRIGGER '+row[0])
            db.execute('DELETE FROM ownership_documents')
        state=self.state('2026-10-14T23:00:00Z');self.assertEqual(state['completed_trials'],0);self.assertTrue(state['integrity_errors'])


if __name__=='__main__':unittest.main()
