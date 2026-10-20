"""Reproducible checks of retained numbers, with explicit evidence boundaries.

Arithmetic agreement never upgrades a provider quote or a scenario to a verified
financial fact or forecast. Captures are isolated from the trading/account ledger.
"""
import hashlib
import json
import math
import sqlite3
from collections import Counter
from decimal import Decimal, localcontext
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from financial_context import financial_context
from research_store import encoded, immutable, now

VERSION = 'retained-number-evidence-v1'
DEBT_RATIO_NORMALIZATION_SCHEMA = 5
FIELDS = ('ticker','company_name','_fw_version','market_data_fetched_at','financial_period_end','financial_currency',
          'financial_data_source','financial_provenance','cik','quote_as_of','quote_currency','quote_source',
          'price','market_cap','shares_outstanding','revenue','net_income','operating_cf','capex','fcf','fcf_yield',
          'cash','total_debt','net_debt','total_assets','total_equity','de_ratio','dupont','dcf_price',
          'valuation_scenario','reverse_dcf','instrument_eligibility')
PROVIDER = {
    'pe_ttm':'Trailing P/E','pe_fwd':'Forward P/E','pb':'Price/book','ps':'Price/sales','peg':'Provider PEG',
    'ev_ebitda':'EV/EBITDA','ev_revenue':'EV/revenue','dividend_yield':'Dividend yield','payout_ratio':'Payout ratio',
    'gross_margin':'Gross margin','op_margin':'Operating margin','net_margin':'Net margin','roe':'Provider ROE',
    'roa':'Provider ROA','rev_growth':'Revenue growth','eps_growth':'Earnings growth','eps':'Trailing EPS',
    'current_ratio':'Provider current ratio','quick_ratio':'Quick ratio','bvps':'Book value/share',
    'target_price':'Analyst target price','analyst_count':'Analyst count','analyst_rec':'Analyst recommendation',
    'enterprise_value':'Enterprise value','beta':'Beta','high_52w':'52-week high','low_52w':'52-week low',
    'avg_volume':'Average volume','pct_52w':'52-week price change','short_pct_float':'Short float percentage',
}
MODELS = ('avg_fair_value','margin_of_safety','graham_number','altman_z','piotroski_f','sloan','red_flags',
          'beneish_m','magic_formula','epv','roic_wacc','peg_ratio','net_net','owner_earnings','rule_of_40','revenue_trend','ni_trend')
LIMITS = [
    'These checks cover the listed retained inputs and formulas only. Arithmetic agreement does not establish complete records, aligned accounting periods, instrument suitability or accurate source inputs.',
    'Provider ratios can use trailing, quarterly, average-balance, share-class or adjusted definitions. Annual SEC references are separate; a difference alone is not proof that a provider ratio is wrong.',
    'Quotes, current shares and market capitalization remain provider assertions. Retrieval time is not quote execution time, and internal reconciliation is not independent price corroboration.',
    'DCF and reverse DCF are conditional arithmetic with assumed growth, discount rates and zero future net borrowing. Their assumptions, future prices, analyst targets and investment returns are unverified.',
    'Legacy analyses and source records are retained. This report creates no order, fill, paper mark or account return.',
]


def sha(raw): return hashlib.sha256(raw.encode()).hexdigest()


def clean(value):
    if isinstance(value, float) and not math.isfinite(value): return {'invalid_number':str(value)}
    if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
    if isinstance(value, list): return [clean(v) for v in value]
    return value


def public_values(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:public_values(v) for k,v in value.items()}
    if isinstance(value,list):return [public_values(v) for v in value]
    return value


def project(data):
    result = {key:clean(data.get(key)) for key in FIELDS+tuple(PROVIDER)+MODELS}
    if len(encoded(result)) > 1_000_000: raise ValueError('Retained numeric input exceeds the bounded report size.')
    return result


def number(value):
    if value is None or isinstance(value, (bool,dict,list)): raise ValueError('A finite reported number is missing.')
    try: result=Decimal(str(value))
    except Exception: raise ValueError('A numeric input is invalid.') from None
    if not result.is_finite() or abs(result)>Decimal('1e30'): raise ValueError('A numeric input is nonfinite or outside supported bounds.')
    return result


def timestamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None: return None
        return result.astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError): return None


def epsilon(value, decimals=4):
    # Retained app inputs are rounded to four decimal places. Large floats also
    # have a binary representation interval; do not use an arbitrary % tolerance.
    result=Decimal(5).scaleb(-decimals-1)
    if isinstance(value,float): result+=Decimal(str(math.ulp(value)))/2
    return result


