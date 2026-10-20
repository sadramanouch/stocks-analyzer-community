"""Isolated broker-record intake. Reconciled arithmetic is not source authenticity.

No broker credentials, orders or mutations to the virtual paper ledger. Opening
positions are statement assertions, never invented purchases or funding records.
"""
import json, sqlite3
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from paper_ledger import amount, instant
from research_store import encoded, immutable, now
from forward_performance import fingerprint

SCHEMA='broker-reconciliation-v1'


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Holding(Strict):
    ticker:str=Field(pattern=r'^[A-Z][A-Z0-9.\-]{0,23}$')
    shares:str
    price:str
    market_value:str
    quote_at:str
    price_decimals:int=Field(ge=0,le=8)
    currency:str=Field(pattern='^USD$')
    asset_type:str=Field(pattern='^(common_equity|etf)$')
    source:str=Field(min_length=1,max_length=300)


class Statement(Strict):
    as_of:str
    cash:str
    nav:str
    source:str=Field(min_length=1,max_length=300)
    holdings:list[Holding]=Field(max_length=100)


class Bundle(Strict):
    schema_version:str=Field(pattern='^broker-reconciliation-v1$')
    broker_name:str=Field(min_length=1,max_length=80)
    account_label:str=Field(min_length=1,max_length=80)
    currency:str=Field(pattern='^USD$')
    completeness_asserted:bool=False
    opening:Statement
    closing:Statement
    transactions:list[dict]=Field(max_length=500)


class Intake(Strict):
    data_json:str=Field(min_length=2,max_length=750000)
    preview_token:str|None=None


