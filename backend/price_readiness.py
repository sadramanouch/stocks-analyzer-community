"""Current, input-bound quote context; existing frozen quote reports are unchanged."""
import json
import re
import sqlite3
from datetime import datetime, timedelta, time
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from market_provenance import calendar_check
from quote_checks import NY, POLICY, decimal, digest, exchange, export_report, identity_check, instant
from research_store import identities, now

METHOD = 'displayed-reference-price-context-v1'


def capture_window(at):
    local = instant(at).astimezone(NY)
    for offset in range(11):
        day = local.date() + timedelta(days=offset)
        calendar = calendar_check(day.isoformat())
        if calendar['state'] == 'unknown_year':
            return {'available_now': False, 'next_open': None, 'calendar': calendar}
        if calendar['state'] != 'scheduled_session':
            continue
        opening = datetime.combine(day, time(9, 30), NY)
        hour = 13 if day.isoformat() in ('2026-11-27', '2026-12-24') else 16
        closing = datetime.combine(day, time(hour), NY) + timedelta(seconds=POLICY['maximum_quote_age_seconds'])
        if opening <= local <= closing:
            return {'available_now': True, 'next_open': None, 'calendar': calendar}
        if local < opening:
            return {'available_now': False, 'next_open': opening.isoformat(), 'calendar': calendar}
    return {'available_now': False, 'next_open': None, 'calendar': calendar_check(local.date().isoformat())}


def evaluate_reference(item, report, at, *, current_edition, current_identity, integrity_error=False):
    reference = {'price': None, 'as_of': item.get('quote_as_of'), 'currency': item.get('quote_currency'),
                 'source': item.get('quote_source'), 'age_seconds': None,
                 'paper_quote_requirements_met': False}
    reasons = []
    valid = True
    try:
        price = decimal(item.get('price'))
        quoted = instant(item.get('quote_as_of'))
        age = Decimal(str((instant(at) - quoted).total_seconds()))
        if age < 0 or reference['currency'] != 'USD' or not isinstance(reference['source'], str) or not reference['source'].strip():
            raise ValueError('Invalid dated USD reference')
        reference.update(price=str(price), age_seconds=str(age), paper_quote_requirements_met=age <= 86400)
    except (ValueError, TypeError, OverflowError):
        valid = False
        reasons.append('The displayed reference needs a positive finite price, aware past timestamp, USD currency and source.')
    identity_ok = identity_check(current_identity, item.get('ticker'), at)
    if not identity_ok:
        reasons.append('Current authoritative common-share identity is missing, stale or unsupported.')
    if not current_edition:
        reasons.append('This saved input is from an earlier research edition; it is not the current displayed-reference context.')

    evidence = None
    comparisons = []
    valid_until = None
    state = 'missing_comparison'
    if integrity_error:
        state = 'evidence_integrity_failed'
        reasons.append('The latest retained quote report failed source replay. No earlier report was substituted.')
    elif report:
        evidence = {k: report[k] for k in ('id', 'report_sha256')}
        r = report['report']
        evidence.update(captured_at=r['captured_at'], state_at_capture=r['comparison']['state'])
        if instant(r['captured_at']) > instant(at):
            state = 'future_comparison'
            reasons.append('The retained comparison is later than this evaluation clock.')
        elif r['comparison']['state'] != 'within_declared_tolerance':
            state = 'source_disagreement' if r['comparison']['state'] == 'price_disagreement' else 'sources_not_comparable'
            reasons.append('The latest source comparison did not report agreement within its declared policy.')
        else:
            quotes = r['quotes']
            ages = [(instant(at) - instant(q['as_of'])).total_seconds() for q in quotes]
            if any(not 0 <= age <= POLICY['maximum_quote_age_seconds'] for age in ages):
                state = 'comparison_expired'
                reasons.append('The retained source prices are older than the 20-minute corroboration window.')
            elif valid and age > POLICY['maximum_quote_age_seconds']:
                state = 'displayed_reference_expired'
                reasons.append('The displayed reference is older than the 20-minute corroboration window.')
            elif valid:
                for q in quotes:
                    source_price = decimal(q['price'])
                    difference = abs(price - source_price)
                    tolerance = max(Decimal(POLICY['absolute_price_tolerance']), min(price, source_price) * Decimal(POLICY['relative_price_tolerance']))
                    lag = abs((quoted - instant(q['as_of'])).total_seconds())
                    comparisons.append({'provider': q['provider'], 'source_price': str(source_price), 'source_as_of': q['as_of'],
                        'source_id': q['source_id'], 'source_sha256': q['source_sha256'],
                        'absolute_difference': str(difference), 'price_tolerance': str(tolerance),
                        'time_difference_seconds': str(lag),
                        'within_price_tolerance': difference <= tolerance,
                        'within_time_window': lag <= POLICY['maximum_time_difference_seconds'],
                        'same_current_exchange': q['exchange'] == exchange((current_identity or {}).get('exchange'))})
                state = 'displayed_reference_agrees' if all(c['within_price_tolerance'] and c['within_time_window'] and c['same_current_exchange'] for c in comparisons) else 'displayed_reference_not_corroborated'
                if state != 'displayed_reference_agrees':
                    reasons.append('The displayed quote differs in price, timestamp or current listing exchange from the retained source checks.')
                else:
                    valid_until = (min([quoted] + [instant(q['as_of']) for q in quotes]) + timedelta(seconds=POLICY['maximum_quote_age_seconds'])).isoformat()
    else:
        reasons.append('No retained two-source comparison exists for this ticker.')
    if not valid:
        state = 'invalid_displayed_reference'
    elif not identity_ok:
        state = 'identity_needs_review'
    elif not current_edition:
        state = 'earlier_research_edition'
    agrees = state == 'displayed_reference_agrees'
    return {'state': state, 'reference': reference, 'comparison': evidence, 'displayed_reference_comparisons': comparisons,
            'displayed_reference_corroborated_now': agrees, 'valid_until': valid_until if agrees else None, 'reasons': reasons,
            'upstream_independence_verified': False, 'executable_price_verified': False, 'profitability_verified': False,
            'capture_window': capture_window(at),
            'limits': 'A contemporaneous tolerance agreement is price corroboration only. Shared upstream feeds, delayed prices and missing executions remain possible. The paper quote check alone does not establish financial, signal, cash, position or corporate-action eligibility.'}


