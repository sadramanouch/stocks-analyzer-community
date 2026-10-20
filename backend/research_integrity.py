"""Explicit security eligibility and reproducible equity cash-flow scenarios."""
import re
from financial_integrity import finite

MODEL_VERSION = 'equity-scenario-v1'


def instrument_eligibility(name='', quote_type=None, security_type=None, sector='', industry=''):
    name = name or ''
    reasons = []
    # Titles identify known exclusions; absence of a match does not verify common stock.
    if re.search(r'\b(preferred|preference|depositary shares|warrants?|rights|units|debentures?|senior notes?|subordinated notes?|notes due|bonds?)\b|\d(?:\.\d+)?%.*\bnotes\b', name, re.I):
        reasons.append('Security title identifies a non-common instrument.')
    if security_type and security_type != 'CS':
        reasons.append(f'Reference security type is {security_type}, not CS.')
    if quote_type and quote_type != 'EQUITY':
        reasons.append(f'Quote type is {quote_type}, not EQUITY.')
    specialized = sector in ('Financial Services', 'Real Estate') or bool(re.search(r'Bank|Insurance|REIT|Mortgage|Credit|Thrift|Broker', industry or '', re.I))
    if specialized:
        reasons.append('Sector requires a specialized valuation model.')
    state = 'excluded' if reasons else 'eligible' if security_type == 'CS' else 'provisional' if quote_type == 'EQUITY' else 'unverified'
    return {'status': state, 'model_applicable': state in ('eligible', 'provisional'), 'reasons': reasons,
            'basis': 'Reference CS classification' if state == 'eligible' else 'Provider title/type screening; common-stock identity not independently verified'}


def equity_value(cash_flow, growth, discount_rate, terminal_growth=0.025, years=10, fade=True):
    values = [finite(x) for x in (cash_flow, growth, discount_rate, terminal_growth)]
    if any(x is None for x in values) or not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 50:
        return None
    cf, g, rate, terminal = values
    if cf <= 0 or g <= -1 or rate <= 0 or rate <= terminal or terminal <= -1:
        return None
    pv = 0.0
    for year in range(1, years + 1):
        cf *= 1 + (g if not fade or year <= years // 2 else g / 2)
        pv += cf / (1 + rate) ** year
    result = pv + cf * (1 + terminal) / (rate - terminal) / (1 + rate) ** years
    return finite(result)


def implied_growth(cash_flow, market_cap, discount_rate, terminal_growth=0.025):
    target = finite(market_cap)
    low, high = -0.5, 1.0
    lo_value = equity_value(cash_flow, low, discount_rate, terminal_growth, fade=False)
    hi_value = equity_value(cash_flow, high, discount_rate, terminal_growth, fade=False)
    if target is None or lo_value is None or hi_value is None or not lo_value <= target <= hi_value:
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if equity_value(cash_flow, mid, discount_rate, terminal_growth, fade=False) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def valuation_scenario(info, cash_flow, history):
    eligibility = instrument_eligibility(info.get('longName') or info.get('shortName'), info.get('quoteType'),
        info.get('securityType'), info.get('sector', ''), info.get('industry', ''))
    result = {'version': MODEL_VERSION, 'status': 'unavailable', 'instrument': eligibility,
              'price': None, 'reverse': None, 'sensitivity': [], 'limitations': [
                  'OCF minus capex is modeled with ZERO future net borrowing; actual FCFE requires debt issuance and repayments.',
                  'Cost of equity is an explicit assumption, not a live calibrated discount rate. No second subtraction of net debt.',
                  'Current provider shares and annual cash flow are not a point-in-time backtest or a verified forecast.']}
    shares = finite(info.get('sharesOutstanding'))
    price = finite(info.get('currentPrice') or info.get('regularMarketPrice'))
    cap = finite(info.get('marketCap'))
    beta = finite(info.get('beta'))
    if not eligibility['model_applicable']:
        result['reason'] = 'Instrument or sector is excluded or not identified.'
        return result
    if shares is None or shares <= 0 or price is None or price <= 0 or cap is None or cap <= 0:
        result['reason'] = 'Current shares, quote and equity market cap are required; annual shares are not substituted.'
        return result
    if abs(shares * price / cap - 1) > 0.25:
        result['reason'] = 'Current shares × price do not reconcile with market cap within 25%.'
        return result
    rates = []
    for current, prior in history:
        current, prior = finite(current), finite(prior)
        if current is not None and prior is not None and current > 0 and prior > 0:
            rates.append(current / prior - 1)
    if not rates:
        result['reason'] = 'Comparable annual growth inputs are missing.'
        return result
    growth = max(-0.05, min(0.25, sum(rates) / len(rates)))
    ke = 0.045 + max(0.5, min(2.5, beta if beta is not None else 1.0)) * 0.055
    value = equity_value(cash_flow, growth, ke)
    if value is None:
        result['reason'] = 'Positive finite OCF less capex is required.'
        return result
    result.update(status='scenario', price=round(value / shares, 2), cash_flow_basis='OCF − capex; assumed net borrowing = 0',
        discount_rate=ke, growth_rate=growth, terminal_growth=0.025, shares=shares, shares_basis='Current market-provider sharesOutstanding')
    result['sensitivity'] = [{'discount_rate': round(rate, 4), 'growth_rate': round(g, 4),
                             'price': round(equity_value(cash_flow, g, rate) / shares, 2)}
                            for rate in (ke - 0.02, ke, ke + 0.02) for g in (growth - 0.02, growth, growth + 0.02)]
    implied = implied_growth(cash_flow, cap, ke)
    if implied is not None:
        result['reverse'] = {'implied_growth_rate': round(implied * 100, 1), 'assessment': 'Conditional growth scenario', 'color': 'gray',
            'interpretation': 'Constant annual growth required under the disclosed zero-net-borrowing equity scenario; not a forecast.'}
    return result


def current_share_basis(info):
    shares = finite(info.get('sharesOutstanding'))
    cap = finite(info.get('marketCap'))
    price = finite(info.get('currentPrice') or info.get('regularMarketPrice'))
    if any(x is None or x <= 0 for x in (shares, cap, price)) or abs(shares * price / cap - 1) > .25:
        return None
    return shares
