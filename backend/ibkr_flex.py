"""Read-only, bounded Activity Flex XML review; never broker authentication.

Two native exports provide opening holdings and period activity. Uploaded bytes
are preserved, while cash/share arithmetic remains separate from virtual funds.
The initial attribute profile needs validation with the user's real exports.
"""
import base64
import hashlib
import json
import re
import sqlite3
from itertools import groupby
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from forward_performance import fingerprint
from paper_ledger import amount, instant
from research_store import encoded, immutable, now

PROFILE = 'ibkr-flex-trade-date-review-v1'
GUIDE = 'https://www.ibkrguides.com/reportingreference/reportguide/activity%20flex%20query%20reference.htm'
REQUIRED = ('CashReport', 'OpenPositions', 'Trades', 'CashTransactions', 'CorporateActions', 'Transfers')
CONTEXT = {'AccountInformation', 'SecuritiesInfo', 'FinancialInstrumentInformation', 'ConversionRates', 'Codes',
           'EquitySummaryInBase', 'MTMPerformanceSummaryInBase', 'ChangeInNAV', 'ChangeInPositionValue',
           'PriorPeriodPositions', 'CommissionDetails', 'InterestAccruals', 'OpenDividendAccruals', 'ChangeInDividendAccruals'}
LIMITATIONS = ('Uploaded native records are not authenticated with IBKR. Attribute mappings are specification-based and '
               'await real-export validation. Cash is reviewed on a trade-date basis, not settlement/buying power. '
               'Only single-account USD, unit-multiplier STK positions and executions are mapped; transfers, '
               'corporate actions, other currencies/assets and short/margin accounting require further models. '
               'Declared cash income does not prove entitlement. Overlapping bundles are never concatenated into returns.')


class FlexInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opening_base64: str = Field(min_length=1, max_length=860000)
    activity_base64: str = Field(min_length=1, max_length=860000)
    account_label: str = Field(min_length=1, max_length=80)
    export_timezone: str = Field(min_length=1, max_length=64)
    preview_token: str | None = None