class Interval:
    def __init__(self, value, error=Decimal(0)):
        self.low,self.high=value-error,value+error
    @classmethod
    def bounds(cls, low, high):
        result=cls(Decimal(0));result.low,result.high=low,high;return result
    def __sub__(self,other): return self.bounds(self.low-other.high,self.high-other.low)
    def __mul__(self,other):
        values=[a*b for a in (self.low,self.high) for b in (other.low,other.high)]
        return self.bounds(min(values),max(values))
    def __truediv__(self,other):
        if other.low<=0<=other.high: raise ValueError('Rounded denominator includes zero.')
        return self*self.bounds(1/other.high,1/other.low)
    def absolute(self):
        return self.bounds(0 if self.low<=0<=self.high else min(abs(self.low),abs(self.high)),max(abs(self.low),abs(self.high)))


def operand(inputs,key,positive=False,nonnegative=False):
    raw=inputs.get(key);value=number(raw)
    if positive and value<=0 or nonnegative and value<0: raise ValueError('A required positive/nonnegative input is invalid.')
    return Interval(value,epsilon(raw))


def value_at(inputs,path):
    value=inputs
    for key in path.split('.'):
        if not isinstance(value,dict): return None
        value=value.get(key)
    return value


def check(inputs,path,label,formula,paths,calculate,decimals=4):
    raw=value_at(inputs,path)
    result={'field':path,'label':label,'reported':str(number(raw)) if numeric(raw) else None,
            'formula':formula,'input_fields':paths,'state':'not_reproducible','expected_interval':None,
            'independently_source_verified':False}
    try:
        interval=calculate();reported=number(raw);tolerance=epsilon(raw,decimals)
        result['expected_interval']=[str(interval.low),str(interval.high)]
        result['state']='arithmetic_consistent' if interval.low-tolerance<=reported<=interval.high+tolerance else 'arithmetic_difference'
        result['reason']='Comparison includes retained decimal rounding and float representation intervals; source values and period alignment remain unverified.'
    except (ValueError,ArithmeticError) as exc: result['reason']=str(exc)
    return result


def numeric(value):
    try: number(value);return True
    except ValueError:return False


def dupont_supported(inputs):
    return (all(numeric(inputs.get(k)) for k in ('net_income','revenue','total_assets','total_equity'))
            and all(number(inputs[k])>0 for k in ('revenue','total_assets','total_equity')))


def scenario_value(cf,growth,rate,terminal,fade):
    if cf<=0 or growth<=-1 or not rate>terminal or terminal<=-1: raise ValueError('Unsupported positive-cash-flow scenario assumptions.')
    result=Decimal(0)
    for year in range(1,11):
        cf*=1+(growth if not fade or year<=5 else growth/2)
        result+=cf/(1+rate)**year
    return result+cf*(1+terminal)/(rate-terminal)/(1+rate)**10


