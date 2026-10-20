"""Fixed, prospective close-reference study. Never reports executed follower P&L."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter
from research_store import encoded,immutable,now
from market_history import latest_histories,NY
from portfolio_reconciliation import CONFIRMED_SPLITS, REVIEWED_DISTRIBUTIONS

POLICY={'version':'copy-close-reference-v2','eligible':'Original SEC common-stock purchases without unresolved flags, plans or amendments',
        'maximum_disclosure_age_days':7,'entry':'First complete reference close on a later exchange date than first observation',
        'holding_bars':5,'fees_bps_each_side':10,'slippage_bps_each_side':10,'benchmark':'SPY',
        'price_basis':'Vendor split/dividend-adjusted closing-price proxy; no additional split or dividend cash applied',
        'fundamentals':'Current financial scores are not used as historical selection inputs',
        'corporate_actions':'Block intervals with reviewed distributions or vendor split factors lacking primary ratio/date corroboration',
        'limitations':'Bounded observed issuer universe, vendor adjustment assumptions and hypothetical closes/costs. Not a verified execution, account return, complete backtest or trader ranking.'}


def init_copy(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS copy_reference_studies (
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, policy_json TEXT NOT NULL,
            input_sha256 TEXT NOT NULL, inputs_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, results_json TEXT NOT NULL)''')
        immutable(db,'copy_reference_studies')


def instant(raw):
    dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Copy availability requires timezone-aware timestamps')
    return dt.astimezone(timezone.utc)


def eligible(event,policy=None):
    policy=policy or POLICY
    reasons=[]
    if event.get('kind')!='insiders' or event.get('transaction_code')!='P' or event.get('side')!='buy' or event.get('table')!='nonDerivative' or event.get('instrument')!='CS':reasons.append('Not an original common-stock purchase')
    if event.get('source_verification')!='primary_parsed' or not event.get('accession'):reasons.append('Primary source fields require review')
    if event.get('amendment') or event.get('footnotes') or event.get('trading_plan') in ('1','true'):reasons.append('Amendment, footnotes or plan requires economic review')
    try:
        observed=instant(event['available_at']);first=instant(event['first_seen_at']);disclosed=instant(event['disclosed_at'])
        if observed<first or observed<disclosed or first<disclosed:reasons.append('Invalid source availability ordering')
        age=(observed-disclosed).total_seconds()/86400
        if age>policy['maximum_disclosure_age_days']:reasons.append('Disclosure was already older than seven days when observed')
    except (ValueError,TypeError,KeyError):reasons.append('Precise observed availability is missing')
    return reasons


def study_event(event,histories,at,policy=None,action_reviews=None):
    policy=policy or POLICY
    action_reviews=action_reviews or {'splits':CONFIRMED_SPLITS,'distributions':REVIEWED_DISTRIBUTIONS}
    result={'event_id':event['id'],'actor':event.get('actor'),'asset':event.get('asset'),'available_at':event.get('available_at'),
            'source_url':event.get('source_url'),'status':'excluded','reasons':eligible(event,policy),
            'gross_reference_return_pct':None,'net_reference_return_pct':None,'benchmark_reference_return_pct':None,
            'excess_reference_return_pct':None,'executed_copy_return_pct':None}
    if result['reasons']:return result
    if instant(event['available_at'])>instant(at):
        result['reasons']=['Signal was not yet available at study time'];return result
    result['status']='needs_reference_prices'
    doc=histories.get(event['asset']);bench=histories.get(policy['benchmark'])
    if not doc or not bench:
        result['reasons']=['Matching archived asset and SPY histories are needed'];return result
    if any(instant(d['observed_at'])>instant(at) for d in (doc,bench)):
        result['reasons']=['A price archive was observed after this study time'];return result
    day=instant(event['available_at']).astimezone(NY).date().isoformat()
    bars=[b for b in doc['history']['bars'] if b['date']>day]
    # Never substitute an earlier disclosure-day close for a later observed signal.
    if len(bars)<policy['holding_bars']:
        result.update(status='pending_reference_window',reasons=['Five complete post-observation reference bars are not yet archived']);return result
    entry,exit_bar=bars[0],bars[policy['holding_bars']-1]
    for symbol,source in ((event['asset'],doc),(policy['benchmark'],bench)):
        actions=[b for b in source['history']['bars'] if entry['date']<b['date']<=exit_bar['date'] and b['split_ratio']]
        complex_actions=[a for a in action_reviews['distributions'] if a['ticker']==symbol and entry['date']<a['effective_date']<=exit_bar['date']]
        missing_known=[a for a in action_reviews['splits'] if a['ticker']==symbol and entry['date']<a['effective_date']<=exit_bar['date'] and (a.get('requires_review') or not any(b['date']==a['effective_date'] and abs(Decimal(str(b['split_ratio']))-Decimal(a['new_shares'])/Decimal(a['old_shares']))<Decimal('1e-8') for b in actions))]
        if policy.get('corporate_actions') and (complex_actions or missing_known or any(not any(a['ticker']==symbol and a['effective_date']==b['date'] and not a.get('requires_review') and abs(Decimal(str(b['split_ratio']))-Decimal(a['new_shares'])/Decimal(a['old_shares']))<Decimal('1e-8') for a in action_reviews['splits']) for b in actions)):
            result['reasons']=['A distribution or uncorroborated vendor action affects the asset or benchmark interval'];return result
    if any(entry['date']<=d<=exit_bar['date'] for d in doc['history'].get('omitted_price_dates',[])):
        result['reasons']=['Incomplete source prices within the study interval'];return result
    benchmark={b['date']:b for b in bench['history']['bars']}
    if entry['date'] not in benchmark or exit_bar['date'] not in benchmark:
        result['reasons']=['Benchmark does not cover the same reference endpoints'];return result
    gross=Decimal(str(exit_bar['adjusted_close']))/Decimal(str(entry['adjusted_close']))
    benchmark_gross=Decimal(str(benchmark[exit_bar['date']]['adjusted_close']))/Decimal(str(benchmark[entry['date']]['adjusted_close']))
    cost=Decimal(policy['fees_bps_each_side']+policy['slippage_bps_each_side'])/10000
    factor=(1-cost)/(1+cost)
    result.update(status='reference_study_only',reasons=[policy['limitations']],entry_date=entry['date'],exit_date=exit_bar['date'],
        gross_reference_return_pct=float((gross-1)*100),net_reference_return_pct=float((gross*factor-1)*100),
        benchmark_reference_return_pct=float((benchmark_gross*factor-1)*100),excess_reference_return_pct=float((gross-benchmark_gross)*factor*100),
        price_document_id=doc['id'],benchmark_document_id=bench['id'])
    return result


def create_study(path,at=None):
    at=at or now()
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('BEGIN')
        events=[json.loads(r[0]) for r in db.execute("SELECT event_json FROM trade_events WHERE json_extract(event_json,'$.transaction_code')='P' ORDER BY id")]
        histories=latest_histories(db)
        inputs={'policy':POLICY,'events':events,'price_document_ids':{k:v['id'] for k,v in histories.items()},
                'action_reviews':{'splits':CONFIRMED_SPLITS,'distributions':REVIEWED_DISTRIBUTIONS}}
        results=[study_event(e,histories,at) for e in events]
        result={'policy':POLICY,'observed_at':at,'purchase_rows':len(events),'eligible_signals':sum(r['status']!='excluded' for r in results),
                'completed_reference_studies':sum(r['status']=='reference_study_only' for r in results),'verified_executed_returns':0,'results':results}
        raw=encoded(result);sha=hashlib.sha256(raw.encode()).hexdigest();input_sha=hashlib.sha256(encoded(inputs).encode()).hexdigest()
        study_id=db.execute('INSERT INTO copy_reference_studies(observed_at,policy_json,input_sha256,inputs_json,result_sha256,results_json) VALUES(?,?,?,?,?,?)',(at,encoded(POLICY),input_sha,encoded(inputs),sha,raw)).lastrowid
    return {**result,'study_id':study_id,'input_sha256':input_sha,'result_sha256':sha}


def make_copy_router(path):
    router=APIRouter(prefix='/api/copy-study',tags=['Copy reference studies'])
    @router.get('')
    def latest():
        with sqlite3.connect(path,timeout=15) as db:r=db.execute('SELECT id,input_sha256,result_sha256,results_json FROM copy_reference_studies ORDER BY id DESC LIMIT 1').fetchone()
        if not r:return {'study_id':None,'policy':POLICY,'results':[],'verified_executed_returns':0}
        if hashlib.sha256(r[3].encode()).hexdigest()!=r[2]:
            from fastapi import HTTPException
            raise HTTPException(409,'Study archive hash mismatch')
        return {**json.loads(r[3]),'study_id':r[0],'input_sha256':r[1],'result_sha256':r[2]}
    @router.post('')
    def refresh():return create_study(path)
    return router
