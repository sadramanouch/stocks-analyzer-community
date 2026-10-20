"""Separate filed cash-flow scopes without relabeling legacy capex or strategies."""
import hashlib
import json
import re
import sqlite3
import shutil
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from financial_context import financial_context,receipt,eligible_facts,choose,coherent,amount
from research_store import encoded,immutable,now

METHOD='filed-cashflow-scopes-v1'
DEFINITIONS={
 'reported_operating_cf':('NetCashProvidedByUsedInOperatingActivities','Reported total operating cash flow','Total reported operating cash flow; signed inflow or outflow.'),
 'continuing_operating_cf':('NetCashProvidedByUsedInOperatingActivitiesContinuingOperations','Continuing operating cash flow','Excludes discontinued operations; never substituted for total operating cash flow.'),
 'discontinued_operating_cf':('CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations','Discontinued operating cash flow','Reported operating cash flow of discontinued operations.'),
 'ppe_payments':('PaymentsToAcquirePropertyPlantAndEquipment','PP&E cash payments','Cash payments for property, plant and equipment; excludes separately reported software and other intangible spending.'),
 'productive_asset_payments':('PaymentsToAcquireProductiveAssets','Productive-asset cash payments','Broader payments for PP&E, software and other intangible assets. Not an alias for PP&E-only capex.'),
 'oil_gas_property_equipment_payments':('PaymentsToAcquireOilAndGasPropertyAndEquipment','Oil/gas property and equipment cash payments','Industry-specific property and equipment payments. Other investment categories can remain outside this measure.'),
 'software_payments':('PaymentsToAcquireSoftware','Software cash payments','Separately reported software payments; not added to another category automatically.'),
 'intangible_payments':('PaymentsToAcquireIntangibleAssets','Intangible-asset cash payments','Separately reported intangible payments; may overlap a broader productive-asset category.'),
 'acquisition_payments_net':('PaymentsToAcquireBusinessesNetOfCashAcquired','Business-acquisition payments, net','Acquisition payments net of cash acquired; not ordinary capex and not added to gross acquisition payments.'),
 'acquisition_payments_gross':('PaymentsToAcquireBusinessesGross','Business-acquisition payments, gross','Gross cash acquisition payments; not interchangeable with net acquisition payments.'),
}
SPENDING=set(DEFINITIONS)-{'reported_operating_cf','continuing_operating_cf','discontinued_operating_cf'}
MEASURES={
 'cash_after_ppe_payments':('ppe_payments','Operating cash flow less PP&E payments'),
 'cash_after_productive_asset_payments':('productive_asset_payments','Operating cash flow less productive-asset payments'),
 'cash_after_oil_gas_property_equipment_payments':('oil_gas_property_equipment_payments','Operating cash flow less oil/gas property and equipment payments'),
}
LIMITS=[
 'These are dated annual USD cash-flow measures from archived SEC facts. Reported facts can later be corrected or restated; this is not a point-in-time backtest.',
 'PP&E, productive assets, oil/gas spending, software, intangibles and business acquisitions have different scopes. Missing categories remain unknown. Categories are never automatically added together.',
 'Operating cash flow and spending must share the same start, end, accession, filing date and form. Continuing operating cash flow alone does not establish total operating cash flow.',
 'No legacy capex, FCF, company ranking, saved research status, valuation assumption, copy policy, frozen study, order or account return is changed by this report.',
 'Cash remaining after a stated spending category is not cash available to shareholders: debt, leases, financing, acquisitions, working-capital sustainability and other obligations need separate evidence.',
]


def sha(value):return hashlib.sha256(value.encode()).hexdigest()


def compatible(facts):
    return coherent(facts) and len({f.get('form') for f in facts})==1


def missing(reason):return {'status':'unavailable','value':None,'reason':reason}