def init_broker(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS broker_record_bundles(
          id INTEGER PRIMARY KEY,recorded_at TEXT NOT NULL,broker_name TEXT NOT NULL,account_label TEXT NOT NULL,
          source_sha256 TEXT NOT NULL UNIQUE,result_sha256 TEXT NOT NULL,source_json TEXT NOT NULL,result_json TEXT NOT NULL)''')
        immutable(db,'broker_record_bundles')


def pairs(values):
    d={}
    for k,v in values:
        if k in d:raise ValueError('Duplicate JSON field; source values are ambiguous.')
        d[k]=v
    return d


def statement(s,at):
    stamp=instant(s.as_of)
    if stamp>at:raise ValueError('Statement cannot be future-dated.')
    cash=amount(s.cash,positive=False);nav=amount(s.nav,positive=False);holdings={};value=cash
    for p in s.holdings:
        if p.ticker in holdings:raise ValueError('Duplicate statement holding.')
        q=instant(p.quote_at)
        if not 0<=(stamp-q).total_seconds()<=345600:raise ValueError('Statement marks need explicit quote dates within four days, preceding the statement.')
        shares=amount(p.shares);price=amount(p.price);market=amount(p.market_value)
        bound=shares*(Decimal(10)**-p.price_decimals)/2+Decimal('.005')
        if abs(shares*price-market)>bound:raise ValueError('Holding market value differs from shares × reported price beyond its explicit rounding precision.')
        holdings[p.ticker]=shares;value+=market
    if abs(value-nav)>Decimal('.01'):raise ValueError('Statement NAV does not equal reported cash plus holding market values within one cent.')
    return cash,holdings,nav


def reconcile(raw,at=None):
    at=instant(at or now())
    if not isinstance(raw,str) or len(raw)>750000:raise ValueError('Provide a bounded normalized JSON bundle.')
    parsed=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
    bundle=Bundle.model_validate(parsed)
    begin,end=instant(bundle.opening.as_of),instant(bundle.closing.as_of)
    if not begin<end:raise ValueError('Closing statement must follow the opening statement.')
    cash,positions,opening_nav=statement(bundle.opening,at);expected_cash,expected_positions,closing_nav=statement(bundle.closing,at)
    first_cash=cash;flows=Decimal(0);fees=income=taxes=Decimal(0);seen=set();last=begin
    for event in bundle.transactions:
        if not isinstance(event,dict):raise ValueError('Each transaction must be an object.')
        common={'event_id','kind','occurred_at','source','currency'}
        kind=event.get('kind');specific={'deposit':{'amount'},'withdrawal':{'amount'},'fee':{'amount'},'tax':{'amount'},'interest':{'amount'},
                                     'dividend':{'ticker','amount','entitled_shares','cash_per_share','entitlement_at'},
                                     'buy':{'ticker','shares','price','fee'},'sell':{'ticker','shares','price','fee'},'split':{'ticker','new_shares','old_shares'}}
        if kind not in specific or set(event)!=common|specific[kind]:raise ValueError('Unsupported/ambiguous transaction fields. Map the known broker format explicitly; do not guess transfers or corporate actions.')
        identifier=event['event_id']
        if not isinstance(identifier,str) or not 1<=len(identifier)<=128 or identifier in seen:raise ValueError('Every transaction needs a unique source event_id.')
        seen.add(identifier)
        when=instant(event['occurred_at'])
        if not begin<when<=end or when<last:raise ValueError('Transactions must be ordered, strictly after opening and no later than closing.')
        last=when
        if event['currency']!='USD' or not isinstance(event['source'],str) or not event['source'].strip() or len(event['source'])>300:raise ValueError('Every transaction needs USD currency and a source reference.')
        ticker=event.get('ticker')
        if ticker is not None and (not isinstance(ticker,str) or not re_ticker(ticker)):raise ValueError('Invalid common-equity ticker.')
        if kind in ('buy','sell'):
            quantity=amount(event['shares']);price=amount(event['price']);fee=amount(event['fee'],positive=False);sign=1 if kind=='buy' else -1
            positions[ticker]=positions.get(ticker,Decimal(0))+sign*quantity;cash-=sign*quantity*price+fee;fees+=fee
        elif kind=='split':
            if positions.get(ticker,Decimal(0))<=0:raise ValueError('Split requires an existing holding.')
            positions[ticker]*=amount(event['new_shares'])/amount(event['old_shares'])
        else:
            value=amount(event['amount'])
            sign=-1 if kind in ('withdrawal','fee','tax') else 1
            if kind=='dividend':
                if amount(event['entitled_shares'])*amount(event['cash_per_share'])!=value or instant(event['entitlement_at'])>when:raise ValueError('Dividend cash does not match the documented entitlement.')
            cash+=sign*value
            if kind in ('deposit','withdrawal'):flows+=sign*value
            elif kind=='fee':fees+=value
            elif kind=='tax':taxes+=value
            else:income+=value
        if cash<0 or any(v<0 for v in positions.values()):raise ValueError('Negative cash/shares: margin, shorting or incomplete records require another explicit account model.')
    actual={t:v for t,v in positions.items() if v};cash_difference=cash-expected_cash
    position_differences={t:str(actual.get(t,Decimal(0))-expected_positions.get(t,Decimal(0))) for t in set(actual)|set(expected_positions) if actual.get(t,Decimal(0))!=expected_positions.get(t,Decimal(0))}
    reconciled=abs(cash_difference)<=Decimal('.01') and not position_differences
    has_flows=any(e['kind'] in ('deposit','withdrawal') for e in bundle.transactions)
    reference=(closing_nav/opening_nav-1) if reconciled and bundle.completeness_asserted and not has_flows and opening_nav>0 else None
    gaps=[]
    if not reconciled:gaps.append('Closing cash or shares do not reconcile with the statement. Missing events/actions or incorrect mappings need review.')
    if not bundle.completeness_asserted:gaps.append('Full interval record coverage has not been asserted.')
    if has_flows:gaps.append('External funding requires additional boundary valuations; endpoint NAV ratios are not account returns.')
    if opening_nav==0:gaps.append('Positive starting NAV is required for an interval return.')
    return {'schema_version':SCHEMA,'broker_name':bundle.broker_name,'account_label':bundle.account_label,
            'opening_at':bundle.opening.as_of,'closing_at':bundle.closing.as_of,'transaction_count':len(bundle.transactions),
            'opening_cash':str(first_cash),'replayed_closing_cash':str(cash),'statement_closing_cash':str(expected_cash),'cash_difference':str(cash_difference),
            'replayed_shares':{t:str(v) for t,v in sorted(actual.items())},'share_differences':position_differences,
            'opening_statement_nav':str(opening_nav),'closing_statement_nav':str(closing_nav),'net_external_funding':str(flows),
            'recorded_fees':str(fees),'recorded_income':str(income),'recorded_taxes':str(taxes),'arithmetic_reconciled':reconciled,
            'statement_reference_return':str(reference) if reference is not None else None,'verified_account_return':None,
            'source_authenticity':'Uploaded normalized assertions; broker origin/authenticity and completeness are not independently verified.',
            'gaps':gaps,'paper_ledger_changed':False}


def re_ticker(ticker):
    import re
    return bool(re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,23}',ticker))


def preview(raw):
    return {'source_sha256':fingerprint(raw),'preview_token':fingerprint(raw),'result':reconcile(raw),
            'message':'Preview only. A saved bundle remains separate from the virtual portfolio and does not place orders.'}


def commit(path,raw,token):
    data=preview(raw)
    if token!=data['preview_token']:raise ValueError('Source file changed; preview the exact current content again.')
    output=encoded(data['result'])
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT id FROM broker_record_bundles WHERE source_sha256=?',(data['source_sha256'],)).fetchone()
        if old:return {'created':False,'id':old[0],'paper_ledger_changed':False}
        identifier=db.execute('INSERT INTO broker_record_bundles VALUES(NULL,?,?,?,?,?,?,?)',(now(),data['result']['broker_name'],data['result']['account_label'],data['source_sha256'],fingerprint(output),raw,output)).lastrowid
    return {'created':True,'id':identifier,'paper_ledger_changed':False,'message':'Source assertions and reconciliation saved separately. No paper funding, broker connection or real order created.'}


def checked_bundle(r):
    if fingerprint(r['source_json'])!=r['source_sha256'] or fingerprint(r['result_json'])!=r['result_sha256']:raise ValueError('Broker bundle fingerprint differs.')
    result=reconcile(r['source_json'])
    if encoded(result)!=r['result_json']:raise ValueError('Broker result differs on arithmetic replay.')
    if result['broker_name']!=r['broker_name'] or result['account_label']!=r['account_label']:raise ValueError('Broker bundle identity differs.')
    return result


def intake_error(exc):
    if isinstance(exc,ValidationError):return 'Invalid normalized record fields: '+', '.join('.'.join(map(str,e['loc'])) for e in exc.errors(include_input=False))[:500]
    if isinstance(exc,json.JSONDecodeError):return 'Source file is not valid JSON.'
    return str(exc)


def make_broker_router(path):
    router=APIRouter(prefix='/api/broker-reconciliation',tags=['Isolated broker record reconciliation'])
    @router.get('')
    def overview():
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('SELECT * FROM broker_record_bundles ORDER BY id DESC LIMIT 25')]
        bundles=[];errors=[]
        for r in rows:
            try:
                result=checked_bundle(r);bundles.append({k:r[k] for k in ('id','recorded_at','broker_name','account_label','source_sha256')}|{'result':result})
            except (ValueError,KeyError,TypeError,ArithmeticError):errors.append({'id':r['id'],'message':'Stored bundle failed fingerprint or arithmetic replay; displayed values withheld.'})
        return {'bundles':bundles,'integrity_errors':errors,
                'verified_account_return':None,'paper_ledger_changed':False,'input_schema':SCHEMA,
                'notice':'Broker identity and original exports are needed for a native adapter and independent verification. Never include credentials or account numbers.'}
    @router.get('/schema')
    def schema():return Bundle.model_json_schema()
    @router.post('/preview')
    def inspect(body:Intake):
        try:return preview(body.data_json)
        except (ValueError,KeyError,TypeError,ArithmeticError) as exc:raise HTTPException(422,intake_error(exc))
    @router.post('/commit')
    def record(body:Intake):
        try:return commit(path,body.data_json,body.preview_token)
        except (ValueError,KeyError,TypeError,ArithmeticError) as exc:raise HTTPException(422,intake_error(exc))
    @router.get('/bundles/{identifier}')
    def export(identifier:int):
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;r=db.execute('SELECT * FROM broker_record_bundles WHERE id=?',(identifier,)).fetchone()
        if not r:raise HTTPException(404,'Broker source bundle not found.')
        try:result=checked_bundle(r)
        except (ValueError,KeyError,TypeError,ArithmeticError):raise HTTPException(409,'Broker fingerprint or arithmetic replay failed.')
        return {'id':identifier,'source_sha256':r['source_sha256'],'result_sha256':r['result_sha256'],'original_data_json':r['source_json'],'result':result}
    return router
