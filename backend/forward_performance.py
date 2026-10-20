"""Prospective, immutable paper-reference measurements. Never broker returns.

External flows require an observed boundary valuation. Cash-only boundaries are
exact; a flow while holding shares blocks the interval rather than estimating it.
"""
import hashlib
import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, HTTPException
from equity_workspace import POLICY, check_actions, connect
from paper_ledger import replay
from research_store import encoded, immutable, instant, now

VERSION = 'prospective-paper-measurement-v1'
LIMITATIONS = ('Paper reference arithmetic only. Quotes can be delayed; undiscovered corporate actions, '
               'taxes, execution quality and broker reconciliation remain unverified. '
               'Benchmark and annualized returns require separate comparable evidence.')


def fingerprint(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def init_forward(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS forward_checkpoints(
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, methodology TEXT NOT NULL,
            input_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
            input_json TEXT NOT NULL, result_json TEXT NOT NULL)''')
        immutable(db, 'forward_checkpoints')


def preview_proof(db, event):
    """Compare exact committed events with frozen decision inputs and costs."""
    identifier = event['event_id'].removeprefix('forward-paper-').removeprefix('forward-mark-')
    row = db.execute('''SELECT p.event_json,d.input_json,d.input_sha256,d.id
        FROM equity_paper_previews p JOIN equity_decisions d ON d.id=p.decision_id WHERE p.id=?''', (identifier,)).fetchone()
    if not row or fingerprint(row[1]) != row[2]:
        raise ValueError('Frozen preview/source input is absent or differs from its fingerprint.')
    events, data = json.loads(row[0]), json.loads(row[1])
    if event not in events or data.get('policy') != POLICY:
        raise ValueError('Committed event does not match the frozen paper policy/preview.')
    q = data['research']
    reference = Decimal(str(q['price']))
    if q.get('quote_currency') != 'USD' or event['quote_at'] != q.get('quote_as_of'):
        raise ValueError('Reference currency or timestamp differs.')
    if event['kind'] == 'mark':
        if Decimal(event['price']) != reference:
            raise ValueError('Reference mark differs from its frozen quote.')
    else:
        spread = Decimal(POLICY['slippage_bps']) / 10000
        fill = (reference * (1 + spread if event['kind'] == 'buy' else 1 - spread)).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
        fee = (Decimal(event['shares']) * fill * Decimal(POLICY['fees_bps']) / 10000).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if Decimal(event['price']) != fill or Decimal(event['fee']) != fee:
            raise ValueError('Simulated fill or fee differs from its frozen cost policy.')
    return {'id': row[3], 'sha256': row[2], 'input_json': row[1], 'preview_events': events}


def mark_proof(db, event):
    # Refresh event IDs include the observation timestamp through their frozen
    # decision. Compare source, price and quote date, not a user-written label.
    for row in db.execute("SELECT id,input_json,input_sha256 FROM equity_decisions WHERE kind='paper_marks' AND observed_at=?", (event['occurred_at'],)):
        if fingerprint(row[1]) != row[2]:
            continue
        data = json.loads(row[1]); q = data.get('quotes', {}).get(event['ticker'])
        if q and data.get('policy') == POLICY and q.get('quote_currency') == 'USD' and q.get('quote_as_of') == event.get('quote_at') and Decimal(str(q['price'])) == Decimal(event['price']):
            return {'id': row[0], 'sha256': row[2], 'input_json': row[1]}
    raise ValueError('Reference mark has no matching frozen USD quote.')


def capture(path, at=None):
    at = at or now()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        rows = [dict(r) for r in db.execute('SELECT * FROM paper_ledger_events ORDER BY rowid')]
        if not rows:
            raise ValueError('Fund the separate paper ledger before starting measurement.')
        events = []
        for r in rows:
            if fingerprint(r['payload']) != r['digest']:
                raise ValueError('Ledger source fingerprint differs; no checkpoint saved.')
            e = json.loads(r['payload'])
            if e.get('event_id') != r['event_id'] or e.get('occurred_at') != r['occurred_at'] or instant(r['recorded_at']) > instant(at):
                raise ValueError('Ledger identity/date differs or was recorded after the observation.')
            events.append(e)
        account = replay(events, instant(at))
        issues, proofs = [], {}
        for e in events:
            if e.get('currency') != 'USD':
                issues.append(f"{e['event_id']}: USD currency is not explicit.")
            try:
                if e['kind'] in {'buy', 'sell', 'mark'}:
                    if e['event_id'].startswith(('forward-paper-', 'forward-mark-')):
                        proof = preview_proof(db, e)
                    elif e['kind'] == 'mark' and e['event_id'].startswith('forward-refresh-'):
                        proof = mark_proof(db, e)
                    else:
                        raise ValueError('Imported/manual fill or mark is not independently source-linked.')
                    proofs[proof['id']] = proof
                elif e['kind'] in {'fee', 'dividend', 'split'}:
                    raise ValueError('Imported fee, distribution or split needs independent source reconciliation.')
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                issues.append(f"{e['event_id']}: {exc}")
        for p in account['positions']:
            if not 0 <= (instant(at)-instant(p['quote_at'])).total_seconds() <= 86400:
                issues.append(f"{p['ticker']}: a dated reference mark within 24 hours is required.")
            try:
                check_actions(db, events, p['ticker'], p['quote_at'], at)
            except ValueError as exc:
                issues.append(f"{p['ticker']}: {exc}")
        result = {'observed_at': at, 'methodology': VERSION, 'account': account,
                  'reference_status': 'blocked' if issues else 'paper_reference_arithmetic',
                  'issues': issues, 'verified_account_return': None, 'limitations': LIMITATIONS}
        inputs = {'observed_at': at, 'methodology': VERSION, 'policy': POLICY, 'ledger': rows,
                  'source_decisions': list(proofs.values()), 'limitations': LIMITATIONS}
        raw, output = encoded(inputs), encoded(result)
        previous = db.execute('SELECT id,input_json FROM forward_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
        if previous and instant(json.loads(previous[1])['observed_at']) >= instant(at):
            raise ValueError('Checkpoints must have strictly increasing observation timestamps.')
        identifier = db.execute('INSERT INTO forward_checkpoints(observed_at,methodology,input_sha256,result_sha256,input_json,result_json) VALUES(?,?,?,?,?,?)',
            (at, VERSION, fingerprint(raw), fingerprint(output), raw, output)).lastrowid
    return {'id': identifier, 'input_sha256': fingerprint(raw), 'result_sha256': fingerprint(output), **result}


def verified_checkpoint(row):
    if fingerprint(row['input_json']) != row['input_sha256'] or fingerprint(row['result_json']) != row['result_sha256']:
        raise ValueError('Checkpoint fingerprint differs.')
    inputs, result = json.loads(row['input_json']), json.loads(row['result_json'])
    if inputs['methodology'] != VERSION or result['methodology'] != VERSION or row['methodology'] != VERSION or inputs['observed_at'] != row['observed_at'] or result['observed_at'] != row['observed_at']:
        raise ValueError('Unsupported or inconsistent checkpoint methodology/date.')
    events = []
    for e in inputs['ledger']:
        if fingerprint(e['payload']) != e['digest']:
            raise ValueError('Frozen ledger fingerprint differs.')
        events.append(json.loads(e['payload']))
    for source in inputs['source_decisions']:
        if fingerprint(source['input_json']) != source['sha256']:
            raise ValueError('Frozen decision source fingerprint differs.')
    if replay(events, instant(inputs['observed_at'])) != result['account']:
        raise ValueError('Frozen checkpoint arithmetic differs on independent replay.')
    return inputs, result


def interval(previous, current):
    p, pr = previous; c, cr = current
    output = {'from': p['observed_at'], 'to': c['observed_at'], 'reference_return': None,
              'external_flow': '0', 'issues': []}
    if pr['issues'] or cr['issues']:
        output['issues'].append('One or both checkpoints lack complete reference inputs.'); return output
    if instant(c['observed_at']) <= instant(p['observed_at']):
        output['issues'].append('Checkpoint timestamps are not increasing.'); return output
    n = len(p['ledger'])
    if c['ledger'][:n] != p['ledger']:
        output['issues'].append('The later ledger is not an exact extension of the earlier ledger.'); return output
    events = [json.loads(r['payload']) for r in c['ledger']]
    additional = events[n:]
    if any(instant(e['occurred_at']) <= instant(p['observed_at']) for e in additional):
        output['issues'].append('A later-recorded event is backdated to an observed interval.'); return output
    if any(instant(pos['quote_at']) <= instant(p['observed_at']) for pos in cr['account']['positions']) or any(
            e['kind'] in {'buy','sell'} and instant(e['quote_at']) <= instant(p['observed_at']) for e in additional):
        output['issues'].append('Newer dated reference quotes are needed to measure the interval; unchanged old marks are not new price observations.'); return output
    denominator = Decimal(pr['account']['marked_value']); growth = Decimal(1)
    flow = sum((Decimal(e['amount']) * (1 if e['kind']=='deposit' else -1)
                for e in additional if e['kind'] in {'deposit','withdrawal'}), Decimal(0))
    for index, e in enumerate(additional, n):
        if e['kind'] not in {'deposit', 'withdrawal'}:
            continue
        boundary = replay(events[:index], instant(e['occurred_at']))
        value = Decimal(boundary['marked_value'])
        change = Decimal(e['amount']) * (1 if e['kind'] == 'deposit' else -1)
        if boundary['positions']:
            output['issues'].append('External flow while holding shares lacks an independently observed boundary valuation.'); break
        if denominator <= 0 or value + change <= 0:
            output['issues'].append('Zero capital prevents a continuous return series.'); break
        growth *= value / denominator
        denominator = value + change
    output['external_flow'] = str(flow)
    if denominator <= 0:
        output['issues'].append('Starting reference capital must be positive.')
    if not output['issues']:
        output['reference_return'] = str(growth * Decimal(cr['account']['marked_value']) / denominator - 1)
    return output


def overview(db):
    db.row_factory = sqlite3.Row
    # Summary is bounded; the inception chain is evaluated in full. Every input
    # is hash-checked and its Decimal cash/share arithmetic is replayed.
    rows = [dict(r) for r in db.execute('SELECT * FROM forward_checkpoints ORDER BY id')]
    checkpoints, decoded, errors = [], [], []
    for r in rows:
        try:
            inputs, result = verified_checkpoint(r)
            decoded.append((inputs, result))
            checkpoints.append({k:r[k] for k in ('id','observed_at','input_sha256','result_sha256')} | result)
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors.append({'id':r['id'], 'message': str(exc)})
    intervals = [interval(p,c) for p,c in zip(decoded,decoded[1:])] if not errors else []
    growth = Decimal(1)
    complete = bool(intervals) and not errors and all(i['reference_return'] is not None for i in intervals)
    if complete:
        for i in intervals:
            growth *= 1 + Decimal(i['reference_return'])
    has_trades = any(json.loads(e['payload'])['kind'] in {'buy','sell'} for e in decoded[-1][0]['ledger']) if decoded else False
    return {'methodology': VERSION, 'checkpoint_count': len(rows), 'checkpoints': checkpoints[-20:],
            'intervals': intervals[-20:], 'integrity_errors': errors,
            'reference_time_weighted_return': str(growth-1) if complete else None,
            'evidence_state': 'needs_observations' if len(rows)<2 else 'blocked' if not complete else 'paper_reference_only' if has_trades else 'cash_only_no_strategy_results',
            'verified_account_return': None, 'benchmark_return': None, 'annualized_return': None,
            'benchmark_status': 'No synchronized, action-reconciled benchmark series. No excess-return claim.',
            'limitations': LIMITATIONS,
            'method_source_url': 'https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-of-the-global-investment-performance-standards'}


def make_forward_router(path, refresh_marks=None):
    router = APIRouter(prefix='/api/forward-performance', tags=['Prospective paper measurements'])

    @router.get('')
    def inspect():
        with connect(path) as db:
            db.execute('BEGIN')
            return overview(db)

    @router.post('/capture')
    def checkpoint():
        try:
            return capture(path)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get('/checkpoints/{identifier}')
    def export(identifier: int):
        with connect(path) as db:
            r = db.execute('SELECT * FROM forward_checkpoints WHERE id=?', (identifier,)).fetchone()
            if not r:
                raise HTTPException(404, 'Checkpoint not found.')
            try:
                inputs, result = verified_checkpoint(dict(r))
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                raise HTTPException(409, str(exc))
            return {'id':identifier, 'input_sha256':r['input_sha256'], 'result_sha256':r['result_sha256'], 'inputs':inputs, 'result':result}

    # This observes the current account without placing, funding or marking any
    # trade. Missing/stale inputs are recorded as blocked, not silently replaced.
    def scheduled_capture():
        try:
            if refresh_marks is not None:
                with connect(path) as db:
                    held = bool(replay([json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_ledger_events ORDER BY rowid')])['positions'])
                if held:
                    try:
                        refresh_marks()  # existing workspace also saves a checkpoint
                        return
                    except HTTPException:
                        pass  # observe the stale/blocked state below
            capture(path)
        except (ValueError, sqlite3.Error):
            pass
    router.scheduled_capture = scheduled_capture
    return router