def total_operating(facts):
    total=facts['reported_operating_cf'];continuing=facts['continuing_operating_cf'];discontinued=facts['discontinued_operating_cf']
    check={'status':'not_comparable','difference':None,'reason':'All three scopes need identical filing and annual duration.'}
    if compatible([total,continuing,discontinued]):
        diff=amount(total['value'])-amount(continuing['value'])-amount(discontinued['value'])
        check={'status':'components_reconcile' if diff==0 else 'reported_components_disagree','difference':str(diff),
               'reason':'Exact reported USD amounts; no rounding tolerance or missing-zero assumption is invented.'}
        if diff!=0:return missing('Reported total and reported components disagree; derived cash measures are withheld.'),check
    if total['status']=='source_matched':
        return {'status':'reported_total','value':total['value'],'inputs':['reported_operating_cf'],'facts':[total]},check
    if total['status']!='missing':return missing('The reported total has conflicting or unsupported evidence; components do not override it.'),check
    if not compatible([continuing,discontinued]):return missing('Both continuing and discontinued operating cash flow must be explicitly reported in the same annual filing.'),check
    return {'status':'derived_total_from_reported_components','value':str(amount(continuing['value'])+amount(discontinued['value'])),
            'inputs':['continuing_operating_cf','discontinued_operating_cf'],'facts':[continuing,discontinued]},check


def cash_measure(total,spending,spending_key,label):
    result={'label':label,'spending_scope':spending_key,'status':'unavailable','value':None}
    if total.get('value') is None or spending['status']!='source_matched':return {**result,'reason':'A supported total operating cash flow and explicitly reported spending category are required.'}
    if not compatible(total['facts']+[spending]):return {**result,'reason':'The cash-flow inputs have different filing identities or annual durations; they are not combined.'}
    return {**result,'status':'derived_from_original_facts','value':str(amount(total['value'])-amount(spending['value'])),
            'formula':'total operating cash flow - '+spending_key,'operating_cf_basis':total['status'],'operating_cf_inputs':total['inputs']}


def period_report(payload,end,offset,cutoff):
    facts={}
    for field,(concept,label,description) in DEFINITIONS.items():
        fact=choose(eligible_facts(payload,concept,True,cutoff),end)
        if field in SPENDING and fact['status']=='source_matched' and amount(fact['value'])<0:
            fact={**fact,'status':'requires_review','reason':'Negative payment magnitude needs separate semantic review; no absolute-value correction is applied.'}
        facts[field]={**fact,'label':label,'scope':description}
    total,reconciliation=total_operating(facts)
    measures={key:cash_measure(total,facts[field],field,label) for key,(field,label) in MEASURES.items()}
    return {'end':end,'expected_year_offset':offset,'facts':facts,'total_operating_cf':total,
            'operating_scope_reconciliation':reconciliation,'cash_measures':measures}


def build(db,item,version):
    base=financial_context(db,item,version)
    result={'methodology':METHOD,'ticker':item['ticker'],'research_version':version,'saved_at':item.get('rebuilt_at'),
            'saved_item_sha256':sha(encoded(item)),'source_context_sha256':base['content_sha256'],'sources':base['sources'],
            'status':'unavailable','periods':[],'legacy_baseline_differences':base.get('baseline_differences',[]),'issues':list(base['issues']),'definitions':{k:{'concept':v[0],'label':v[1],'scope':v[2]} for k,v in DEFINITIONS.items()},
            'limitations':LIMITS,'selection_policy_unchanged':True,'legacy_capex_replaced':False,'account_return_verified':False,'forecast_verified':False}
    if base['sources'] and base['periods'] and base['status']=='source_context_available':
        source=base['sources'][0]
        _,payload=receipt(db,db.execute('SELECT * FROM financial_source_documents WHERE id=?',(source['id'],)).fetchone(),item['rebuilt_at'])
        cutoff=min(date.fromisoformat(item['rebuilt_at'][:10]),date.fromisoformat(source['observed_at'][:10]))
        for period in base['periods']:
            result['periods'].append(period_report(payload,period['end'],period['expected_year_offset'],cutoff))
        result['status']='source_scopes_available'
    result['coverage']={'source_matched_facts':sum(f['status']=='source_matched' for p in result['periods'] for f in p['facts'].values()),
                        'available_cash_measures':sum(m['value'] is not None for p in result['periods'] for m in p['cash_measures'].values()),
                        'derived_total_operating_periods':sum(p['total_operating_cf']['status']=='derived_total_from_reported_components' for p in result['periods'])}
    result['content_sha256']=sha(encoded(result))
    return result