def inspect(db, ticker, version, expected_input_sha256, at):
    latest = db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
    row = db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?', (version, ticker)).fetchone()
    if not row:
        raise HTTPException(404, 'Saved company input not found in this research edition.')
    item = json.loads(row['payload_json'])
    if item.get('ticker') != ticker or item.get('input_sha256') != expected_input_sha256:
        raise HTTPException(409, 'Displayed company input changed; reload its decision evidence.')
    report = None
    integrity_error = False
    saved = db.execute('SELECT * FROM quote_check_reports WHERE ticker=? ORDER BY rowid DESC LIMIT 1', (ticker,)).fetchone()
    if saved:
        try:
            report = export_report(db, saved)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            integrity_error = True
    result = evaluate_reference(item, report, at, current_edition=version == latest,
                                current_identity=identities(db).get(ticker), integrity_error=integrity_error)
    return {'methodology': METHOD, 'ticker': ticker, 'research_version': version, 'latest_research_version': latest,
            'input_sha256': expected_input_sha256, 'saved_item_sha256': digest(row['payload_json']), 'observed_at': at,
            'corroboration_policy': {k: POLICY[k] for k in ('maximum_quote_age_seconds', 'maximum_time_difference_seconds', 'absolute_price_tolerance', 'relative_price_tolerance')},
            **result}


def make_price_router(path):
    router = APIRouter(prefix='/api/price-readiness', tags=['Displayed-reference price evidence'])

    @router.get('/{ticker}')
    def read(ticker: str, version: int = Query(gt=0), input_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        ticker = ticker.upper()
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}', ticker):
            raise HTTPException(422, 'Invalid symbol')
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            try:
                return inspect(db, ticker, version, input_sha256, now())
            except (ValueError, KeyError, TypeError, ArithmeticError):
                raise HTTPException(409, 'Saved price context or retained evidence needs integrity review.') from None
    return router