def scenario_checks(inputs):
    result={'state':'not_reproducible','future_forecast_verified':False,'rows':[],
            'limitations':'Only declared scenario arithmetic is checked. Growth derivation, beta, discount calibration, current shares and zero future net borrowing are not independently established.'}
    try:
        s=inputs.get('valuation_scenario') or {}
        if not isinstance(s,dict) or s.get('version')!='equity-scenario-v1' or s.get('status')!='scenario' or not isinstance(s.get('instrument'),dict) or not s['instrument'].get('model_applicable'):
            raise ValueError('A supported, applicable, declared equity-scenario-v1 snapshot is required.')
        cf=number(inputs['fcf']);growth=number(s['growth_rate']);rate=number(s['discount_rate']);terminal=number(s['terminal_growth']);shares=number(s['shares'])
        if terminal!=Decimal('.025') or not Decimal('-.05')<=growth<=Decimal('.25') or not Decimal('.025')<rate<=1 or shares<=0:
            raise ValueError('Declared assumptions are outside the supported version-one bounds.')
        expected=scenario_value(cf,growth,rate,terminal,True)/shares
        def row(field,reported,expected,precision):
            valid=numeric(reported);value=number(reported) if valid else None
            tolerance=Decimal(precision)+(epsilon(reported,8) if valid else 0)
            return {'field':field,'reported':str(value) if valid else None,'recomputed':str(expected),
                    'absolute_tolerance':str(tolerance),'state':'not_reproducible' if not valid else 'scenario_arithmetic_consistent' if abs(value-expected)<=tolerance else 'scenario_arithmetic_difference'}
        result['rows']=[row('dcf_price',inputs.get('dcf_price'),expected,'.01'),row('valuation_scenario.price',s.get('price'),expected,'.01')]
        sensitivity=s.get('sensitivity',[])
        if len(sensitivity)!=9: raise ValueError('All nine declared sensitivity points are required.')
        for index,(dr,dg) in enumerate((dr,dg) for dr in (Decimal('-.02'),Decimal(0),Decimal('.02')) for dg in (Decimal('-.02'),Decimal(0),Decimal('.02'))):
            point=sensitivity[index]
            if abs(number(point['discount_rate'])-(rate+dr))>Decimal('.0000500001') or abs(number(point['growth_rate'])-(growth+dg))>Decimal('.0000500001'):
                raise ValueError('Sensitivity coordinates differ from the declared policy.')
            result['rows'].append(row('sensitivity.'+str(index),point.get('price'),scenario_value(cf,growth+dg,rate+dr,terminal,True)/shares,'.01'))
        reverse_data=inputs.get('reverse_dcf') or {}
        if not isinstance(reverse_data,dict):raise ValueError('Retained reverse-scenario schema is invalid.')
        reverse=reverse_data.get('implied_growth_rate')
        cap=number(inputs.get('market_cap'));low,high=Decimal('-.5'),Decimal(1)
        if scenario_value(cf,low,rate,terminal,False)<=cap<=scenario_value(cf,high,rate,terminal,False):
            for _ in range(100):
                mid=(low+high)/2
                if scenario_value(cf,mid,rate,terminal,False)<cap: low=mid
                else: high=mid
            result['rows'].append(row('reverse_dcf.implied_growth_rate',reverse,(low+high)*50,'.05000001'))
        elif reverse is not None: raise ValueError('Retained reverse scenario lies outside the supported search bracket.')
        result.update(state='scenario_arithmetic_difference' if any(r['state']=='scenario_arithmetic_difference' for r in result['rows']) else 'scenario_arithmetic_consistent' if all(r['state']=='scenario_arithmetic_consistent' for r in result['rows']) else 'not_reproducible',
                      assumptions={'years':10,'growth_fades_after_year':5,'later_growth_multiplier':'0.5','growth':str(growth),'cost_of_equity':str(rate),'terminal_growth':str(terminal),'shares':str(shares),'annual_cash_flow':str(cf),'future_net_borrowing':'0','reverse_growth_constant':True})
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        result['state']='not_reproducible';result['reason']=str(exc)
    return result


