"""Authenticated, local-only operational evidence; never starts a collector."""
import os
import sqlite3
from datetime import datetime, timezone

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
from fastapi import APIRouter, HTTPException

from ibkr_connection import settings as broker_settings


# The scheduler supplies the actual trigger and next fire time. These labels only
# describe which persisted record can corroborate each job's work.
JOBS = (
    ('daily_update', 'Legacy price and snapshot refresh', 'position_snapshots',
     'snapshot_date', ('snapshot_date',), None),
    ('documented_research_refresh', 'Financial evidence refresh', 'research_refresh_jobs',
     'id', ('started_at', 'updated_at', 'state', 'completed', 'failed'), None),
    ('bounded_source_campaign', 'Bounded source campaigns', 'source_campaigns',
     'id', ('kind', 'updated_at', 'state', 'cursor'), None),
    ('focused_sec_monitor', 'Watched SEC disclosures', 'equity_monitor_runs',
     'id', ('started_at', 'finished_at', 'state', 'completed', 'imported'), 'equity_monitor_settings'),
    ('forward_paper_checkpoint', 'Paper ledger checkpoints', 'forward_checkpoints',
     'id', ('observed_at',), None),
    ('prospective_benchmark_collection', 'Prospective benchmark observations', 'benchmark_runs',
     'id', ('started_at', 'finished_at', 'state'), 'benchmark_settings'),
    ('prospective_signal_trials', 'Prospective purchase trials', 'signal_trial_runs',
     'id', ('started_at', 'finished_at', 'state'), 'signal_trial_settings'),
    ('active_trial_quote_checks', 'Active-trial quote observations', 'quote_check_reports',
     'rowid', ('captured_at', 'ticker'), 'signal_trial_settings'),
    ('active_trial_closing_quote_checks', 'Closing quote observations', 'quote_check_reports',
     'rowid', ('captured_at', 'ticker'), 'signal_trial_settings'),
)


def snapshot(path, scheduler):
    at = datetime.now(timezone.utc).isoformat()
    state = 'unavailable'
    runtime = {}
    if scheduler is not None:
        state = {STATE_RUNNING: 'running', STATE_PAUSED: 'paused'}.get(scheduler.state, 'stopped')
        for job in scheduler.get_jobs():
            next_run = getattr(job, 'next_run_time', None)
            runtime[job.id] = {
                'next_run_at': next_run.isoformat() if next_run else None,
                'trigger': str(job.trigger),
                'timezone': str(getattr(job.trigger, 'timezone', 'UTC')),
            }
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        jobs = []
        for identifier, title, table, order, columns, setting in JOBS:
            # SQL identifiers above are compile-time constants, never user input.
            row = db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY {order} DESC LIMIT 1').fetchone()
            enabled = db.execute(f'SELECT enabled FROM {setting} WHERE id=1').fetchone() if setting else None
            jobs.append({'id': identifier, 'title': title, 'registered': identifier in runtime,
                         'collection_enabled': bool(enabled[0]) if enabled else None,
                         'latest_record': dict(row) if row else None,
                         'record_kind': 'snapshot day' if table == 'position_snapshots' else
                                        'shared archived quote' if table == 'quote_check_reports' else
                                        'campaign progress' if table == 'source_campaigns' else 'recorded work',
                         **runtime.get(identifier, {'next_run_at': None, 'trigger': None, 'timezone': None})})
        counts = {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
                  ('broker_flex_receipts', 'broker_flex_downloads', 'paper_ledger_events',
                   'signal_trials', 'signal_trial_observations', 'quote_check_reports')}
        version = db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
        saved = db.execute('SELECT COUNT(*) FROM saved_list_items WHERE version_id=?', (version,)).fetchone()[0]
        documented = db.execute("SELECT COUNT(*) FROM saved_list_items WHERE version_id=? AND json_extract(payload_json,'$.status')='documented_inputs'", (version,)).fetchone()[0]
    token, query, configured = broker_settings()
    return {
        'observed_at': at, 'scheduler_state': state,
        'all_expected_jobs_registered': all(j['registered'] for j in jobs), 'jobs': jobs,
        'access': {'ibkr_token_present': bool(token), 'ibkr_query_present': bool(query),
                   'ibkr_reporting_configured': configured, 'quiver_key_present': bool(os.environ.get('QUIVER_API_KEY')),
                   'nansen_key_present': bool(os.environ.get('NANSEN_API_KEY'))},
        'counts': counts, 'saved_list': {'version': version, 'records': saved, 'documented_inputs': documented},
        'limits': 'Schedule registration and archived records are separate evidence. Dates shown are the latest stored outputs, not a claim that every scheduled attempt succeeded. Quote jobs share an archive and may skip when no active trial is due. Source-matched annual inputs do not certify every ratio or price. Broker downloads, executable price accuracy and profitability require separate validation.',
    }


def make_status_router(path, get_scheduler):
    router = APIRouter(prefix='/api/system-status', tags=['Operational status'])

    @router.get('')
    def status():
        try:
            return snapshot(path, get_scheduler())
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            raise HTTPException(503, 'Operational evidence is unavailable; no health or collection claim can be made.') from None

    return router
