"""Append-only, cash-and-share paper ledger. No broker or order connectivity."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

UTC = timezone.utc
KINDS = {'deposit', 'withdrawal', 'buy', 'sell', 'split', 'dividend', 'fee', 'mark'}


def amount(value, positive=True):
    try:
        if isinstance(value, bool):
            raise ValueError('Boolean is not an amount')
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal('1e15') or (number != 0 and number < Decimal('1e-18')) or (positive and number == 0):
            raise ValueError('Amount must be finite, nonnegative and between 1e-18 and 1e15 when nonzero')
        return number
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid amount') from None


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except (ValueError, AttributeError):
        raise ValueError('Use a timestamp with a timezone') from None


def init_ledger(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS paper_ledger_events (
        event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
        payload TEXT NOT NULL, digest TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS paper_ledger_no_update BEFORE UPDATE ON paper_ledger_events
        BEGIN SELECT RAISE(ABORT, 'Paper ledger events are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS paper_ledger_no_delete BEFORE DELETE ON paper_ledger_events
        BEGIN SELECT RAISE(ABORT, 'Paper ledger events are immutable'); END;''')


def replay(events, as_of=None):
    now = as_of or datetime.now(UTC)
    cash = contributions = Decimal(0)
    holdings, marks, quote_times = {}, {}, {}
    last = None
    for event in events:
        at = instant(event['occurred_at'])
        if at > now or (last is not None and at < last):
            raise ValueError('Events must be chronological and cannot be future-dated')
        last = at
        kind, ticker = event.get('kind'), event.get('ticker')
        if kind not in KINDS:
            raise ValueError('Unsupported ledger event')
        if not isinstance(event.get('source'), str) or not event['source'].strip():
            raise ValueError('Every event needs a source or explicit manual reference')
        if kind in {'buy', 'sell', 'split', 'dividend', 'mark'}:
            if not isinstance(ticker, str) or not ticker or len(ticker) > 24 or ticker != ticker.upper():
                raise ValueError('Use a valid uppercase instrument identifier')
        if kind in {'deposit', 'withdrawal', 'fee', 'dividend'}:
            value = amount(event.get('amount'))
            if kind in {'withdrawal', 'fee'}:
                value = -value
            cash += value
            if kind in {'deposit', 'withdrawal'}:
                contributions += value
            if kind == 'dividend':
                # Cash is credited from documented entitlement, not payment-date holdings.
                amount(event.get('entitled_shares'))
                per_share = amount(event.get('cash_per_share'))
                if amount(event['entitled_shares']) * per_share != value:
                    raise ValueError('Dividend amount must match documented entitlement')
                if instant(event.get('entitlement_at')) > at:
                    raise ValueError('Dividend entitlement cannot follow payment')
        elif kind in {'buy', 'sell', 'mark'}:
            if event.get('price_basis') != 'unadjusted':
                raise ValueError('Ledger requires unadjusted prices; never combine split-adjusted quotes with split events')
            price = amount(event.get('price'))
            quoted = instant(event.get('quote_at'))
            if quoted > at or (at - quoted).total_seconds() > 86400:
                raise ValueError('Quote must precede the event by at most 24 hours')
            if kind != 'mark':
                quantity = amount(event.get('shares'))
                fee = amount(event.get('fee', '0'), positive=False)
                sign = 1 if kind == 'buy' else -1
                holdings[ticker] = holdings.get(ticker, Decimal(0)) + sign * quantity
                cash -= sign * quantity * price + fee
            elif holdings.get(ticker, Decimal(0)) <= 0:
                raise ValueError('Cannot mark an instrument with no holdings')
            marks[ticker], quote_times[ticker] = price, quoted
        elif kind == 'split':
            ratio = amount(event.get('new_shares')) / amount(event.get('old_shares'))
            if holdings.get(ticker, Decimal(0)) <= 0:
                raise ValueError('Cannot split an instrument with no holdings')
            holdings[ticker] *= ratio
            if ticker in marks:
                marks[ticker] /= ratio
        if cash < 0 or any(shares < 0 for shares in holdings.values()):
            raise ValueError('Insufficient cash or shares; borrowing and shorting are disabled')
    positions = [{'ticker': ticker, 'shares': str(shares), 'mark': str(marks[ticker]),
                  'value': str(shares * marks[ticker]), 'quote_at': quote_times[ticker].isoformat(),
                  'stale': (now - quote_times[ticker]).total_seconds() > 172800}
                 for ticker, shares in sorted(holdings.items()) if shares > 0]
    nav = cash + sum((Decimal(p['value']) for p in positions), Decimal(0))
    return {'cash': str(cash), 'net_contributions': str(contributions), 'marked_value': str(nav),
            'pnl': str(nav - contributions), 'positions': positions, 'event_count': len(events),
            'stale_marks': sum(p['stale'] for p in positions), 'performance_status': 'paper_reference_prices',
            'basis': 'Cash + shares × unadjusted reference marks, including recorded costs and dividends. Manual source assertions are not independently verified; no CAGR or benchmark claim.'}


def read_ledger(db_path):
    with sqlite3.connect(db_path) as db:
        init_ledger(db)
        rows = db.execute('SELECT payload FROM paper_ledger_events ORDER BY rowid').fetchall()
    events = [json.loads(row[0]) for row in rows]
    return {**replay(events), 'events': events[-100:]}


def append_event(db_path, event):
    if not isinstance(event, dict) or not isinstance(event.get('event_id'), str) or not 1 <= len(event['event_id']) <= 128:
        raise ValueError('Provide a unique event_id of 1–128 characters')
    payload = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(payload) > 16000:
        raise ValueError('Event too large')
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with sqlite3.connect(db_path, timeout=15) as db:
        init_ledger(db)
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT digest FROM paper_ledger_events WHERE event_id=?', (event['event_id'],)).fetchone()
        if previous:
            if previous[0] != digest:
                raise ValueError('Event ID already exists with different content')
            return {'created': False, 'event_id': event['event_id']}
        events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM paper_ledger_events ORDER BY rowid')]
        replay(events + [event])
        db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)',
                   (event['event_id'], event['occurred_at'], datetime.now(UTC).isoformat(), payload, digest))
    return {'created': True, 'event_id': event['event_id']}


def prepare_import(db, incoming):
    """Validate the whole ordered batch before any ledger mutation."""
    if not isinstance(incoming,list) or not 1<=len(incoming)<=500:
        raise ValueError('Import 1–500 ordered ledger events')
    existing=db.execute('SELECT event_id,payload,digest FROM paper_ledger_events ORDER BY rowid').fetchall()
    prior={r[0]:r[2] for r in existing};seen=set();new=[];duplicates=0
    for event in incoming:
        if not isinstance(event,dict) or not isinstance(event.get('event_id'),str) or not 1<=len(event['event_id'])<=128:
            raise ValueError('Each import event requires an event_id of 1–128 characters')
        if event.get('currency')!='USD':
            raise ValueError('Each imported event must explicitly use USD; foreign-currency conversion needs separate evidence')
        fields={'event_id','kind','occurred_at','source','currency','ticker','amount','shares','price','fee','quote_at','price_basis',
                'new_shares','old_shares','entitled_shares','cash_per_share','entitlement_at'}
        if set(event)-fields:raise ValueError('Import contains unsupported fields; use normalized ledger fields only')
        identifier=event['event_id']
        if identifier in seen:raise ValueError('Duplicate event_id within the import batch')
        seen.add(identifier)
        payload=json.dumps(event,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(payload)>16000:raise ValueError('Event too large')
        digest=hashlib.sha256(payload.encode()).hexdigest()
        if identifier in prior:
            if prior[identifier]!=digest:raise ValueError('Existing event_id has different content')
            duplicates+=1;continue
        new.append((event,payload,digest))
    result=replay([json.loads(r[1]) for r in existing]+[r[0] for r in new])
    token=hashlib.sha256(json.dumps({'prior':[(r[0],r[2]) for r in existing],'incoming':incoming},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return new,{'preview_token':token,'new_events':len(new),'identical_existing_events':duplicates,
                'result':result,'source_status':'Imported/manual references are assertions; not independently broker-verified.'}


def preview_import(db_path,events):
    with sqlite3.connect(db_path,timeout=15) as db:
        init_ledger(db)
        return prepare_import(db,events)[1]


def commit_import(db_path,events,preview_token):
    with sqlite3.connect(db_path,timeout=15) as db:
        init_ledger(db);db.execute('BEGIN IMMEDIATE')
        new,result=prepare_import(db,events)
        if not isinstance(preview_token,str) or result['preview_token']!=preview_token:
            raise ValueError('Ledger or import content changed; preview the current batch again')
        stamp=datetime.now(UTC).isoformat()
        for event,payload,digest in new:
            db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)',(event['event_id'],event['occurred_at'],stamp,payload,digest))
    return {'created':len(new),'identical_existing_events':result['identical_existing_events']}
