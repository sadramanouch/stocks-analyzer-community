"""Operational evidence must stay read-only and distinguish schedules from outputs."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.testclient import TestClient

from system_status import JOBS, make_status_router, snapshot


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.db'
        with sqlite3.connect(self.path) as db:
            for table, columns in {
                'position_snapshots': 'snapshot_date TEXT',
                'research_refresh_jobs': 'id INTEGER,started_at TEXT,updated_at TEXT,state TEXT,completed INTEGER,failed INTEGER',
                'source_campaigns': 'id INTEGER,kind TEXT,updated_at TEXT,state TEXT,cursor INTEGER',
                'equity_monitor_runs': 'id INTEGER,started_at TEXT,finished_at TEXT,state TEXT,completed INTEGER,imported INTEGER',
                'forward_checkpoints': 'id INTEGER,observed_at TEXT',
                'benchmark_runs': 'id INTEGER,started_at TEXT,finished_at TEXT,state TEXT',
                'signal_trial_runs': 'id INTEGER,started_at TEXT,finished_at TEXT,state TEXT',
                'quote_check_reports': 'captured_at TEXT,ticker TEXT',
                'equity_monitor_settings': 'id INTEGER,enabled INTEGER',
                'benchmark_settings': 'id INTEGER,enabled INTEGER',
                'signal_trial_settings': 'id INTEGER,enabled INTEGER',
                'broker_flex_receipts': 'id TEXT', 'broker_flex_downloads': 'id TEXT',
                'paper_ledger_events': 'id TEXT', 'signal_trials': 'id TEXT',
                'signal_trial_observations': 'id TEXT', 'saved_list_versions': 'id INTEGER',
                'saved_list_items': 'version_id INTEGER,payload_json TEXT',
            }.items():
                db.execute(f'CREATE TABLE {table}({columns})')
            db.execute("INSERT INTO research_refresh_jobs VALUES(1,'2026-10-15T20:50:00Z','2026-10-15T20:51:00Z','failed',0,1)")
            db.execute('INSERT INTO equity_monitor_settings VALUES(1,0)')
            db.execute('INSERT INTO benchmark_settings VALUES(1,1)')
            db.execute('INSERT INTO signal_trial_settings VALUES(1,1)')
            db.execute('INSERT INTO saved_list_versions VALUES(1)')
            db.execute('INSERT INTO saved_list_versions VALUES(2)')
            db.executemany('INSERT INTO saved_list_items VALUES(?,?)', [(1,'{"status":"documented_inputs"}'), (2,'{"status":"documented_inputs"}'), (2,'{"status":"blocked"}')])
        trigger = CronTrigger(hour=16, minute=30, timezone='America/New_York')
        self.jobs = [SimpleNamespace(id=j[0], trigger=trigger, next_run_time=datetime(2026,10,16,20,30,tzinfo=timezone.utc)) for j in JOBS]
        self.scheduler = SimpleNamespace(state=1, get_jobs=lambda:self.jobs)

    def test_running_schedule_does_not_turn_failed_or_absent_outputs_into_success(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No external request allowed')):
            result = snapshot(self.path, self.scheduler)
        self.assertEqual(result['scheduler_state'], 'running')
        self.assertTrue(result['all_expected_jobs_registered'])
        self.assertEqual(result['jobs'][1]['latest_record']['state'], 'failed')
        self.assertIsNone(result['jobs'][0]['latest_record'])
        self.assertFalse(result['jobs'][3]['collection_enabled'])
        self.assertEqual(result['jobs'][0]['timezone'], 'America/New_York')
        self.assertEqual(result['jobs'][0]['next_run_at'], '2026-10-16T20:30:00+00:00')
        self.assertEqual(result['saved_list'], {'version':2,'records':2,'documented_inputs':1})
        self.assertEqual(result['counts']['broker_flex_receipts'], 0)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_unavailable_paused_missing_and_stopped_schedulers_remain_explicit(self):
        self.assertEqual(snapshot(self.path,None)['scheduler_state'], 'unavailable')
        self.jobs.pop()
        self.scheduler.state=2
        result=snapshot(self.path,self.scheduler)
        self.assertEqual(result['scheduler_state'],'paused')
        self.assertFalse(result['all_expected_jobs_registered'])
        self.assertFalse(result['jobs'][-1]['registered'])
        self.scheduler.state=0
        self.assertEqual(snapshot(self.path,self.scheduler)['scheduler_state'],'stopped')

    def test_presence_flags_never_disclose_secrets_or_prove_a_download(self):
        values={'IBKR_FLEX_TOKEN':'reporting-token-private','IBKR_FLEX_QUERY_ID':'987654321',
                'QUIVER_API_KEY':'private-quiver-key','NANSEN_API_KEY':'private-nansen-key'}
        with patch.dict('os.environ',values):
            result=snapshot(self.path,self.scheduler)
        raw=json.dumps(result)
        for secret in values.values():self.assertNotIn(secret,raw)
        self.assertTrue(all(result['access'].values()))
        self.assertEqual(result['counts']['broker_flex_receipts'],0)

    def test_missing_database_fails_closed_without_creating_a_database(self):
        missing=Path(self.temp.name)/'missing.db'
        app=FastAPI();app.include_router(make_status_router(missing,lambda:self.scheduler))
        response=TestClient(app).get('/api/system-status')
        self.assertEqual(response.status_code,503)
        self.assertNotIn(str(missing),response.text)
        self.assertFalse(missing.exists())

    def test_quote_records_are_shared_outputs_not_individual_execution_receipts(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO quote_check_reports VALUES('2026-10-15T20:10:00Z','GME')")
        result=snapshot(self.path,self.scheduler)
        self.assertEqual(result['jobs'][-1]['latest_record'],result['jobs'][-2]['latest_record'])
        self.assertEqual(result['jobs'][-1]['record_kind'],'shared archived quote')


if __name__=='__main__':unittest.main()