def init_flex(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS broker_flex_bundles(
          id INTEGER PRIMARY KEY,recorded_at TEXT NOT NULL,profile TEXT NOT NULL,account_label TEXT NOT NULL,
          input_sha256 TEXT NOT NULL UNIQUE,result_sha256 TEXT NOT NULL,input_json TEXT NOT NULL,result_json TEXT NOT NULL)''')
        immutable(db, 'broker_flex_bundles')


def field(row, name):
    value = row.get(name)
    if value is None or not value.strip():
        raise ValueError('Required native field absent: ' + name)
    return value


def number(row, name, signed=False):
    text = field(row, name)
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        raise ValueError('Native monetary/quantity field is not a plain decimal: ' + name)
    value = Decimal(text)
    amount(abs(value), positive=False)
    if not signed and value < 0:
        raise ValueError('Negative ' + name + ' needs an explicit short/margin model.')
    return value


def native_date(text):
    if not re.fullmatch(r'\d{8}', text):
        raise ValueError('Use native YYYYMMDD dates; export formatting must be explicit.')
    return datetime.strptime(text, '%Y%m%d').date()


def event_time(text, zone):
    # Date-only cash entries keep their actual day precision. No clock is added.
    if re.fullmatch(r'\d{8}', text):
        return native_date(text), None
    if not re.fullmatch(r'\d{8};\d{6}', text):
        raise ValueError('Use native YYYYMMDD;HHMMSS Date/Time formatting.')
    naive = datetime.strptime(text, '%Y%m%d;%H%M%S')
    local = naive.replace(tzinfo=zone)
    if local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset() or local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError('Ambiguous or nonexistent local execution time requires an unambiguous export.')
    return naive.date(), local.astimezone(timezone.utc).isoformat()


def document(text):
    try:
        body = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise ValueError('Native source must be valid base64 bytes.') from None
    if not 1 <= len(body) <= 640000:
        raise ValueError('Native XML source exceeds the bounded import size.')
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False)
        root = etree.fromstring(body, parser)
    except etree.XMLSyntaxError:
        raise ValueError('Source is not well-formed native XML.') from None
    if root.getroottree().docinfo.doctype or any(not isinstance(e.tag, str) or e.tag.startswith('{') for e in root.iter()):
        raise ValueError('DTD, entity, namespaced or executable XML is unsupported.')
    nodes = list(root.iter())
    if len(nodes) > 6000 or any(len(list(e.iterancestors())) > 8 for e in nodes):
        raise ValueError('Native XML exceeds the bounded row/depth limit.')
    if root.tag != 'FlexQueryResponse' or root.get('type', 'AF') != 'AF':
        raise ValueError('Provide an Activity FlexQueryResponse, not a service response or trade-confirmation query.')
    wrappers = root.findall('FlexStatements')
    if len(wrappers) != 1 or len(wrappers[0]) != 1 or wrappers[0][0].tag != 'FlexStatement':
        raise ValueError('Each source must contain exactly one non-consolidated account statement.')
    statement = wrappers[0][0]
    account = field(statement, 'accountId')
    for row in nodes:
        if row.get('accountId') and row.get('accountId') != account:
            raise ValueError('Mixed source accounts are not combined.')
        if any(k.rsplit('}', 1)[-1].lower() in ('password', 'token', 'apikey', 'api_key', 'secret') for k in row.attrib):
            raise ValueError('Remove credentials from uploaded records.')
    sections = {}
    for section in statement:
        if section.tag in sections:
            raise ValueError('Duplicate native report section is ambiguous.')
        sections[section.tag] = section
    begin, end = native_date(field(statement, 'fromDate')), native_date(field(statement, 'toDate'))
    if begin > end or (end - begin).days > 366:
        raise ValueError('Provide a bounded statement period of at most 367 calendar dates.')
    return {'body': body, 'sha256': hashlib.sha256(body).hexdigest(), 'account': account, 'begin': begin, 'end': end, 'sections': sections}


def inspect_sources(body, at=None):
    zone = ZoneInfo(body.export_timezone)
    observed = instant(at or now())
    opening, activity = document(body.opening_base64), document(body.activity_base64)
    if len(opening['body']) + len(activity['body']) > 640000:
        raise ValueError('The two source files together must be no larger than 640 KB.')
    if opening['account'] != activity['account']:
        raise ValueError('Opening and activity records must refer to the same source account.')
    if activity['begin'] != opening['end'] + timedelta(days=1):
        raise ValueError('The activity range must start on the day after the opening export ends; gaps and overlapping periods are not inferred.')
    if activity['end'] >= observed.astimezone(zone).date():
        raise ValueError('Use completed prior-day broker exports, not unfinished or future statement days.')
    issues = []; instruments = {}; symbol_ids = {}; counts = {}; rows_review = []

    def problem(section, index, reason):
        rows_review.append({'section': section, 'row': index, 'reason': reason})

    def identify(row):
        if field(row, 'currency') != 'USD' or field(row, 'assetCategory') != 'STK' or number(row, 'multiplier') != 1:
            raise ValueError('USD, STK and explicit unit multiplier are required; no FX/derivative conversion is guessed.')
        conid, symbol = field(row, 'conid'), field(row, 'symbol')
        if not re.fullmatch(r'\d{1,18}', conid) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,23}', symbol):
            raise ValueError('A valid reported contract ID and symbol are required.')
        old = instruments.get(conid)
        identifiers = {k: row.get(k) for k in ('isin', 'cusip', 'securityID', 'securityIDType') if row.get(k)}
        if old and (old['symbol'] != symbol or any(k in old['reported_identifiers'] and old['reported_identifiers'][k] != v for k, v in identifiers.items())):
            raise ValueError('Reported contract identity changed; symbol/identifier history needs review.')
        if symbol in symbol_ids and symbol_ids[symbol] != conid:
            raise ValueError('The same symbol has different source contract IDs; no ticker-only merge is made.')
        symbol_ids[symbol] = conid
        instruments[conid] = {'conid': conid, 'symbol': symbol, 'currency': 'USD', 'asset_category': 'STK',
                             'reported_identifiers': {**(old or {}).get('reported_identifiers', {}), **identifiers}}
        return conid

    def positions(doc, label):
        section = doc['sections'].get('OpenPositions')
        if section is None:
            issues.append(label + ': OpenPositions section absent; empty holdings are not assumed.'); return None
        values = {}
        for index, row in enumerate(section, 1):
            try:
                if row.tag != 'OpenPosition' or field(row, 'levelOfDetail') != 'SUMMARY':
                    raise ValueError('Use one summary position per contract, not lots plus summaries.')
                conid = identify(row)
                if conid in values:
                    raise ValueError('Duplicate summary position.')
                if native_date(field(row, 'reportDate')) != doc['end']:
                    raise ValueError('Position snapshot date must match its export end date.')
                shares, price, value = number(row, 'position'), number(row, 'markPrice'), number(row, 'positionValue')
                if abs(shares * price - value) > Decimal('.01'):
                    raise ValueError('Position value differs from shares × reported mark by more than one cent; source precision needs review.')
                values[conid] = shares
            except (ValueError, ArithmeticError) as exc:
                problem(label + '/OpenPositions', index, str(exc))
        return values

    def cash(doc, label):
        section = doc['sections'].get('CashReport')
        if section is None:
            issues.append(label + ': CashReport section absent.'); return None
        rows = [r for r in section if r.tag == 'CashReportCurrency' and r.get('currency') == 'USD']
        other = [r for r in section if r.tag != 'CashReportCurrency' or r.get('currency') not in ('USD', 'BASE_SUMMARY')]
        if len(rows) != 1 or other:
            issues.append(label + ': one USD trade-date cash row is required; other currencies/ambiguous summaries need a separate model.'); return None
        try:
            row = rows[0]
            if native_date(field(row, 'fromDate')) != doc['begin'] or native_date(field(row, 'toDate')) != doc['end']:
                raise ValueError('Cash-report range differs from the statement.')
            return {'starting': number(row, 'startingCash'), 'ending': number(row, 'endingCash'), 'row': row}
        except (ValueError, ArithmeticError) as exc:
            issues.append(label + ': ' + str(exc)); return None

    initial_positions, final_positions = positions(opening, 'Opening'), positions(activity, 'Activity')
    initial_cash, final_cash = cash(opening, 'Opening'), cash(activity, 'Activity')
    for name in REQUIRED:
        if name not in activity['sections']:
            issues.append('Activity: ' + name + ' section absent; missing source coverage is not zero events.')
    for name, section in activity['sections'].items():
        counts[name] = len(section)
        if len(section) and (name in ('CorporateActions', 'Transfers') or name not in set(REQUIRED) | CONTEXT):
            issues.append(name + ': unmapped economic records require review before cash/share reconciliation.')
    events = []; seen = set(); commission = Decimal(0); taxes = Decimal(0); flows = Decimal(0)
    for name, tag in (('Trades', 'Trade'), ('CashTransactions', 'CashTransaction')):
        for index, row in enumerate(activity['sections'].get(name, []), 1):
            try:
                if row.tag != tag or field(row, 'currency') != 'USD':
                    raise ValueError('Unsupported row or currency in ' + name)
                day, stamp = event_time(field(row, 'dateTime'), zone)
                if not activity['begin'] <= day <= activity['end']:
                    raise ValueError('Transaction date is outside the native activity range.')
                if name == 'Trades':
                    if field(row, 'levelOfDetail') != 'EXECUTION' or stamp is None:
                        raise ValueError('Use timestamped executions; orders, lots and date-only fills are not executed trades.')
                    conid = identify(row); key = 'trade-' + field(row, 'tradeID')
                    side = field(row, 'buySell'); quantity = number(row, 'quantity', signed=True)
                    if side not in ('BUY', 'SELL') or not quantity or side == 'BUY' and quantity < 0:
                        raise ValueError('Invalid execution side/quantity.')
                    sign = 1 if side == 'BUY' else -1; quantity = abs(quantity)
                    price = number(row, 'tradePrice'); proceeds = number(row, 'proceeds', signed=True)
                    fee = number(row, 'ibCommission', signed=True); tax = number(row, 'taxes', signed=True); net = number(row, 'netCash', signed=True)
                    if field(row, 'ibCommissionCurrency') != 'USD' or abs(proceeds + sign * quantity * price) > Decimal('.01') or abs(net - proceeds - fee - tax) > Decimal('.01'):
                        raise ValueError('Execution proceeds or net cash conflict with shares, price, signed commission and tax.')
                    if row.get('origTradeID') not in (None, '', '0') or row.get('origOrderID') not in (None, '', '0') or any(code in ('Ca', 'Co') for code in row.get('notes', '').split(';')):
                        raise ValueError('Cancelled/corrected execution needs an explicit replacement chain.')
                    event = {'source_key': key, 'kind': side.lower(), 'conid': conid, 'share_change': str(sign * quantity), 'cash_change': str(net), 'day': day.isoformat(), 'occurred_at': stamp}
                else:
                    row_digest = hashlib.sha256(encoded(dict(row.attrib)).encode()).hexdigest()
                    key = 'cash-' + (row.get('transactionID') or row_digest)
                    kind = field(row, 'type'); value = number(row, 'amount', signed=True)
                    supported = {'Deposits/Withdrawals', 'Dividends', 'Payment In Lieu Of Dividends', 'Withholding Tax', 'Broker Interest Paid', 'Broker Interest Received', 'Other Fees', 'Advisor Fees'}
                    if kind not in supported:
                        raise ValueError('Cash transaction type requires an explicit mapping; descriptions are not guessed.')
                    event = {'source_key': key, 'kind': kind, 'cash_change': str(value), 'day': day.isoformat(), 'occurred_at': stamp}
                if key in seen:
                    raise ValueError('Duplicate source transaction or indistinguishable cash rows require review.')
                seen.add(key); events.append(event)
                if name == 'Trades':
                    commission += fee; taxes += tax
                elif kind == 'Deposits/Withdrawals':
                    flows += value
            except (ValueError, ArithmeticError) as exc:
                problem(name, index, str(exc))
    events.sort(key=lambda e: (e['day'], e['occurred_at'] or '', e['source_key']))
    replayed = dict(initial_positions or {}); cash_value = initial_cash['ending'] if initial_cash else None
    for _, group in groupby(events, key=lambda e: (e['day'], e['occurred_at'])):
        # Within one reported second, source order is unknown. Apply the group
        # together instead of inventing chronology from a trade ID.
        for event in group:
            if cash_value is not None: cash_value += Decimal(event['cash_change'])
            if 'conid' in event:
                key = event['conid']; replayed[key] = replayed.get(key, Decimal(0)) + Decimal(event['share_change'])
        if any(value < 0 for value in replayed.values()):
            issues.append('Executions imply a short holding or missing opening shares; further account modelling is required.')
    if initial_cash and final_cash and abs(initial_cash['ending'] - final_cash['starting']) > Decimal('.01'):
        issues.append('Opening ending cash differs from activity starting cash; an interval boundary is missing.')
    difference = cash_value - final_cash['ending'] if cash_value is not None and final_cash else None
    share_differences = {key: str(replayed.get(key, Decimal(0)) - (final_positions or {}).get(key, Decimal(0))) for key in set(replayed) | set(final_positions or {}) if replayed.get(key, Decimal(0)) != (final_positions or {}).get(key, Decimal(0))} if initial_positions is not None and final_positions is not None else None
    if difference is not None and abs(difference) > Decimal('.01'): issues.append('Closing trade-date cash does not reconcile.')
    if share_differences: issues.append('Closing shares do not reconcile; missing fills, transfers or actions need review.')
    if final_cash:
        row = final_cash['row']
        for name, value in (('commissions', commission), ('depositsWithdrawals', flows)):
            if row.get(name) and abs(number(row, name, signed=True) - value) > Decimal('.01'):
                issues.append('Cash-report ' + name + ' total differs from mapped source transactions.')
    reconciled = not issues and not rows_review and difference is not None and share_differences is not None
    return {'profile': PROFILE, 'account_label': body.account_label, 'export_timezone': body.export_timezone,
            'opening_date': opening['end'].isoformat(), 'activity_start': activity['begin'].isoformat(), 'closing_date': activity['end'].isoformat(),
            'opening_sha256': opening['sha256'], 'activity_sha256': activity['sha256'], 'section_counts': counts,
            'opening_cash': str(initial_cash['ending']) if initial_cash else None, 'activity_starting_cash': str(final_cash['starting']) if final_cash else None,
            'replayed_closing_cash': str(cash_value) if cash_value is not None else None, 'reported_closing_cash': str(final_cash['ending']) if final_cash else None,
            'cash_difference': str(difference) if difference is not None else None, 'share_differences': share_differences,
            'instruments': sorted(instruments.values(), key=lambda i: i['conid']), 'mapped_events': events,
            'commission_cash_effect': str(commission), 'execution_tax_cash_effect': str(taxes), 'net_external_funding': str(flows),
            'arithmetic_reconciled': reconciled, 'state': 'cash_and_shares_reconcile' if reconciled else 'needs_source_review',
            'row_reviews': rows_review, 'issues': list(dict.fromkeys(issues)), 'verified_account_return': None, 'statement_reference_return': None,
            'paper_ledger_changed': False, 'broker_connected': False, 'limitations': LIMITATIONS, 'field_reference_url': GUIDE}


def source_input(body):
    return encoded({'profile': PROFILE, **body.model_dump(exclude={'preview_token'})})


def preview_flex(body, at=None):
    return {'preview_token': fingerprint(source_input(body)), 'result': inspect_sources(body, at),
            'message': 'Native source preview only; no stored account, virtual funding or order created.'}


def checked_flex(row):
    if row['profile'] != PROFILE or fingerprint(row['input_json']) != row['input_sha256'] or fingerprint(row['result_json']) != row['result_sha256']:
        raise ValueError('Native broker source fingerprint/profile differs.')
    data = json.loads(row['input_json'])
    if data.pop('profile') != PROFILE: raise ValueError('Frozen native profile differs.')
    body = FlexInput.model_validate(data); result = inspect_sources(body, row['recorded_at'])
    if encoded(result) != row['result_json'] or result['account_label'] != row['account_label']:
        raise ValueError('Native cash/share replay differs from the frozen review.')
    return body, result


def make_flex_router(path):
    router = APIRouter(prefix='/api/broker-flex', tags=['Native IBKR Activity Flex review'])
    @router.get('')
    def overview():
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM broker_flex_bundles ORDER BY id DESC LIMIT 25').fetchall()
        bundles = []; errors = []
        for row in rows:
            try:
                _, result = checked_flex(row)
                bundles.append({'id': row['id'], 'recorded_at': row['recorded_at'], 'account_label': row['account_label'], 'input_sha256': row['input_sha256'], 'result': result})
            except (ValueError, KeyError, TypeError, ArithmeticError, ZoneInfoNotFoundError):
                errors.append({'id': row['id'], 'message': 'Native source hash or replay failed; values withheld.'})
        return {'profile': PROFILE, 'bundles': bundles, 'integrity_errors': errors, 'broker_connected': False, 'verified_account_return': None, 'limitations': LIMITATIONS}
    @router.post('/preview')
    def review(body: FlexInput):
        try: return preview_flex(body)
        except (ValueError, KeyError, TypeError, ArithmeticError, ZoneInfoNotFoundError):
            raise HTTPException(422, 'Native source format, period, account or timezone is invalid. Use the displayed export contract; account identifiers and values are not echoed.')
    @router.post('/commit')
    def save(body: FlexInput):
        try:
            at = now(); data = preview_flex(body, at)
            if body.preview_token != data['preview_token']: raise ValueError('Preview token differs.')
            raw = source_input(body); output = encoded(data['result'])
            with sqlite3.connect(path, timeout=30) as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT id FROM broker_flex_bundles WHERE input_sha256=?', (data['preview_token'],)).fetchone()
                if old: return {'created': False, 'id': old[0], 'paper_ledger_changed': False}
                identifier = db.execute('INSERT INTO broker_flex_bundles VALUES(NULL,?,?,?,?,?,?,?)', (at, PROFILE, body.account_label, data['preview_token'], fingerprint(output), raw, output)).lastrowid
            return {'created': True, 'id': identifier, 'paper_ledger_changed': False, 'message': 'Uploaded XML bytes and source review saved separately. No broker connection or virtual money change.'}
        except (ValueError, KeyError, TypeError, ArithmeticError, ZoneInfoNotFoundError):
            raise HTTPException(422, 'Native source or preview changed; review the current exact files and export timezone again.')
    @router.get('/bundles/{identifier}')
    def export(identifier: int):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM broker_flex_bundles WHERE id=?', (identifier,)).fetchone()
        if not row: raise HTTPException(404, 'Native source bundle not found.')
        try: body, result = checked_flex(row)
        except (ValueError, KeyError, TypeError, ArithmeticError, ZoneInfoNotFoundError): raise HTTPException(409, 'Native source hash or arithmetic replay failed.')
        return {'id': identifier, 'input_sha256': row['input_sha256'], 'result_sha256': row['result_sha256'], 'sources': body.model_dump(exclude={'preview_token'}), 'result': result}
    return router
