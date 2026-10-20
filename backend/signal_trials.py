"""Preregistered, isolated public-purchase price-reference trials.

No orders, synthetic account funding, retrospective signal selection or trader P&L.
Every planned session needs timely, immutable original vendor evidence.
"""
import hashlib
import json
import shutil
import sqlite3
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException

from benchmark_study import close_at, vendor_price, source_body
from equity_workspace import connect, decision, latest_items, financial_gate, POLICY as SELECTION_POLICY
from ownership_history import parse_form4
from financial_context import financial_context
from market_history import completed_day, fetch_history, validate_history, NY
from market_provenance import CALENDAR, calendar_check
from research_store import encoded, immutable, instant, now

POLICY = {
    'version': 'prospective-purchase-trials-v1', 'benchmark': 'SPY', 'currency': 'USD',
    'selection': 'Fresh source-reparsed SEC common-stock purchases plus currently documented positive annual free cash flow under the frozen equity policy. The original SEC financial context must match the purchase issuer and saved annual facts.',
    'sampling': 'One trial per issuer ticker and original filing accession; all eligible transaction lines in that filing are grouped. All qualifying filings in the observed universe are enrolled; no performance-based selection.',
    'entry': 'Close of the first scheduled session on a strictly later New York date than registration.',
    'holding_sessions': 5,
    'exit': 'Close of the fifth scheduled session including the entry session (four close-to-close intervals). Missing sessions never shift either endpoint.',
    'fees_bps_each_side': 10, 'slippage_bps_each_side': 10,
    'cost_model': 'Fractional unit price reference: entry close × (1 + slippage) × (1 + fee); exit close × (1 − slippage) × (1 − fee). Same costs for stock and SPY. No minimum fees, rounding, taxes, liquidity or FX model.',
    'price_basis': 'Original Yahoo split-adjusted daily closes, not executable quotes. Any reported split, dividend or capital distribution in either window blocks a price-only comparison.',
    'capture': 'After 17:30 New York time. Every planned close must be archived before the next scheduled session close. Missing or conflicting evidence blocks the result; no late backfill substitutes.',
    'maximum_new_trials_per_pass': 25, 'maximum_due_trials_per_pass': 25,
    'limitations': 'Observed source universe only; correlated filings are not independent samples. Price and action completeness remain unverified. No account return, executable copy return, trader profitability, expected return or proof of a repeatable edge. Your paper cash/share ledger is separate.',
}
RESERVE = 256 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def init_trials(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS signal_trial_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL);
        INSERT OR IGNORE INTO signal_trial_settings VALUES(1,0);
        CREATE TABLE IF NOT EXISTS signal_trial_batches(id INTEGER PRIMARY KEY,registered_at TEXT NOT NULL,input_json TEXT NOT NULL,input_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signal_trials(id TEXT PRIMARY KEY,ticker TEXT NOT NULL,accession TEXT NOT NULL,registered_at TEXT NOT NULL,
          entry_date TEXT NOT NULL,exit_date TEXT NOT NULL,input_json TEXT NOT NULL,input_sha256 TEXT NOT NULL,UNIQUE(ticker,accession));
        CREATE TABLE IF NOT EXISTS signal_trial_observations(id INTEGER PRIMARY KEY,trial_id TEXT NOT NULL,session_date TEXT NOT NULL,observed_at TEXT NOT NULL,
          input_json TEXT NOT NULL,input_sha256 TEXT NOT NULL,result_json TEXT NOT NULL,result_sha256 TEXT NOT NULL,UNIQUE(trial_id,session_date));
        CREATE TABLE IF NOT EXISTS signal_trial_runs(id INTEGER PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,state TEXT NOT NULL,message TEXT NOT NULL,detail_json TEXT);
        ''')
        for table in ('signal_trial_batches', 'signal_trials', 'signal_trial_observations'):
            immutable(db, table)
        db.execute("UPDATE signal_trial_runs SET state='interrupted',finished_at=?,message='Worker interrupted; missing sessions remain missing.' WHERE state='running'", (now(),))


def sessions_after(day, count):
    result = []
    current = date.fromisoformat(day)
    for _ in range(40):
        current += timedelta(days=1)
        status = calendar_check(current.isoformat())['state']
        if status == 'unknown_year':
            raise ValueError('Publish the next calendar version before registering this window.')
        if status == 'scheduled_session':
            result.append(current.isoformat())
            if len(result) == count:
                return result
    raise ValueError('No bounded covered session window.')


def verified_json(row, field='input_json', sha='input_sha256'):
    if digest(row[field]) != row[sha]:
        raise ValueError('Immutable trial evidence fingerprint differs.')
    return json.loads(row[field])


def trial_inputs(row):
    data = verified_json(row)
    if data['policy'] != POLICY or data['calendar'] != CALENDAR or data['selection_policy'] != SELECTION_POLICY:
        raise ValueError('Trial method differs from its frozen policy version.')
    days = sessions_after(instant(row['registered_at']).astimezone(NY).date().isoformat(), POLICY['holding_sessions']+1)[:-1]
    if (data['sessions'] != days or row['entry_date'] != days[0] or row['exit_date'] != days[-1]
            or data['decision']['ticker'] != row['ticker'] or data['registered_at'] != row['registered_at']
            or data['accession'] != row['accession']):
        raise ValueError('Trial identity, registration or planned dates differ.')
    return data


def verify_registration_sources(db, frozen):
    if financial_gate(frozen['decision']['research'], frozen['registered_at']):
        raise ValueError('Frozen financial selection no longer replays.')
    context = dict(frozen['financial_context'])
    content = context.pop('content_sha256')
    if digest(encoded(context)) != content:
        raise ValueError('Frozen financial-context fingerprint differs.')
    current = financial_context(db, frozen['decision']['research'], frozen['decision']['research_version'])
    if current != frozen['financial_context']:
        raise ValueError('Financial context no longer matches its original source receipts.')
    for purchase in frozen['grouped_purchase_lines']:
        event = purchase['event']
        source = db.execute('SELECT * FROM ownership_documents WHERE accession=?', (event['accession'],)).fetchone()
        if not source or digest(source['document_xml']) != source['sha256'] or source['sha256'] != purchase['source_sha256']:
            raise ValueError('Frozen original purchase XML fingerprint differs.')
        parsed = parse_form4(source['document_xml'], event['accession'], source['source_url'], source['filed_date'], source['observed_at'])
        match = next((e for e in parsed['events'] if e['id'] == event['id']), None)
        if match and 'date_status' not in event:
            match = {k: v for k, v in match.items() if k != 'date_status'}
        if match != event or event['accession'] != frozen['accession'] or event['asset'] != frozen['decision']['ticker']:
            raise ValueError('Frozen purchase line differs from original XML or trial identity.')
        if not issuer_match(frozen['financial_context'], event):
            raise ValueError('Original financial and purchase issuer identities differ.')


def issuer_match(context, event):
    return (context['status'] == 'source_context_available' and not context.get('baseline_differences')
            and bool(context['sources']) and str(context['sources'][0].get('cik', '')).zfill(10) == str(event.get('issuer_cik', '')).zfill(10)
            and bool(context.get('periods')) and context['periods'][0]['annual_inputs_coherent']
            and all(context['periods'][0]['annual_fields'][name]['status'] == 'source_matched'
                    for name in ('revenue', 'net_income', 'operating_cf', 'capex')))


def enroll(path, at=None, enable=False):
    """Freeze all qualifying filings in one consistent read, then publish atomically."""
    at = at or now()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        version, items = latest_items(db)
        tickers = [r[0] for r in db.execute("SELECT DISTINCT json_extract(event_json,'$.asset') FROM trade_events WHERE json_extract(event_json,'$.transaction_code')='P' ORDER BY 1")]
        existing = {(r[0], r[1]) for r in db.execute('SELECT ticker,accession FROM signal_trials')}
        screened = []
        candidates = []
        for ticker in tickers:
            if ticker not in items:
                screened.append({'ticker': ticker, 'reasons': ['No current saved research record.'], 'eligible_filings': []})
                continue
            d = decision(db, ticker, at, (version, items))
            if d['research'].get('quote_currency') != 'USD':
                d['financial_gate_reasons'].append('An explicit USD research instrument is required.')
            groups = {}
            if not d['financial_gate_reasons']:
                for p in d['purchase_evidence']:
                    if p['eligible_now']:
                        groups.setdefault(p['event']['accession'], []).append(p)
            context = financial_context(db, d['research'], version) if groups else None
            if groups and any(not issuer_match(context, p['event']) for purchases in groups.values() for p in purchases):
                d['financial_gate_reasons'].append('Original SEC financial context must match the purchase issuer and saved annual facts.')
                groups = {}
            screened.append({'ticker': ticker, 'reasons': d['financial_gate_reasons'], 'eligible_filings': sorted(groups),
                             'excluded_purchase_lines': [{'id': p['event']['id'], 'reasons': p['reasons']} for p in d['purchase_evidence'] if not p['eligible_now']]})
            for accession, purchases in sorted(groups.items()):
                if (ticker, accession) not in existing:
                    candidates.append((d, accession, purchases, context))
        if len(candidates) > POLICY['maximum_new_trials_per_pass']:
            raise ValueError('More than 25 new filings qualify; publish a broader bounded policy before enrollment. No subset was selected.')
        identifiers = []
        if candidates:
            # The following session supplies a covered deadline even at year end.
            days = sessions_after(instant(at).astimezone(NY).date().isoformat(), POLICY['holding_sessions']+1)[:-1]
            for d, accession, purchases, context in candidates:
                data = {'policy': POLICY, 'selection_policy': SELECTION_POLICY, 'calendar': CALENDAR, 'registered_at': at,
                        'sessions': days, 'accession': accession, 'decision': d, 'financial_context': context,
                        'grouped_purchase_lines': purchases}
                raw = encoded(data)
                identifier = digest(encoded([d['ticker'], accession]))
                db.execute('INSERT INTO signal_trials VALUES(?,?,?,?,?,?,?,?)',
                           (identifier, d['ticker'], accession, at, days[0], days[-1], raw, digest(raw)))
                identifiers.append(identifier)
        receipt = encoded({'registered_at': at, 'research_version': version, 'policy': POLICY,
                           'screened': screened, 'new_trial_ids': identifiers})
        # Avoid recurring duplicate receipts when no source/selection state changed.
        previous = db.execute('SELECT * FROM signal_trial_batches ORDER BY id DESC LIMIT 1').fetchone()
        old = verified_json(previous) if previous else None
        changed = old is None or any(old[k] != json.loads(receipt)[k] for k in ('research_version', 'policy', 'screened'))
        if changed or identifiers:
            db.execute('INSERT INTO signal_trial_batches VALUES(NULL,?,?,?)', (at, receipt, digest(receipt)))
        if enable:
            db.execute('UPDATE signal_trial_settings SET enabled=1 WHERE id=1')
    return {'new_trial_ids': identifiers, 'screened_tickers': len(tickers), 'research_version': version}


def original_window(db, payload, ticker, days, at):
    """Match each exact close to original bytes; reject actions, identity and time errors."""
    validate_history(payload, ticker, days[0], days[-1], at)
    source = source_body(db, payload['original_source_document_id'])
    if not instant(source['observed_at']) or not instant(source['observed_at']) <= instant(at):
        raise ValueError('Price source was observed after the measurement time.')
    chart = json.loads(source['body_bytes'])['chart']['result'][0]
    if chart['meta'].get('instrumentType') != ('ETF' if ticker == 'SPY' else 'EQUITY'):
        raise ValueError('Original chart instrument type differs.')
    prices = {}
    for day in days:
        price, ref = vendor_price(db, payload, day)
        if instant(source['observed_at']) < close_at(day):
            raise ValueError('An unfinished close cannot be source evidence.')
        if not price.is_finite() or price <= 0:
            raise ValueError('Positive finite original close required.')
        prices[day] = str(price)
    if [b['date'] for b in payload['bars']] != days or payload.get('omitted_price_dates'):
        raise ValueError('Every exact planned close is required; no date substitution.')
    for kind, events in chart.get('events', {}).items():
        if kind not in ('splits', 'dividends', 'capitalGains'):
            raise ValueError('Unsupported original action fields require review.')
        for event in events.values():
            day = datetime.fromtimestamp(event['date'], NY).date().isoformat()
            if days[0] <= day <= days[-1]:
                raise ValueError('A reported corporate action requires reconciliation; price-only comparison blocked.')
    if any(Decimal(str(b['dividend'])) or Decimal(str(b['split_ratio'])) for b in payload['bars']):
        raise ValueError('Normalized corporate actions require reconciliation.')
    return prices, ref


def evaluate(db, row, inputs, at):
    frozen = trial_inputs(row)
    verify_registration_sources(db, frozen)
    days = inputs['sessions']
    if not days or days != frozen['sessions'][:len(days)] or inputs['trial_input_sha256'] != row['input_sha256']:
        raise ValueError('Observation window differs from the preregistration.')
    if inputs['observed_at'] != at or instant(at) >= close_at(sessions_after(days[-1], 1)[0]):
        raise ValueError('The planned close was not archived before the next scheduled close; no late backfill.')
    prices = {}
    refs = {}
    for ticker in (row['ticker'], 'SPY'):
        prices[ticker], refs[ticker] = original_window(db, inputs['histories'][ticker], ticker, days, at)
    for prior in db.execute('SELECT * FROM signal_trial_observations WHERE trial_id=? ORDER BY session_date', (row['id'],)):
        previous = verified_json(prior, 'result_json', 'result_sha256')
        for ticker in prices:
            for day, price in previous['closes'][ticker].items():
                if day in prices[ticker] and Decimal(prices[ticker][day]) != Decimal(price):
                    raise ValueError('A retained close changed in a later original archive; correction review required.')
    # Frozen known complex actions must not silently disappear from a price-only study.
    reviews = frozen['decision']['action_reviews']
    for action in reviews['splits'] + reviews['distributions']:
        if action['ticker'] in prices and days[0] <= action['effective_date'] <= days[-1]:
            raise ValueError('Known corporate action requires separate reconciliation.')
    if inputs['later_amendments']:
        raise ValueError('A later issuer/owner amendment requires original-line reconciliation.')
    complete = len(days) == POLICY['holding_sessions']
    expected_prior = set(days[:-1])
    captured = {r[0] for r in db.execute('SELECT session_date FROM signal_trial_observations WHERE trial_id=?', (row['id'],))}
    if not expected_prior <= captured:
        raise ValueError('An earlier scheduled observation is missing; no late backfill or endpoint shift.')
    result = {'session_date': days[-1], 'observed_at': at, 'status': 'completed_price_reference' if complete else 'observing_planned_window',
              'closes': prices, 'source_receipts': refs, 'gross_stock_reference_return': None, 'costed_stock_reference_return': None,
              'costed_benchmark_reference_return': None, 'reference_difference': None, 'verified_account_return': None,
              'verified_executed_copy_return': None}
    if complete:
        fee = Decimal(POLICY['fees_bps_each_side']) / 10000
        slip = Decimal(POLICY['slippage_bps_each_side']) / 10000
        factor = (1-slip)*(1-fee)/((1+slip)*(1+fee))
        ratios = {t: Decimal(p[days[-1]])/Decimal(p[days[0]]) for t, p in prices.items()}
        result.update(gross_stock_reference_return=str(ratios[row['ticker']]-1),
                      costed_stock_reference_return=str(ratios[row['ticker']]*factor-1),
                      costed_benchmark_reference_return=str(ratios['SPY']*factor-1),
                      reference_difference=str((ratios[row['ticker']]-ratios['SPY'])*factor))
    return result


def later_amendments(db, frozen, at):
    event = frozen['grouped_purchase_lines'][0]['event']
    owners = {o['cik'] for o in event.get('owners', [])}
    result = []
    for row in db.execute("SELECT accession,sha256,observed_at,metadata_json FROM ownership_documents WHERE json_extract(metadata_json,'$.amendment')=1 AND json_extract(metadata_json,'$.ticker')=?", (event['asset'],)):
        meta = json.loads(row['metadata_json'])
        if (instant(row['observed_at']) <= instant(at) and meta.get('issuer_cik') == event.get('issuer_cik')
                and (not meta.get('original_submission_date') or meta['original_submission_date'] == event['disclosed_at'][:10])
                and owners.intersection(o['cik'] for o in meta.get('owners', []))):
            result.append({k: row[k] for k in ('accession', 'sha256', 'observed_at')})
    return result


def collect(path, history=None, at=None):
    explicit_at = at
    at = at or now()
    if shutil.disk_usage(Path(path).parent).free < RESERVE:
        raise ValueError('Source collection paused below the 256 MiB storage reserve.')
    with connect(path) as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM signal_trials ORDER BY registered_at,id')]
        due = []
        blocked = []
        for row in rows:
            data = trial_inputs(row)
            days = [d for d in data['sessions'] if d <= completed_day(at).isoformat()]
            captured = {r[0] for r in db.execute('SELECT session_date FROM signal_trial_observations WHERE trial_id=?', (row['id'],))}
            if any(d not in captured and instant(at) >= close_at(sessions_after(d, 1)[0]) for d in days):
                blocked.append({'trial_id': row['id'], 'message': 'Planned archive deadline missed; no late backfill.'})
                continue
            if days and not db.execute('SELECT 1 FROM signal_trial_observations WHERE trial_id=? AND session_date=?', (row['id'], days[-1])).fetchone():
                due.append((row, data, days))
    if len(due) > POLICY['maximum_due_trials_per_pass']:
        raise ValueError('More than 25 trials are due; a broader bounded policy is required. No subset was selected.')
    collected = []
    cache = {}
    for row, frozen, days in due:
        try:
            if instant(at) >= close_at(sessions_after(days[-1], 1)[0]):
                raise ValueError('Planned archive deadline missed; no late backfill.')
            histories = {}
            for ticker in (row['ticker'], 'SPY'):
                key = (ticker, days[0], days[-1])
                if key not in cache:
                    if shutil.disk_usage(Path(path).parent).free < RESERVE + 4*1024*1024:
                        raise ValueError('Source collection paused to preserve the 256 MiB storage reserve.')
                    cache[key] = (history(ticker, days[0], days[-1], at) if history else fetch_history(ticker, days[0], days[-1], at, path, True))
                histories[ticker] = cache[key]
            with connect(path) as db:
                db.execute('BEGIN IMMEDIATE')
                captured_at = explicit_at or now()
                inputs = {'trial_input_sha256': row['input_sha256'], 'observed_at': captured_at, 'sessions': days,
                          'histories': histories, 'later_amendments': later_amendments(db, frozen, captured_at)}
                result = evaluate(db, row, inputs, captured_at)
                raw, output = encoded(inputs), encoded(result)
                db.execute('INSERT INTO signal_trial_observations VALUES(NULL,?,?,?,?,?,?,?)',
                           (row['id'], days[-1], captured_at, raw, digest(raw), output, digest(output)))
                collected.append({'trial_id': row['id'], 'session_date': days[-1], 'status': result['status']})
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            blocked.append({'trial_id': row['id'], 'message': str(exc)[:300]})
    return {'collected': collected, 'blocked': blocked, 'not_due_trials': len(rows)-len(due)}


def trial_view(db, row, at):
    frozen = trial_inputs(row)
    verify_registration_sources(db, frozen)
    rows = list(db.execute('SELECT * FROM signal_trial_observations WHERE trial_id=? ORDER BY session_date', (row['id'],)))
    observations = []
    for observation in rows:
        inputs = verified_json(observation)
        actual = evaluate(db, row, inputs, observation['observed_at'])
        result = verified_json(observation, 'result_json', 'result_sha256')
        if actual != result or result['session_date'] != observation['session_date']:
            raise ValueError('Retained observation differs from original-price replay.')
        observations.append({'id': observation['id'], 'input_sha256': observation['input_sha256'], 'result_sha256': observation['result_sha256'], **result})
    captured = {o['session_date'] for o in observations}
    missing = [d for d in frozen['sessions'] if d not in captured and instant(at) >= close_at(sessions_after(d, 1)[0])]
    amendments = later_amendments(db, frozen, at)
    state = 'blocked' if missing or amendments else observations[-1]['status'] if observations else 'waiting_for_entry'
    return {k: row[k] for k in ('id', 'ticker', 'accession', 'registered_at', 'entry_date', 'exit_date', 'input_sha256')} | {
        'state': state, 'purchase_lines': len(frozen['grouped_purchase_lines']),
        'buyers': sorted({p['event']['actor'] for p in frozen['grouped_purchase_lines']}),
        'missing_capture_sessions': missing, 'later_amendments': amendments, 'observations': observations,
        'result': observations[-1] if observations and state == 'completed_price_reference' else None}


def overview(db, at=None):
    at = at or now()
    trials = []
    errors = []
    for row in db.execute('SELECT * FROM signal_trials ORDER BY registered_at DESC,id'):
        try:
            trials.append(trial_view(db, row, at))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors.append({'id': row['id'], 'message': str(exc)[:300]})
    runs = [dict(r) for r in db.execute('SELECT * FROM signal_trial_runs ORDER BY id DESC LIMIT 5')]
    recent_issues = {}
    for run in runs:
        for issue in json.loads(run['detail_json'] or '{}').get('blocked', []):
            recent_issues.setdefault(issue['trial_id'], (run['started_at'], issue['message']))
    for trial in trials:
        issue = recent_issues.get(trial['id'])
        last = trial['observations'][-1]['observed_at'] if trial['observations'] else trial['registered_at']
        if issue and instant(issue[0]) >= instant(last):
            trial['collection_issue'] = issue[1]
            trial['state'] = 'blocked'
            trial['result'] = None
    batch = db.execute('SELECT * FROM signal_trial_batches ORDER BY id DESC LIMIT 1').fetchone()
    return {'policy': POLICY, 'observed_at': at, 'enabled': bool(db.execute('SELECT enabled FROM signal_trial_settings WHERE id=1').fetchone()[0]),
            'trials': trials, 'integrity_errors': errors, 'latest_screening': verified_json(batch) if batch else None,
            'completed_trials': sum(t['state'] == 'completed_price_reference' for t in trials),
            'verified_executed_returns': 0, 'runs': runs}


def make_trials_router(path):
    router = APIRouter(prefix='/api/signal-trials', tags=['Preregistered public-purchase trials'])
    lock = threading.Lock()

    @router.get('')
    def inspect():
        with connect(path) as db:
            db.execute('BEGIN')
            return overview(db)

    @router.get('/trials/{identifier}')
    def export(identifier: str):
        with connect(path) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM signal_trials WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Registered trial not found.')
            try:
                return {'input_sha256': row['input_sha256'], 'input_json': row['input_json'], 'inputs': trial_inputs(row), 'trial': trial_view(db, row, now())}
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                raise HTTPException(409, str(exc))

    @router.get('/observations/{identifier}')
    def export_observation(identifier: int):
        with connect(path) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM signal_trial_observations WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Prospective observation not found.')
            trial = db.execute('SELECT * FROM signal_trials WHERE id=?', (row['trial_id'],)).fetchone()
            try:
                inputs = verified_json(row)
                result = verified_json(row, 'result_json', 'result_sha256')
                if not trial or evaluate(db, trial, inputs, row['observed_at']) != result or result['session_date'] != row['session_date']:
                    raise ValueError('Original-price observation replay differs.')
                return {**dict(row), 'inputs': inputs, 'result': result}
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                raise HTTPException(409, str(exc))

    def worker(identifier):
        state, message, detail = 'blocked', 'Source collection failed; existing evidence retained.', None
        try:
            enrolled = enroll(path)
            collected = collect(path)
            detail = encoded({'enrollment': enrolled, **collected})
            state = 'needs_review' if collected['blocked'] else 'completed'
            message = 'Source pass complete. Only preregistered, timely observed windows can produce price references.'
        except ValueError as exc:
            message = str(exc)[:400]
        except Exception:
            pass
        finally:
            try:
                with connect(path) as db:
                    db.execute('UPDATE signal_trial_runs SET state=?,finished_at=?,message=?,detail_json=? WHERE id=?', (state, now(), message, detail, identifier))
                    if state == 'blocked':
                        db.execute('UPDATE signal_trial_settings SET enabled=0 WHERE id=1')
            finally:
                lock.release()

    @router.post('/start')
    def start():
        if not lock.acquire(blocking=False):
            raise HTTPException(409, 'A signal-trial pass is already running.')
        identifier = None
        try:
            enrolled = enroll(path, enable=True)
            with connect(path) as db:
                identifier = db.execute("INSERT INTO signal_trial_runs(started_at,state,message) VALUES(?,'running','Bounded enrollment and prospective price collection')", (now(),)).lastrowid
            threading.Thread(target=worker, args=(identifier,), daemon=True).start()
            return {'run_id': identifier, 'state': 'running', **enrolled}
        except Exception as exc:
            with connect(path) as db:
                db.execute('UPDATE signal_trial_settings SET enabled=0 WHERE id=1')
                if identifier:
                    db.execute("UPDATE signal_trial_runs SET state='blocked',finished_at=?,message='Worker could not start.' WHERE id=?", (now(), identifier))
            lock.release()
            if isinstance(exc, ValueError):
                raise HTTPException(422, str(exc))
            raise

    @router.post('/pause')
    def pause():
        with connect(path) as db:
            db.execute('UPDATE signal_trial_settings SET enabled=0 WHERE id=1')
        return {'enabled': False, 'message': 'Future passes paused. An active bounded pass may finish.'}

    def scheduled():
        with connect(path) as db:
            enabled = db.execute('SELECT enabled FROM signal_trial_settings WHERE id=1').fetchone()[0]
        if enabled:
            try:
                start()
            except HTTPException:
                pass
    router.scheduled_collect = scheduled
    return router
