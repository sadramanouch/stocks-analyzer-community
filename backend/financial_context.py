"""Dated financial context from original receipts; never a backtest or forecast."""
import gzip
import hashlib
import io
import json
import re
import sqlite3
import zlib
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query
from financial_integrity import ANNUAL_FORMS
from research_store import encoded, instant

VERSION = 'primary-financial-context-v1'
MAPPING_URL = 'https://www.sec.gov/files/company_tickers.json'
CORE = {
    'revenue': ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
                'SalesRevenueNet', 'SalesRevenueGoodsNet', 'RevenueFromContractWithCustomerIncludingAssessedTax'),
    'net_income': ('NetIncomeLoss', 'ProfitLoss'),
    'operating_cf': ('NetCashProvidedByUsedInOperatingActivities',),
    'capex': ('PaymentsToAcquirePropertyPlantAndEquipment',),
}
BALANCE = {
    'cash': 'CashAndCashEquivalentsAtCarryingValue',
    'current_assets': 'AssetsCurrent', 'current_liabilities': 'LiabilitiesCurrent',
    'assets': 'Assets', 'liabilities': 'Liabilities',
    'stockholders_equity': 'StockholdersEquity',
    'equity_including_nci': 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
    'debt_current_portion': 'LongTermDebtCurrent',
    'debt_noncurrent': 'LongTermDebtNoncurrent', 'short_term_borrowings': 'ShortTermBorrowings',
}
LIMITS = [
    'Dated annual USD facts, as known in the archived response. Later restatements may change earlier years; this is not a point-in-time backtest.',
    'The cash-flow measure is operating cash flow less PP&E payments. It excludes other investments and debt service and may differ from issuer-reported free cash flow.',
    'Missing facts are unknown, not zero. Debt components are shown separately; no complete total-debt or net-debt claim is made.',
    'Statement balances are dated, not current cash or debt. This does not validate quotes, market cap, forecasts, trader skill or investment returns.',
]


def amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Invalid monetary fact')
    result = Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal('1e30'):
        raise ValueError('Nonfinite monetary fact')
    return result


def receipt(db, row, at):
    r = dict(row)
    if r['status_code'] != 200 or not instant(r['observed_at']) or instant(r['observed_at']) > instant(at):
        raise ValueError('Original source unavailable or observed after this saved edition.')
    expected = hashlib.sha256(encoded([r['cik'], r['observed_at'], r['status_code'], r['sha256']]).encode()).hexdigest()
    if expected != r['id']:
        raise ValueError('Original source metadata fingerprint differs.')
    with gzip.GzipFile(fileobj=io.BytesIO(r['body_gzip'])) as stream:
        raw = stream.read(32_000_001)
    if len(raw) > 32_000_000 or hashlib.sha256(raw).hexdigest() != r['sha256']:
        raise ValueError('Original source bytes exceed the limit or fingerprint differs.')
    return {k: r[k] for k in ('id', 'cik', 'observed_at', 'source_url', 'status_code', 'sha256')}, json.loads(raw, parse_float=Decimal)


def eligible_facts(payload, concept, duration, cutoff):
    entries = payload.get('facts', {}).get('us-gaap', {}).get(concept, {}).get('units', {}).get('USD', [])
    result = []
    for f in entries:
        try:
            end, filed = date.fromisoformat(f['end']), date.fromisoformat(f['filed'])
            if f.get('form') not in ANNUAL_FORMS or end > cutoff or not end <= filed <= cutoff:
                continue
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', f.get('accn', '')):
                continue
            if duration:
                if not 335 <= (end - date.fromisoformat(f['start'])).days + 1 <= 395:
                    continue
            elif f.get('start'):
                continue
            value = amount(f['val'])
            # PP&E payments have a payment magnitude; a negative reported value
            # needs separate semantic review rather than an automatic abs().
            if concept == CORE['capex'][0] and value < 0:
                continue
            result.append({**{k: f.get(k) for k in ('start', 'end', 'filed', 'accn', 'form')},
                           'value': str(value), 'namespace': 'us-gaap', 'concept': concept, 'unit': 'USD'})
        except (ValueError, KeyError, TypeError, InvalidOperation):
            continue
    return result


