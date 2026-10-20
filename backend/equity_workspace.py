"""Current equity evidence and explicitly simulated forward paper decisions.

No broker, orders, trader-return claims or retrospective score selection.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from copy_study import eligible
from ownership_history import HistoryRequest, parse_form4
from paper_ledger import amount, init_ledger, prepare_import, replay
from research_store import currently_fresh, encoded, immutable, instant, now
from portfolio_reconciliation import CONFIRMED_SPLITS, REVIEWED_DISTRIBUTIONS
from price_readiness import evaluate_reference
from quote_checks import export_report
from research_store import identities

POLICY = {'version': 'forward-equity-paper-v1', 'currency': 'USD',
          'fees_bps': 10, 'slippage_bps': 10, 'maximum_position_fraction': '0.20',
          'maximum_quote_age_hours': 24, 'preview_expiry_minutes': 10,
          'fill_price_decimals': 6, 'fee_decimals': 2, 'rounding': 'ROUND_HALF_UP',
          'selection': 'Documented, currently fresh common-equity inputs and positive annual FCF. Optional fresh primary purchase requirement.',
          'limitations': 'Simulated reference fills, not executable quotes or broker executions. Corporate actions, reference marks and full economic records require ongoing reconciliation. No verified account/trader return or expected-profit claim.'}

COMPARED_POLICY = {**{k:v for k,v in POLICY.items() if k != 'maximum_quote_age_hours'},
                   'version': 'forward-equity-paper-compared-reference-v2', 'maximum_quote_age_seconds': 1200,
                   'reference': 'Yahoo regularMarketPrice from the latest replayed Yahoo/CNBC comparison; both observations must remain within 20 minutes and agree under the frozen comparison policy.'}


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def init_workspace(path):
    with connect(path) as db:
        init_ledger(db)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS equity_watchlist(ticker TEXT PRIMARY KEY, enabled INTEGER NOT NULL, added_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS equity_monitor_settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL);
        INSERT OR IGNORE INTO equity_monitor_settings VALUES(1,1);
        CREATE TABLE IF NOT EXISTS equity_monitor_runs(id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          state TEXT NOT NULL, tickers_json TEXT NOT NULL, since_date TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
          imported INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS equity_decisions(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, ticker TEXT NOT NULL,
          kind TEXT NOT NULL, input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS equity_paper_previews(id TEXT PRIMARY KEY, expires_at TEXT NOT NULL,
          decision_id TEXT NOT NULL, event_json TEXT NOT NULL, preview_token TEXT NOT NULL);
        ''')
        immutable(db, 'equity_decisions')
        immutable(db, 'equity_paper_previews')
        # The shortlist evaluates hundreds of issuers in one consistent read.
        # Index the exact existing JSON predicates; no source/eligibility rule changes.
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'trade_events' in tables:
            db.execute("CREATE INDEX IF NOT EXISTS equity_purchase_lookup ON trade_events(json_extract(event_json,'$.asset'),json_extract(event_json,'$.transaction_code'),first_seen_at DESC)")
        if 'ownership_documents' in tables:
            db.execute("CREATE INDEX IF NOT EXISTS equity_amendment_lookup ON ownership_documents(json_extract(metadata_json,'$.ticker'),json_extract(metadata_json,'$.amendment'))")


def latest_items(db):
    edition = db.execute('SELECT id FROM saved_list_versions ORDER BY id DESC LIMIT 1').fetchone()
    if not edition:
        return None, {}
    return edition[0], {r[0]: json.loads(r[1]) for r in db.execute(
        'SELECT ticker,payload_json FROM saved_list_items WHERE version_id=?', (edition[0],))}


def seed_watchlist(path):
    with connect(path) as db:
        if db.execute('SELECT 1 FROM equity_watchlist LIMIT 1').fetchone():
            return
        _, items = latest_items(db)
        for ticker in sorted(t for t, item in items.items() if item['status'] == 'documented_inputs')[:25]:
            db.execute('INSERT INTO equity_watchlist VALUES(?,1,?)', (ticker, now()))


def financial_gate(item, at):
    reasons = []
    if item.get('status') != 'documented_inputs' or not currently_fresh(item, at):
        reasons.append('Required annual inputs, instrument identity or observation dates are undocumented or stale.')
    try:
        fcf = Decimal(str(item.get('fcf')))
        positive = not isinstance(item.get('fcf'), bool) and fcf.is_finite() and fcf > 0
    except Exception:
        positive = False
    if not positive:
        reasons.append('Positive documented annual free cash flow is required by this research policy.')
    return reasons


def quote_gate(item, at):
    quoted = instant(item.get('quote_as_of'))
    if (item.get('quote_currency') != 'USD' or not quoted or not item.get('quote_source')
            or not 0 <= (instant(at)-quoted).total_seconds() <= 86400):
        raise ValueError('A dated USD reference quote no older than 24 hours is required. Refresh source inputs.')
    return amount(item.get('price'))


def decision(db, ticker, at, research_context=None):
    version, items = research_context if research_context is not None else latest_items(db)
    item = items.get(ticker)
    if not item:
        raise ValueError('Ticker is not in the latest saved research edition.')
    reasons = financial_gate(item, at)
    purchases = []
    amendments = [dict(r) for r in db.execute("SELECT accession,source_url,sha256,observed_at,metadata_json FROM ownership_documents WHERE json_extract(metadata_json,'$.ticker')=? AND json_extract(metadata_json,'$.amendment')=1", (ticker,))]
    rows = db.execute("SELECT event_json FROM trade_events WHERE json_extract(event_json,'$.asset')=? AND json_extract(event_json,'$.transaction_code')='P' ORDER BY first_seen_at DESC", (ticker,))
    for row in rows:
        event = json.loads(row[0])
        blockers = eligible(event)
        amendment_evidence = []
        for amended in amendments:
            metadata = json.loads(amended['metadata_json'])
            if (instant(amended['observed_at']) <= instant(at) and metadata.get('issuer_cik') == event.get('issuer_cik')
                    and (not metadata.get('original_submission_date') or metadata['original_submission_date'] == event.get('disclosed_at','')[:10])
                    and {o['cik'] for o in metadata.get('owners',[])}.intersection(o['cik'] for o in event.get('owners',[]))):
                blockers.append('An observed amendment affects this owner/submission; original-line reconciliation is unresolved.')
                amendment_evidence.append({k: amended[k] for k in ('accession','source_url','sha256','observed_at')})
        try:
            age = (instant(at)-instant(event['disclosed_at'])).total_seconds()/86400
            if not 0 <= age <= 7 or instant(event['available_at']) > instant(at):
                blockers.append('Disclosure is stale or was not yet observed at this decision time.')
            if not 0 <= event['disclosure_lag_days'] <= 14:
                blockers.append('Reported transaction-to-disclosure lag exceeds the policy.')
            doc = db.execute('SELECT * FROM ownership_documents WHERE accession=?', (event.get('accession'),)).fetchone()
            if not doc or sha(doc['document_xml']) != doc['sha256']:
                raise ValueError('Primary XML receipt is missing or its hash differs.')
            original = parse_form4(doc['document_xml'], event['accession'], doc['source_url'], doc['filed_date'], doc['observed_at'])
            parsed = next((e for e in original['events'] if e['id'] == event['id']), None)
            if parsed is not None and 'date_status' not in event:
                # Earlier immutable parser editions predate this derived label.
                # All reported fields and classification flags must still match.
                parsed = {k:v for k,v in parsed.items() if k != 'date_status'}
            if parsed is None or encoded(parsed) != encoded(event):
                raise ValueError('Stored transaction differs from its original parsed filing.')
            source_hash = doc['sha256']
        except (ValueError, TypeError, KeyError, ParseError):
            blockers.append('Original source or availability fields require reconciliation.')
            source_hash = None
        purchases.append({'event': event, 'source_sha256': source_hash, 'amendment_evidence': amendment_evidence, 'eligible_now': not blockers,
                          'reasons': list(dict.fromkeys(blockers))})
    fresh = [p for p in purchases if p['eligible_now']]
    action_reference = db.execute('SELECT id,sha256,observed_at FROM market_history_documents WHERE ticker=? ORDER BY observed_at DESC,id DESC LIMIT 1', (ticker,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='market_history_documents'").fetchone() else None
    return {'policy': POLICY, 'observed_at': at, 'ticker': ticker, 'research_version': version,
            'action_reviews': {'splits': CONFIRMED_SPLITS, 'distributions': REVIEWED_DISTRIBUTIONS},
            'action_reference': dict(action_reference) if action_reference else None,
            'research': item, 'financial_gate_reasons': reasons, 'fresh_purchases': len(fresh),
            'distinct_fresh_buyers': len({p['event']['actor_id'] for p in fresh}),
            'purchase_evidence': purchases,
            'state': 'needs_evidence' if reasons else 'fresh_purchase_and_documented_inputs' if fresh else 'documented_inputs_no_fresh_purchase',
            'trader_profitability_verified': False, 'expected_return': None}


def save_decision(db, data, kind):
    from financial_context import financial_context
    # Freeze this supplementary context only when saving a decision. Overview
    # reads stay bounded; old decisions and the established policy stay intact.
    if isinstance(data.get('research'), dict) and data.get('research_version') is not None:
        data = {**data, 'financial_context': financial_context(db, data['research'], data['research_version'])}
    identifier = uuid.uuid4().hex
    raw = encoded(data)
    db.execute('INSERT INTO equity_decisions VALUES(?,?,?,?,?,?)',
               (identifier, data['observed_at'], data['ticker'], kind, raw, sha(raw)))
    return identifier


def ledger_rows(db):
    return [json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_ledger_events ORDER BY rowid')]


def check_actions(db, events, ticker, quote_at, at):
    """Never apply a new share-basis quote across a known unreconciled action."""
    ny = ZoneInfo('America/New_York')
    quote_day = instant(quote_at).astimezone(ny).date().isoformat()
    today = instant(at).astimezone(ny).date().isoformat()
    relevant, balance, starting_quote = [], Decimal(0), quote_day
    for event in events:
        if event.get('ticker') != ticker:
            continue
        if event['kind'] == 'buy':
            if balance == 0:
                relevant = []
                starting_quote = instant(event['quote_at']).astimezone(ny).date().isoformat()
            balance += amount(event['shares'])
        elif event['kind'] == 'sell':
            balance -= amount(event['shares'])
        elif event['kind'] == 'split':
            balance *= amount(event['new_shares'])/amount(event['old_shares'])
        relevant.append(event)
        if balance == 0:
            relevant, starting_quote = [], quote_day
    notices = [a for a in CONFIRMED_SPLITS+REVIEWED_DISTRIBUTIONS if a['ticker'] == ticker and starting_quote < a['effective_date'] <= today]
    for action in notices:
        if action['effective_date'] > quote_day:
            raise ValueError('Reference quote precedes a known corporate action; refresh source inputs.')
        if action in REVIEWED_DISTRIBUTIONS or action.get('requires_review'):
            raise ValueError('A distribution or disputed action affects the paper holding; reconcile economic records first.')
        matches = [e for e in relevant if e['kind'] == 'split'
                   and instant(e['occurred_at']).astimezone(ny).date().isoformat() == action['effective_date']
                   and amount(e.get('new_shares'))/amount(e.get('old_shares')) == Decimal(action['new_shares'])/Decimal(action['old_shares'])
                   and action['source'] in e.get('source','')]
        if len(matches) != 1:
            raise ValueError('A known split requires one matching source-referenced ledger action before a new quote is used.')
    document = db.execute("SELECT payload_json,sha256 FROM market_history_documents WHERE ticker=? ORDER BY observed_at DESC,id DESC LIMIT 1", (ticker,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='market_history_documents'").fetchone() else None
    if document:
        if sha(document['payload_json']) != document['sha256']:
            raise ValueError('Corporate-action reference archive hash differs.')
        for bar in json.loads(document['payload_json'])['bars']:
            if bar['split_ratio'] and starting_quote < bar['date'] <= quote_day and not any(a['effective_date'] == bar['date'] and not a.get('requires_review') and abs(Decimal(str(bar['split_ratio']))-Decimal(a['new_shares'])/Decimal(a['old_shares'])) < Decimal('1e-8') for a in CONFIRMED_SPLITS if a['ticker'] == ticker):
                raise ValueError('An unmatched vendor action affects the paper holding; source reconciliation is required.')


def compared_reference(db, ticker, item, report_id, at):
    """Use original source bytes, never an order's caller-supplied price."""
    row = db.execute('SELECT * FROM quote_check_reports WHERE ticker=? ORDER BY rowid DESC LIMIT 1', (ticker,)).fetchone()
    if not row or row['id'] != report_id:
        raise ValueError('The latest quote comparison is required. Capture sources and preview again.')
    try:
        report = export_report(db, row)
        current_identity = identities(db).get(ticker)
        for key in ('ticker', 'title', 'exchange', 'security_type', 'class_evidence'):
            values = [(identity or {}).get(key) for identity in (current_identity, report['report']['identity'], item.get('identity'))]
            if values[0] != values[1] or values[0] != values[2]:
                raise ValueError('Current, financial and quote instrument identities differ.')
        quote = next(q for q in report['report']['quotes'] if q['provider'] == 'yahoo')
        reference = {'ticker': ticker, 'price': quote.get('price'), 'quote_as_of': quote.get('as_of'),
                     'quote_currency': quote.get('currency'), 'quote_source': quote.get('source_url')}
        checked = evaluate_reference(reference, report, at, current_edition=True, current_identity=current_identity)
        if not checked['displayed_reference_corroborated_now']:
            raise ValueError('; '.join(checked['reasons']))
        return {'report_id': report['id'], 'report_sha256': report['report_sha256'],
                'input_sha256': report['input_sha256'], 'provider': 'yahoo',
                'source_id': quote['source_id'], 'source_sha256': quote['source_sha256'],
                'price': str(amount(quote['price'])), 'quote_at': quote['as_of'],
                'valid_until': checked['valid_until'], 'identity': current_identity,
                'upstream_independence_verified': False, 'executable_price_verified': False}
    except (KeyError, TypeError, ArithmeticError, StopIteration) as e:
        raise ValueError('Quote comparison evidence cannot be replayed. Capture sources and preview again.') from e