def init_cashflow_evidence(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS cashflow_evidence_reports(
          id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,ticker TEXT NOT NULL,research_version INTEGER NOT NULL,
          captured_at TEXT NOT NULL,report_json TEXT NOT NULL,report_sha256 TEXT NOT NULL)''')
        immutable(db,'cashflow_evidence_reports')


class Capture(BaseModel):
    model_config=ConfigDict(extra='forbid')
    research_version:int=Field(ge=1)
    content_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')


def item_for(db,ticker,version):
    row=db.execute('SELECT payload_json FROM saved_list_items WHERE version_id=? AND ticker=?',(version,ticker)).fetchone()
    if not row:raise HTTPException(404,'Saved company/edition not found')
    return json.loads(row[0])


def replay(db,row):
    if sha(row['report_json'])!=row['report_sha256']:raise ValueError('Cash-flow report fingerprint mismatch')
    report=build(db,item_for(db,row['ticker'],row['research_version']),row['research_version'])
    if encoded(report)!=row['report_json']:raise ValueError('Cash-flow report source replay mismatch')
    return {'id':row['id'],'captured_at':row['captured_at'],'report_sha256':row['report_sha256'],'report_json':row['report_json'],'report':report}


def make_cashflow_router(path):
    router=APIRouter(prefix='/api/cashflow-evidence',tags=['Filed cash-flow scopes'])
    @router.get('/reports/{report_id}')
    def report(report_id:str):
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN');row=db.execute('SELECT * FROM cashflow_evidence_reports WHERE id=?',(report_id,)).fetchone()
            if not row:raise HTTPException(404,'Cash-flow capture not found')
            try:return replay(db,row)
            except (ValueError,KeyError,TypeError):raise HTTPException(409,'Cash-flow evidence integrity check failed') from None
    @router.get('/{ticker}')
    def current(ticker:str,version:int|None=Query(None,ge=1)):
        ticker=ticker.upper()
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):raise HTTPException(422,'Invalid ticker')
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            version=version or db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
            if version is None:raise HTTPException(404,'No saved edition')
            return build(db,item_for(db,ticker,version),version)
    @router.post('/{ticker}/capture')
    def capture(ticker:str,body:Capture):
        ticker=ticker.upper()
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):raise HTTPException(422,'Invalid ticker')
        with sqlite3.connect(path,timeout=30) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM cashflow_evidence_reports WHERE request_id=?',(body.request_id,)).fetchone()
            if previous:
                try:prior=replay(db,previous)
                except (ValueError,KeyError,TypeError,HTTPException):raise HTTPException(409,'Cash-flow evidence integrity check failed') from None
                if previous['ticker']!=ticker or previous['research_version']!=body.research_version or prior['report']['content_sha256']!=body.content_sha256:raise HTTPException(409,'Capture request ID was already used for different inputs')
                return {**prior,'created':False}
            current_version=db.execute('SELECT MAX(id) FROM saved_list_versions').fetchone()[0]
            if current_version!=body.research_version:raise HTTPException(409,'Saved research edition changed; reload before capturing')
            result=build(db,item_for(db,ticker,current_version),current_version)
            if result['content_sha256']!=body.content_sha256:raise HTTPException(409,'Source context changed; reload before capturing')
            if not result['coverage']['source_matched_facts']:raise HTTPException(409,'No usable source facts to capture')
            if shutil.disk_usage(path).free<256*1024*1024+1_000_000:raise HTTPException(409,'Evidence storage reserve reached')
            raw=encoded(result);key=uuid.uuid4().hex;at=now()
            db.execute('INSERT INTO cashflow_evidence_reports VALUES(?,?,?,?,?,?,?)',(key,body.request_id,ticker,current_version,at,raw,sha(raw)))
            row=db.execute('SELECT * FROM cashflow_evidence_reports WHERE id=?',(key,)).fetchone()
            return {**replay(db,row),'created':True}
    return router