def choose(entries, end):
    values = [f for f in entries if f['end'] == end]
    if not values:
        return {'status': 'missing', 'value': None, 'reason': 'No supported annual USD fact for this exact period.'}
    latest = max(f['filed'] for f in values)
    values = [f for f in values if f['filed'] == latest]
    # A conflicting duplicate on the same filing day cannot be resolved by
    # sorting accession numbers, choosing the largest value or taking the mean.
    if len({(f['start'], amount(f['value'])) for f in values}) != 1:
        return {'status': 'conflict', 'value': None,
                'reason': 'Conflicting annual values or durations on the latest filing day.',
                'candidates': sorted(values, key=encoded)[:20]}
    selected = max(values, key=lambda f: (f['accn'], encoded(f)))
    return {'status': 'source_matched', **selected}


def coherent(facts, duration=True):
    return bool(facts) and all(f['status'] == 'source_matched' for f in facts) and len({
        tuple(f.get(k) for k in (('start', 'end', 'accn', 'filed') if duration else ('end', 'accn', 'filed')))
        for f in facts}) == 1


def metric(fields, names, calculate, duration=True):
    facts = [fields[n] for n in names]
    if not coherent(facts, duration):
        return {'value': None, 'status': 'unavailable', 'reason': 'Inputs need matching period and filing; missing or conflicting values are not combined.'}
    try:
        value = calculate(*[amount(f['value']) for f in facts])
        return {'value': str(value), 'status': 'derived_from_source', 'inputs': list(names)}
    except (ValueError, InvalidOperation, ZeroDivisionError):
        return {'value': None, 'status': 'unavailable', 'reason': 'A positive denominator is required.'}


def positive_ratio(a, b):
    if b <= 0:
        raise ValueError('Positive denominator required')
    return a / b


def finish(result):
    return {**result, 'content_sha256': hashlib.sha256(encoded(result).encode()).hexdigest()}