def check_quote_progression(events, ticker, quote_at):
    prior = [instant(e['quote_at']) for e in events if e.get('ticker') == ticker and e.get('kind') in ('buy', 'sell', 'mark')]
    if prior and instant(quote_at) < max(prior):
        raise ValueError('The reference predates an existing paper quote. Use a newer source comparison; no marks or trades recorded.')


def make_preview(path, ticker, side, shares, strategy, at=None, quote_report_id=None):
    at = at or now()
    quantity = amount(shares)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        data = decision(db, ticker, at)
        if side == 'buy' and data['financial_gate_reasons']:
            raise ValueError('; '.join(data['financial_gate_reasons']))
        if side == 'buy' and strategy == 'public_purchase' and not data['fresh_purchases']:
            raise ValueError('This strategy requires a fresh, source-validated primary purchase. None qualifies now.')
        context = compared_reference(db, ticker, data['research'], quote_report_id, at) if quote_report_id else None
        policy = COMPARED_POLICY if context else POLICY
        reference = amount(context['price']) if context else quote_gate(data['research'], at)
        quote_at = context['quote_at'] if context else data['research']['quote_as_of']
        existing_events = ledger_rows(db)
        check_quote_progression(existing_events, ticker, quote_at)
        check_actions(db, existing_events, ticker, quote_at, at)
        current = replay(existing_events, instant(at))
        if side == 'buy' and current['stale_marks']:
            raise ValueError('Existing paper marks are stale; reconcile and update them before a new purchase.')
        if quantity*reference > Decimal('1e15'):
            raise ValueError('Paper notional exceeds the supported ledger amount.')
        spread = Decimal(policy['slippage_bps'])/10000
        fill = (reference*(1+spread if side == 'buy' else 1-spread)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        fee = (quantity*fill*Decimal(policy['fees_bps'])/10000).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        identifier = uuid.uuid4().hex
        event = {'event_id': 'forward-paper-'+identifier, 'currency': 'USD', 'kind': side,
                 'ticker': ticker, 'shares': str(quantity), 'price': str(fill), 'fee': str(fee),
                 'quote_at': quote_at, 'price_basis': 'unadjusted', 'occurred_at': at,
                 'source': f'Explicit simulated {strategy} reference fill; preview {identifier}; source edition {data["research_version"]}.' + (f' Yahoo regularMarketPrice; compared quote report {context["report_id"]}.' if context else '') + ' Not a broker execution.'}
        events = [event]
        existing_shares = next((Decimal(p['shares']) for p in current['positions'] if p['ticker'] == ticker), Decimal(0))
        if side == 'buy' or existing_shares > quantity:
            events.append({'event_id': 'forward-mark-'+identifier, 'currency': 'USD', 'kind': 'mark',
                           'ticker': ticker, 'price': str(reference), 'quote_at': event['quote_at'],
                           'price_basis': 'unadjusted', 'occurred_at': at,
                           'source': f'Simulated reference mark after preview {identifier}; same dated source, not a broker valuation.'})
        _, reviewed = prepare_import(db, events)
        if side == 'buy':
            position = next(p for p in reviewed['result']['positions'] if p['ticker'] == ticker)
            if Decimal(position['value']) > Decimal(reviewed['result']['marked_value'])*Decimal(policy['maximum_position_fraction']):
                raise ValueError('Paper policy limits each position to 20% of marked portfolio value after costs.')
        data['paper_strategy'] = strategy
        data['policy'] = policy
        if context:
            data['reference_quote_context'] = context
        decision_id = save_decision(db, data, 'paper_preview')
        deadline = instant(at)+timedelta(minutes=policy['preview_expiry_minutes'])
        expires = min(deadline, instant(context['valid_until'])).isoformat() if context else deadline.isoformat()
        db.execute('INSERT INTO equity_paper_previews VALUES(?,?,?,?,?)',
                   (identifier, expires, decision_id, encoded(events), reviewed['preview_token']))
    return {'id': identifier, 'decision_id': decision_id, 'expires_at': expires, 'event': event,
            'events': events, 'reference_price': str(reference), 'reference_quote_context': context, 'policy': policy, 'result': reviewed['result'],
            'message': 'Preview only. Recording creates a simulated ledger entry; no real order is placed.'}


def commit_preview(path, identifier, at=None):
    at = at or now()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT p.*,d.input_json,d.input_sha256 FROM equity_paper_previews p JOIN equity_decisions d ON d.id=p.decision_id WHERE p.id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Paper preview does not exist.')
        events = json.loads(row['event_json'])
        event = events[0]
        prior = db.execute('SELECT digest FROM paper_ledger_events WHERE event_id=?', (event['event_id'],)).fetchone()
        if prior:
            for incoming in events:
                stored = db.execute('SELECT digest FROM paper_ledger_events WHERE event_id=?', (incoming['event_id'],)).fetchone()
                if not stored or stored[0] != sha(encoded(incoming)):
                    raise ValueError('Existing ledger entries differ from their atomic preview.')
            return {'created': False, 'event_id': event['event_id']}
        if sha(row['input_json']) != row['input_sha256'] or instant(at) > instant(row['expires_at']):
            raise ValueError('Preview expired or its source fingerprint differs. Preview again.')
        captured = json.loads(row['input_json'])
        current = decision(db, event['ticker'], at)
        context = captured.get('reference_quote_context')
        if context:
            current['policy'] = COMPARED_POLICY
            checked = compared_reference(db, event['ticker'], current['research'], context['report_id'], at)
            if encoded(checked) != encoded(context):
                raise ValueError('Reference quote evidence changed. Preview again.')
        if encoded(current['policy']) != encoded(captured['policy']) or encoded(current['action_reviews']) != encoded(captured['action_reviews']) or encoded(current['action_reference']) != encoded(captured['action_reference']):
            raise ValueError('Policy or corporate-action references changed. Preview again.')
        if (current['research_version'] != captured['research_version']
                or encoded(current['research']) != encoded(captured['research'])
                or encoded(current['purchase_evidence']) != encoded(captured['purchase_evidence'])):
            raise ValueError('Source edition or purchase evidence changed. Preview again.')
        quote_at = context['quote_at'] if context else current['research']['quote_as_of']
        if not context:
            quote_gate(current['research'], at)
        check_quote_progression(ledger_rows(db), event['ticker'], quote_at)
        check_actions(db, ledger_rows(db), event['ticker'], quote_at, at)
        if event['kind'] == 'buy' and (current['financial_gate_reasons'] or
                captured['paper_strategy'] == 'public_purchase' and not current['fresh_purchases']):
            raise ValueError('Evidence no longer meets the selected paper strategy. Preview again.')
        new, result = prepare_import(db, events)
        if result['preview_token'] != row['preview_token']:
            raise ValueError('Ledger changed since preview. Preview again.')
        for incoming, raw, digest in new:
            db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)',
                       (incoming['event_id'], incoming['occurred_at'], at, raw, digest))
    return {'created': True, 'event_id': event['event_id'], 'message': 'Simulated paper entry recorded. No real order placed.'}


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Watch(WorkspaceRequest):
    enabled: bool