def build(inputs,context,version,at):
    with localcontext() as precision:
        precision.prec=50
        v=lambda key,**kw:operand(inputs,key,**kw)
        hundred=Interval(Decimal(100))
        checks=[
            check(inputs,'fcf','Operating cash flow less PP&E payments','operating_cf − abs(capex)',['operating_cf','capex'],lambda:v('operating_cf')-v('capex').absolute()),
            check(inputs,'fcf_yield','FCF / provider equity market cap','fcf / market_cap',['fcf','market_cap'],lambda:v('fcf')/v('market_cap',positive=True)),
            check(inputs,'net_debt','Reported debt less cash','total_debt − cash',['total_debt','cash'],lambda:v('total_debt',nonnegative=True)-v('cash',nonnegative=True)),
            check(inputs,'de_ratio','Reported interest-bearing debt / equity','total_debt / total_equity',['total_debt','total_equity'],lambda:v('total_debt',nonnegative=True)/v('total_equity',positive=True)),
            check(inputs,'dupont.net_margin','DuPont annual net margin · %','100 × net_income / revenue',['net_income','revenue'],lambda:hundred*v('net_income')/v('revenue',positive=True),2),
            check(inputs,'dupont.asset_turnover','DuPont turnover · ending assets','revenue / total_assets',['revenue','total_assets'],lambda:v('revenue',positive=True)/v('total_assets',positive=True),3),
            check(inputs,'dupont.equity_multiplier','DuPont ending balance leverage','total_assets / total_equity',['total_assets','total_equity'],lambda:v('total_assets',positive=True)/v('total_equity',positive=True),3),
            check(inputs,'dupont.roe','DuPont return on ending equity · %','100 × net_income / total_equity',['net_income','total_equity'],lambda:hundred*v('net_income')/v('total_equity',positive=True),2),
        ]
        if inputs.get('_fw_version')!=DEBT_RATIO_NORMALIZATION_SCHEMA and numeric(inputs.get('de_ratio')):
            checks[3].update(state='definition_unverified',reason='This legacy schema does not establish percentage versus multiple units or the provider balance basis. The retained debt/equity interval is shown for review; no automatic rescaling or unit assumption is made.')
        suppress=[]
        # All four DuPont values are a single model. Missing NI must not become
        # a zero margin/ROE, even when another component alone can be calculated.
        if inputs.get('dupont') is not None and not dupont_supported(inputs):
            suppress.append({'field':'dupont','reason':'A retained DuPont model has missing/nonfinite inputs or a nonpositive revenue/assets/equity denominator. It is suppressed in the current view; the original cache is retained.'})
        source_links=[]
        if context and context.get('status')=='source_context_available' and context.get('periods'):
            period=context['periods'][0]
            issuer=str(inputs.get('cik','')).zfill(10)==str(context['sources'][0]['cik']).zfill(10)
            for key in ('revenue','net_income','operating_cf','capex'):
                fact=period['annual_fields'][key];provenance=inputs.get('financial_provenance')
                prov=provenance.get(key) if isinstance(provenance,dict) else None
                prov=prov if isinstance(prov,dict) else {}
                match=(issuer and inputs.get('financial_currency')=='USD' and inputs.get('financial_period_end')==period['end']
                       and fact['status']=='source_matched' and numeric(inputs.get(key)) and numeric(fact['value'])
                       and (abs(number(inputs[key])) if key=='capex' else number(inputs[key]))==number(fact['value'])
                       and all(prov.get(k)==fact.get(k) for k in ('accn','end','form','unit','concept','filed')))
                source_links.append({'field':key,'state':'matched_to_original_annual_fact' if match else 'not_matched_to_this_source_period','reported':str(number(inputs[key])) if numeric(inputs.get(key)) else None,'reference':fact})
        quoted=timestamp(inputs.get('quote_as_of'));observed=timestamp(at)
        age=Decimal(str((observed-quoted).total_seconds()))/3600 if quoted and observed else None
        quote={'state':'provider_dated_unverified' if age is not None and age>=0 and inputs.get('quote_currency')=='USD' and isinstance(inputs.get('quote_source'),str) and inputs['quote_source'].strip() and numeric(inputs.get('price')) and number(inputs['price'])>0 else 'missing_or_invalid_provider_quote',
               'as_of':inputs.get('quote_as_of'),'source':inputs.get('quote_source'),'age_hours':str(age) if age is not None else None,
               'over_24_hours':age>24 if age is not None else None,'independently_source_verified':False}
        cap={'state':'not_reproducible','independently_source_verified':False}
        try:
            price,shares,reported=[number(inputs[k]) for k in ('price','shares_outstanding','market_cap')]
            if min(price,shares,reported)<=0:raise ValueError('Positive price, shares and market cap are required.')
            difference=abs(price*shares/reported-1)
            cap.update(state='provider_internal_reconciliation_only' if difference<=Decimal('.25') else 'provider_values_need_reconciliation',implied_equity_market_cap=str(price*shares),reported=str(reported),relative_difference=str(difference),policy_tolerance='0.25')
        except (ValueError,KeyError,ArithmeticError) as exc:cap['reason']=str(exc)
        result={'methodology':VERSION,'ticker':inputs['ticker'],'evaluated_at':at,'research_version':version,'retained_normalization_schema':inputs.get('_fw_version'),
                'retained_inputs':inputs,'input_sha256':sha(encoded(inputs)),'primary_context':context,
                'checks':checks,'check_counts':dict(Counter(r['state'] for r in checks)),'core_source_matches':source_links,
                'quote':quote,'market_cap_reconciliation':cap,'conditional_valuation':scenario_checks(inputs),
                'provider_fields':[{'field':key,'label':label,'value':inputs.get(key),'state':'provider_reported_unverified' if inputs.get(key) is not None else 'not_available','reason':'Same-period original operands and provider definition are not retained; annual SEC references do not verify this value.'} for key,label in PROVIDER.items()],
                'other_legacy_models':[{'field':key,'state':'not_independently_verified' if inputs.get(key) is not None else 'not_available'} for key in MODELS],
                'display_suppressions':suppress,'forecast_verified':False,'account_return_verified':False,'limitations':LIMITS}
        return {**result,'content_sha256':sha(encoded(result))}