def financial_context(db, item, version):
    result = {'methodology': VERSION, 'ticker': item['ticker'], 'research_version': version,
              'saved_at': item.get('rebuilt_at'), 'saved_input_sha256': item.get('input_sha256'),
              'status': 'unavailable', 'sources': [], 'periods': [],
              'issues': [], 'limitations': LIMITS, 'selection_policy_unchanged': True,
              'trader_profitability_verified': False, 'expected_return': None}
    try:
        at = item['rebuilt_at']
        if not instant(at):
            raise ValueError('Saved edition timestamp is invalid.')
        original = (item.get('primary_fact_comparison') or {}).get('receipt') or {}
        row = db.execute('SELECT * FROM financial_source_documents WHERE id=?', (original.get('id'),)).fetchone()
        if not row:
            raise ValueError('No original SEC company-facts receipt attached to this saved item.')
        source, payload = receipt(db, row, at)
        if any(original.get(k) != source[k] for k in source):
            raise ValueError('Saved financial receipt metadata differs from the original.')
        if (not re.fullmatch(r'\d{10}', source['cik']) or isinstance(payload.get('cik'), bool)
                or str(payload.get('cik', '')).zfill(10) != source['cik']
                or source['source_url'] != f"https://data.sec.gov/api/xbrl/companyfacts/CIK{source['cik']}.json"):
            raise ValueError('Original company-facts issuer/URL differs.')
        rows = db.execute('SELECT id,observed_at FROM financial_source_documents WHERE source_url=?', (MAPPING_URL,)).fetchall()
        available = [r for r in rows if instant(r['observed_at']) and instant(r['observed_at']) <= instant(source['observed_at'])]
        if not available:
            raise ValueError('No original SEC ticker directory available at source observation.')
        chosen = max(available, key=lambda r: (instant(r['observed_at']), r['id']))
        mapping, directory = receipt(db, db.execute('SELECT * FROM financial_source_documents WHERE id=?', (chosen['id'],)).fetchone(), at)
        entries = [e for e in directory.values() if e.get('ticker') == item['ticker']]
        if len(entries) != 1 or str(entries[0].get('cik_str', '')).zfill(10) != source['cik']:
            raise ValueError('Exact ticker/issuer identity is unresolved in the original SEC directory.')
        result['sources'] = [source, mapping]
        cutoff = min(date.fromisoformat(at[:10]), date.fromisoformat(source['observed_at'][:10]))
        anchor = date.fromisoformat(item['financial_period_end'])
        if anchor > cutoff:
            raise ValueError('Annual period occurs after the available source.')
        fields = {}
        for name, concepts in CORE.items():
            preferred = (item.get('financial_provenance', {}).get(name) or {}).get('concept')
            if preferred not in concepts:
                preferred = next((c for c in concepts if any(f['end'] == anchor.isoformat() for f in eligible_facts(payload, c, True, cutoff))), concepts[0])
            fields[name] = eligible_facts(payload, preferred, True, cutoff)
        balances = {name: eligible_facts(payload, concept, False, cutoff) for name, concept in BALANCE.items()}
        for offset in range(3):
            target = anchor - timedelta(days=365*offset)
            ends = {f['end'] for fs in fields.values() for f in fs if abs((date.fromisoformat(f['end'])-target).days) <= (30 if offset else 0)}
            end = anchor.isoformat() if not offset else next(iter(ends)) if len(ends) == 1 else None
            ambiguous = offset > 0 and len(ends) > 1
            facts = {name: choose(entries, end) if not ambiguous else {'status':'conflict','value':None,'reason':'Multiple annual end dates near the expected year; no period is substituted.'} for name, entries in fields.items()}
            balance = {name: choose(entries, end) if not ambiguous else
                       {'status':'conflict','value':None,'reason':'Annual period is ambiguous.'} for name, entries in balances.items()}
            for name, fact in balance.items():
                if name not in ('stockholders_equity','equity_including_nci') and fact['value'] is not None and amount(fact['value']) < 0:
                    balance[name] = {**fact, 'status':'requires_review', 'reason':'Negative balance in a normally nonnegative concept; excluded from ratios.'}
            row = {'end': end if not ambiguous else None, 'expected_year_offset': offset,
                   'annual_fields': facts, 'balance_fields': balance,
                   'annual_inputs_coherent': coherent(list(facts.values())),
                   'metrics': {
                       'ocf_less_ppe': metric(facts, ('operating_cf','capex'), lambda ocf, capex: ocf-capex),
                       'net_margin': metric(facts, ('net_income','revenue'), positive_ratio),
                       'ocf_margin': metric(facts, ('operating_cf','revenue'), positive_ratio),
                       'current_ratio': metric(balance, ('current_assets','current_liabilities'), positive_ratio, False),
                       'liabilities_to_assets': metric(balance, ('liabilities','assets'), positive_ratio, False),
                       'balance_equation_difference': metric(balance, ('assets','liabilities','equity_including_nci'), lambda a,l,e:a-l-e, False),
                   }}
            if not offset:
                differences = [name for name,f in facts.items() if f['status']=='source_matched' and item.get('financials',{}).get(name) is not None
                               and amount(f['value']) != (abs(amount(item['financials'][name])) if name=='capex' else amount(item['financials'][name]))]
                result['baseline_differences'] = differences
                if differences:
                    result['issues'].append('Current original-source values differ from saved annual inputs: '+', '.join(differences)+'.')
            result['periods'].append(row)
        periods = result['periods']
        consecutive = all(periods[n]['end'] and periods[n+1]['end'] and coherent(list(periods[n]['annual_fields'].values()))
                          and periods[n]['annual_fields']['revenue']['start'] == (date.fromisoformat(periods[n+1]['end'])+timedelta(days=1)).isoformat()
                          for n in (0,1)) and periods[2]['annual_inputs_coherent']
        complete = consecutive and all(p['metrics']['ocf_less_ppe']['value'] is not None for p in periods)
        result['three_year_summary'] = {'status':'complete_comparable_periods' if complete else 'incomplete_or_incomparable',
              'ocf_less_ppe_sum': str(sum((amount(p['metrics']['ocf_less_ppe']['value']) for p in periods), Decimal(0))) if complete else None,
              'positive_cashflow_periods':sum(amount(p['metrics']['ocf_less_ppe']['value'])>0 for p in periods) if complete else None}
        fact_count = sum(f['status']=='source_matched' for p in periods for f in list(p['annual_fields'].values())+list(p['balance_fields'].values()))
        result['coverage'] = {'source_matched_facts':fact_count,
                              'derived_metrics':sum(m['value'] is not None for p in periods for m in p['metrics'].values()),
                              'coherent_annual_periods':sum(p['annual_inputs_coherent'] for p in periods),
                              'anchor_age_days_at_saved_edition':(date.fromisoformat(at[:10])-anchor).days}
        result['status'] = 'source_context_available' if fact_count else 'no_supported_facts'
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError, OSError, EOFError, zlib.error, sqlite3.OperationalError) as exc:
        result['status'] = 'unavailable'
        result['periods'] = []
        result['issues'].append(str(exc))
    return finish(result)


def make_context_router(path):
    router = APIRouter(prefix='/api/financial-context', tags=['Primary financial context'])

    @router.get('/{ticker}')
    def inspect(ticker: str, version: int | None = Query(None, ge=1)):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            v = version or db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
            row = db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?', (v, ticker.upper())).fetchone()
            if not row:
                raise HTTPException(404, 'Ticker or saved research edition not found.')
            return financial_context(db, json.loads(row['payload_json']), v)
    return router