class Funding(WorkspaceRequest):
    amount: str = Field(default='10000', min_length=1, max_length=30)


class PaperOrder(WorkspaceRequest):
    ticker: str = Field(pattern=r'^[A-Z0-9.\-]{1,16}$')
    side: str = Field(pattern='^(buy|sell)$')
    shares: str = Field(min_length=1, max_length=30)
    strategy: str = Field(default='fundamentals', pattern='^(fundamentals|public_purchase)$')


class ComparedPaperOrder(PaperOrder):
    quote_report_id: str = Field(pattern=r'^[a-f0-9]{64}$')


def make_workspace_router(path, start_sec, measure=None):
    router = APIRouter(prefix='/api/equity-workspace', tags=['Equity decisions and forward paper portfolio'])
    lock = threading.Lock()

    def measured(result):
        if measure is not None:
            try:
                result['measurement'] = measure()
            except (ValueError, sqlite3.Error):
                result['measurement_error'] = 'Ledger operation completed; the measurement checkpoint could not be saved. Retry checkpoint capture.'
        return result

    @router.get('')
    def overview():
        at = now()
        with connect(path) as db:
            db.execute('BEGIN')
            version, items = latest_items(db)
            watches = {r['ticker']: dict(r) for r in db.execute('SELECT * FROM equity_watchlist')}
            selected = set(watches) | {t for t, i in items.items() if i['status'] == 'documented_inputs'}
            candidates = []
            for ticker in sorted(selected):
                if ticker not in items:
                    continue
                data = decision(db, ticker, at, (version, items))
                candidates.append({k: data[k] for k in ('ticker','state','fresh_purchases','distinct_fresh_buyers','financial_gate_reasons')} | {
                    'company_name': items[ticker].get('company_name'), 'fcf_yield': items[ticker].get('fcf_yield'),
                    'quote_as_of': items[ticker].get('quote_as_of'), 'watched': bool(watches.get(ticker, {}).get('enabled'))})
            runs = [dict(r) for r in db.execute('SELECT * FROM equity_monitor_runs ORDER BY id DESC LIMIT 10')]
            enabled = bool(db.execute('SELECT enabled FROM equity_monitor_settings WHERE id=1').fetchone()[0])
            paper = replay(ledger_rows(db))
            decisions = [dict(r) for r in db.execute('SELECT id,observed_at,ticker,kind,input_sha256 FROM equity_decisions ORDER BY rowid DESC LIMIT 20')]
        candidates.sort(key=lambda i: (bool(i['financial_gate_reasons']), -i['fresh_purchases'], -(i['fcf_yield'] or 0), i['ticker']))
        return {'observed_at': at, 'research_version': version, 'saved_records': len(items),
                'documented_records': sum(i['status'] == 'documented_inputs' for i in items.values()),
                'qualified_now': sum(not i['financial_gate_reasons'] for i in candidates), 'candidates': candidates,
                'monitor_enabled': enabled, 'monitor_runs': runs, 'paper': paper, 'decisions': decisions, 'policy': POLICY,
                'monitor_policy': 'Up to 25 watched issuers, hourly at minute 10, weekdays 07:00–22:00 America/New_York. Last seven calendar days, at most 10 unparsed documents per issuer per pass; bounded coverage, not complete market surveillance.',
                'provider_access': {'quiver': bool(os.environ.get('QUIVER_API_KEY')), 'nansen': bool(os.environ.get('NANSEN_API_KEY'))}}

    @router.get('/decision/{ticker}')
    def inspect(ticker: str):
        try:
            with connect(path) as db:
                return decision(db, ticker.upper(), now())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post('/decision/{ticker}')
    def capture(ticker: str):
        try:
            with connect(path) as db:
                data = decision(db, ticker.upper(), now())
                identifier = save_decision(db, data, 'research')
                saved = db.execute('SELECT input_json,input_sha256 FROM equity_decisions WHERE id=?', (identifier,)).fetchone()
            return {'id': identifier, 'input_sha256': saved['input_sha256'], 'decision': json.loads(saved['input_json'])}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get('/decisions/{identifier}')
    def frozen(identifier: str):
        with connect(path) as db:
            row = db.execute('SELECT * FROM equity_decisions WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Decision not found')
        if sha(row['input_json']) != row['input_sha256']:
            raise HTTPException(409, 'Decision input hash differs')
        return {**dict(row), 'inputs': json.loads(row['input_json'])}

    @router.put('/watch/{ticker}')
    def watch(ticker: str, body: Watch):
        ticker = ticker.upper()
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            _, items = latest_items(db)
            if ticker not in items or items[ticker]['status'] == 'excluded' or (items[ticker].get('identity') or {}).get('security_type') != 'CS':
                raise HTTPException(422, 'Watch only supported saved equity candidates.')
            if body.enabled and db.execute('SELECT COUNT(*) FROM equity_watchlist WHERE enabled=1 AND ticker<>?', (ticker,)).fetchone()[0] >= 25:
                raise HTTPException(422, 'The focused SEC watchlist is limited to 25 issuers.')
            db.execute('INSERT INTO equity_watchlist VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET enabled=excluded.enabled', (ticker, int(body.enabled), now()))
        return {'ticker': ticker, 'enabled': body.enabled}

    @router.put('/monitor')
    def monitor_setting(body: Watch):
        with connect(path) as db:
            db.execute('UPDATE equity_monitor_settings SET enabled=? WHERE id=1', (int(body.enabled),))
        return {'enabled': body.enabled}

    def run_monitor(identifier, tickers, cutoff):
        completed = imported = 0
        state, message = 'bounded', 'Bounded current SEC pass finished; no complete coverage or profitability claim.'
        try:
            for ticker in tickers:
                run = start_sec(HistoryRequest(ticker=ticker, limit=10, since_date=cutoff))
                deadline = time.monotonic()+300
                while True:
                    with connect(path) as db:
                        source = db.execute('SELECT state,imported,message FROM ownership_history_runs WHERE id=?', (run['run_id'],)).fetchone()
                    if source['state'] != 'running':
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError('SEC import has not finished; no further issuer requests started.')
                    time.sleep(1)
                completed += 1
                imported += source['imported']
                with connect(path) as db:
                    db.execute('UPDATE equity_monitor_runs SET completed=?,imported=? WHERE id=?', (completed, imported, identifier))
                if source['state'] not in ('bounded','partial'):
                    state, message = 'blocked', source['message']
                    break
                if source['state'] == 'partial':
                    state = 'partial'
        except HTTPException as error:
            state = 'deferred' if error.status_code == 409 else 'blocked'
            message = 'Shared SEC worker became busy; remaining issuers deferred.' if state == 'deferred' else 'SEC pass stopped; source requires attention.'
        except Exception:
            state, message = 'blocked', 'SEC pass stopped after a source, worker or storage failure. Existing evidence retained; no further requests started.'
        finally:
            try:
                with connect(path) as db:
                    db.execute('UPDATE equity_monitor_runs SET state=?,finished_at=?,completed=?,imported=?,message=? WHERE id=?', (state, now(), completed, imported, message, identifier))
                    if state == 'blocked':
                        db.execute('UPDATE equity_monitor_settings SET enabled=0 WHERE id=1')
            finally:
                lock.release()

    @router.post('/monitor/run')
    def start_monitor():
        if not lock.acquire(blocking=False):
            raise HTTPException(409, 'A focused SEC pass is already running.')
        identifier = None
        try:
            with connect(path) as db:
                if db.execute("SELECT 1 FROM ownership_history_runs WHERE state='running'").fetchone():
                    raise HTTPException(409, 'An SEC import already owns the source worker.')
                tickers = [r[0] for r in db.execute('SELECT ticker FROM equity_watchlist WHERE enabled=1 ORDER BY ticker LIMIT 25')]
                if not tickers:
                    raise HTTPException(409, 'Add issuers to the focused watchlist first.')
                cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).date().isoformat()
                identifier = db.execute("INSERT INTO equity_monitor_runs(started_at,state,tickers_json,since_date,message) VALUES(?,'running',?,?,?)", (now(), encoded(tickers), cutoff, 'Sequential current SEC source checks')).lastrowid
            threading.Thread(target=run_monitor, args=(identifier,tickers,cutoff), daemon=True).start()
            return {'id': identifier, 'tickers': tickers, 'since_date': cutoff}
        except Exception:
            if identifier is not None:
                with connect(path) as db:
                    db.execute("UPDATE equity_monitor_runs SET state='blocked',finished_at=?,message=? WHERE id=?", (now(),'Source worker could not start; no requests made.',identifier))
                    db.execute('UPDATE equity_monitor_settings SET enabled=0 WHERE id=1')
            lock.release()
            raise

    def scheduled_monitor():
        with connect(path) as db:
            enabled = db.execute('SELECT enabled FROM equity_monitor_settings WHERE id=1').fetchone()[0]
        if enabled:
            try:
                start_monitor()
            except HTTPException:
                pass

    @router.post('/paper/fund')
    def fund(body: Funding):
        try:
            value = amount(body.amount)
            event = {'event_id': 'forward-paper-capital-'+uuid.uuid4().hex, 'kind': 'deposit', 'currency': 'USD',
                     'amount': str(value), 'occurred_at': now(),
                     'source': 'Explicit simulated starting capital for a new forward paper portfolio. Not a bank deposit or a reconstruction of legacy funding.'}
            with connect(path) as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM paper_ledger_events LIMIT 1').fetchone():
                    raise ValueError('The separate paper ledger already exists. Use reviewed ledger events for subsequent funding changes.')
                new, _ = prepare_import(db, [event])
                for incoming, raw, digest in new:
                    db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)', (incoming['event_id'],incoming['occurred_at'],now(),raw,digest))
            return measured({'created': True, 'simulated_capital': str(value), 'message': 'New simulated capital recorded. No money transferred.'})
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post('/paper/preview')
    def preview(body: PaperOrder):
        try:
            return make_preview(path, body.ticker, body.side, body.shares, body.strategy)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post('/paper/compared-preview')
    def compared_preview(body: ComparedPaperOrder):
        try:
            return make_preview(path, body.ticker, body.side, body.shares, body.strategy, quote_report_id=body.quote_report_id)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post('/paper/marks')
    def marks():
        at = now()
        try:
            with connect(path) as db:
                db.execute('BEGIN IMMEDIATE')
                events = ledger_rows(db)
                current = replay(events)
                if not current['positions']:
                    raise ValueError('No paper holdings need reference marks.')
                version, items = latest_items(db)
                captured = {'ticker':'PORTFOLIO','observed_at':at,'research_version':version,'policy':POLICY,'quotes':{}}
                incoming = []
                identifier = uuid.uuid4().hex
                for position in current['positions']:
                    ticker = position['ticker']
                    item = items.get(ticker)
                    if not item:
                        raise ValueError(f'{ticker} lacks a current source edition. No marks recorded.')
                    price = quote_gate(item, at)
                    check_quote_progression(events, ticker, item['quote_as_of'])
                    check_actions(db, events, ticker, item['quote_as_of'], at)
                    captured['quotes'][ticker] = item
                    incoming.append({'event_id':f'forward-refresh-{identifier}-{ticker}','kind':'mark','currency':'USD',
                                     'ticker':ticker,'price':str(price),'quote_at':item['quote_as_of'],'price_basis':'unadjusted',
                                     'occurred_at':at,'source':f'Simulated source-reference mark {identifier}, edition {version}; not a broker valuation.'})
                new, result = prepare_import(db, incoming)
                decision_id = save_decision(db, captured, 'paper_marks')
                for event, raw, digest in new:
                    db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)', (event['event_id'],at,at,raw,digest))
            return measured({'created':len(new),'decision_id':decision_id,'result':result['result']})
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post('/paper/commit/{identifier}')
    def commit(identifier: str):
        try:
            result = commit_preview(path, identifier)
            return measured(result) if result['created'] else result
        except ValueError as e:
            raise HTTPException(422, str(e))

    router.scheduled_monitor = scheduled_monitor
    router.record_marks = marks
    return router