def context_for(db,ticker):
    row=db.execute('SELECT version_id,payload_json FROM saved_list_items WHERE ticker=? ORDER BY version_id DESC LIMIT 1',(ticker,)).fetchone()
    return (row[0],financial_context(db,json.loads(row[1]),row[0])) if row else (None,None)


def current_report(db,data,at=None):
    version,context=context_for(db,data['ticker'])
    return build(project(data),context,version,at or now())


def init_number_evidence(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS number_evidence_reports(id TEXT PRIMARY KEY,ticker TEXT NOT NULL,captured_at TEXT NOT NULL,
          input_sha256 TEXT NOT NULL,report_json TEXT NOT NULL,report_sha256 TEXT NOT NULL)''')
        immutable(db,'number_evidence_reports')


class Capture(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    context_sha256:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    request_id:str=Field(pattern=r'^[0-9a-f]{32}$')


def make_number_router(path):
    router=APIRouter(prefix='/api/number-evidence',tags=['Retained number evidence'])
    def connection():
        db=sqlite3.connect(path,timeout=30);db.row_factory=sqlite3.Row;return db
    @router.get('/{ticker}')
    def inspect(ticker:str):
        with connection() as db:
            db.execute('BEGIN');row=db.execute('SELECT data_json FROM market_data_cache WHERE ticker=?',(ticker.upper(),)).fetchone()
            if not row:raise HTTPException(404,'No retained market-input snapshot for this ticker.')
            data=json.loads(row[0])
            if data.get('ticker')!=ticker.upper():raise HTTPException(409,'Retained input ticker differs from the requested instrument.')
            return current_report(db,data)
    @router.post('/{ticker}/capture')
    def capture(ticker:str,body:Capture):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT data_json FROM market_data_cache WHERE ticker=?',(ticker.upper(),)).fetchone()
            if not row:raise HTTPException(404,'Retained numeric inputs not found.')
            data=json.loads(row[0])
            if data.get('ticker')!=ticker.upper():raise HTTPException(409,'Retained input ticker differs from the requested instrument.')
            report=current_report(db,data);context=report['primary_context']
            if report['input_sha256']!=body.input_sha256 or (context or {}).get('content_sha256')!=body.context_sha256:
                raise HTTPException(409,'Numeric inputs or source edition changed. Reload the numbers before capturing.')
            prior=db.execute('SELECT * FROM number_evidence_reports WHERE id=?',(body.request_id,)).fetchone()
            if prior:
                if sha(prior['report_json'])!=prior['report_sha256']:
                    raise HTTPException(409,'Existing capture fingerprint differs; original evidence requires review.')
                stored=json.loads(prior['report_json'])
                if prior['ticker']!=ticker.upper() or stored['input_sha256']!=body.input_sha256 or (stored['primary_context'] or {}).get('content_sha256')!=body.context_sha256:
                    raise HTTPException(409,'Capture identifier already belongs to different evidence.')
                return {'id':prior['id'],'created':False,'report_sha256':prior['report_sha256'],'captured_at':prior['captured_at']}
            raw=encoded(report)
            db.execute('INSERT INTO number_evidence_reports VALUES(?,?,?,?,?,?)',(body.request_id,ticker.upper(),report['evaluated_at'],report['input_sha256'],raw,sha(raw)))
            return {'id':body.request_id,'created':True,'report_sha256':sha(raw),'captured_at':report['evaluated_at']}
    @router.get('/reports/{identifier}')
    def export(identifier:str):
        with connection() as db:
            db.execute('BEGIN');row=db.execute('SELECT * FROM number_evidence_reports WHERE id=?',(identifier,)).fetchone()
            if not row:raise HTTPException(404,'Number-evidence capture not found.')
            try:
                report=json.loads(row['report_json']);context=report['primary_context']
                if context:
                    item=db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?',(report['research_version'],row['ticker'])).fetchone()
                    actual=financial_context(db,json.loads(item[0]),report['research_version']) if item else None
                else:actual=None
                if (sha(row['report_json'])!=row['report_sha256'] or actual!=context
                        or build(report['retained_inputs'],actual,report['research_version'],report['evaluated_at'])!=report
                        or row['input_sha256']!=report['input_sha256'] or row['captured_at']!=report['evaluated_at'] or row['ticker']!=report['ticker']):
                    raise ValueError('Number evidence does not replay.')
            except (ValueError,KeyError,TypeError,AttributeError,ArithmeticError):
                raise HTTPException(409,'Captured numbers differ from original-source replay or fingerprints.')
            return {**dict(row),'report':report}
    return router
