"""Prospective daily-close paper study with a corroborated SPY reference.

Issuer prices/distributions and original vendor bytes are retained. This is a
declared-distribution reference model, never audited economic or broker returns.
"""
import hashlib, io, json, re, sqlite3, threading, zipfile
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from lxml import html
import yfinance as yf
from fastapi import APIRouter, HTTPException

from equity_workspace import connect, check_actions
from forward_performance import preview_proof, mark_proof, fingerprint
from market_history import completed_day, validate_history
from market_provenance import calendar_check, fetch_original_chart, save_source
from paper_ledger import amount, replay
from research_store import encoded, immutable, instant, now

NY=ZoneInfo('America/New_York')
ISSUER='https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy'
DISTRIBUTIONS='https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx'
POLICY={'version':'prospective-daily-close-spy-v1','benchmark':'SPY','currency':'USD','maximum_holdings':25,
        'calendar':'nyse-close-times-2026-v1','early_closes':['2026-11-27','2026-12-24'],
        'first_close':'First scheduled close strictly after study registration. Earlier data is source context only.',
        'benchmark_model':'Issuer closing price plus declared cash distributions accrued and hypothetically reinvested at the ex-date close. Gross of execution costs and taxes; fund expenses are already reflected in fund prices.',
        'portfolio_model':'Actual recorded simulated cash/shares at the same scheduled close; native fill costs retained. Unreconciled actions or flows without boundary valuations block comparison.',
        'limitations':'Original sources corroborate reported facts, not complete economic performance. No guaranteed action completeness, broker execution or trader profitability. Missing observations remain gaps.'}


def init_benchmark(path):
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS benchmark_studies(
          id INTEGER PRIMARY KEY,registered_at TEXT NOT NULL,first_session TEXT NOT NULL,policy_json TEXT NOT NULL,sha256 TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS benchmark_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL);
          INSERT OR IGNORE INTO benchmark_settings VALUES(1,0);
          CREATE TABLE IF NOT EXISTS benchmark_runs(id INTEGER PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,state TEXT NOT NULL,message TEXT NOT NULL,context_json TEXT);
          CREATE TABLE IF NOT EXISTS benchmark_observations(id INTEGER PRIMARY KEY,study_id INTEGER NOT NULL,session_date TEXT NOT NULL,
          observed_at TEXT NOT NULL,input_sha256 TEXT NOT NULL,result_sha256 TEXT NOT NULL,input_json TEXT NOT NULL,result_json TEXT NOT NULL,
          UNIQUE(study_id,session_date));''')
        for table in ('benchmark_studies','benchmark_observations'):immutable(db,table)
        db.execute("UPDATE benchmark_runs SET state='interrupted',finished_at=?,message='Worker interrupted; no missing close was inferred.' WHERE state='running'",(now(),))


def close_at(day):
    if calendar_check(day)['state']!='scheduled_session':raise ValueError('A covered scheduled NYSE session is required.')
    hour=13 if day in POLICY['early_closes'] else 16
    return datetime.combine(date.fromisoformat(day),datetime.min.time(),NY).replace(hour=hour).astimezone(timezone.utc)


def first_session(at):
    at=instant(at);day=at.astimezone(NY).date()
    for _ in range(10):
        if calendar_check(day.isoformat())['state']=='unknown_year':raise ValueError('Publish the next exchange-calendar version before starting a study.')
        if calendar_check(day.isoformat())['state']=='scheduled_session' and close_at(day.isoformat())>at:return day.isoformat()
        day+=timedelta(days=1)
    raise ValueError('No covered prospective session available.')


def issuer_price(body,at):
    if len(body)>2_000_000:raise ValueError('Issuer page too large.')
    doc=html.fromstring(body)
    headings=doc.xpath('//h1')
    if len(headings)!=1 or not re.match(r'^\s*SPY\b',headings[0].text_content()):raise ValueError('Issuer fund identity differs.')
    sections=[e.getparent() for e in doc.xpath('//h2') if 'Listing Information' in e.text_content()]
    rows=[[c.text_content().strip() for c in r.xpath('./td')] for section in sections for r in section.xpath('.//tr')]
    expected=['NYSE ARCA','Jan 22 1993','USD','SPY','78462F103','US78462F1030']
    if rows.count(expected)!=1:raise ValueError('Issuer ticker, currency, exchange, CUSIP or ISIN differs.')
    h=[e for e in doc.xpath('//h2') if 'Fund Market Price' in e.text_content()]
    if len(h)!=1:raise ValueError('Issuer price section ambiguous.')
    match=re.search(r'as of\s+([A-Za-z]{3} \d{1,2} \d{4})',' '.join(h[0].text_content().split()))
    if not match:raise ValueError('Issuer closing-price date is absent.')
    day=datetime.strptime(match[1],'%b %d %Y').date().isoformat()
    if date.fromisoformat(day)>completed_day(at) or (instant(at).astimezone(NY).date()-date.fromisoformat(day)).days>7:raise ValueError('Issuer close is unfinished or stale.')
    prices=[r.xpath('./td')[0].text_content().strip() for r in h[0].getparent().xpath('.//tr') if r.xpath('./th') and r.xpath('./th')[0].text_content().strip().startswith('Closing Price') and len(r.xpath('./td'))==1]
    if len(prices)!=1 or not re.fullmatch(r'\$[\d,]+\.\d{2}',prices[0]):raise ValueError('Official closing price is absent or ambiguous.')
    price=amount(prices[0].replace('$','').replace(',',''))
    return {'ticker':'SPY','currency':'USD','cusip':'78462F103','isin':'US78462F1030','session_date':day,'price':str(price),'close_at':close_at(day).isoformat(),'precision_usd':'0.01'}


def issuer_distributions(body):
    """Bounded, non-executable Office Open XML reader for the issuer's fixed table."""
    if len(body)>2_000_000:raise ValueError('Distribution file too large.')
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if len(z.infolist())>30 or sum(i.file_size for i in z.infolist())>15_000_000 or any(i.flag_bits&1 for i in z.infolist()):raise ValueError('Unsupported distribution archive.')
        def xml(name):
            raw=z.read(name)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('XML entities are unsupported.')
            return ET.fromstring(raw)
        workbook=xml('xl/workbook.xml');sheets=workbook.find('s:sheets',ns)
        if len(sheets)!=1 or sheets[0].get('name')!='dividend':raise ValueError('Distribution worksheet schema changed.')
        strings=[''.join(si.itertext()) for si in xml('xl/sharedStrings.xml').findall('s:si',ns)]
        rows=xml('xl/worksheets/sheet1.xml').findall('s:sheetData/s:row',ns)
        if not 1<len(rows)<=100000:raise ValueError('Distribution row count unsupported.')
        def cells(row):
            output={}
            for c in row:
                col=re.sub(r'\d','',c.get('r',''))
                if c.find('s:f',ns) is not None:raise ValueError('Distribution formulas are unsupported.')
                v=c.find('s:v',ns);value=v.text if v is not None else ''
                if c.get('t')=='s':value=strings[int(value)]
                elif c.get('t')=='inlineStr':value=''.join(c.find('s:is',ns).itertext())
                output[col]=str(value).strip()
            return output
        header=cells(rows[0])
        expected=['FUND NAME','TICKER','CUSIP','EX-DATE','RECORD DATE','PAYABLE DATE','DIVIDEND ($)','SHORT TERM CAPITAL GAIN ($)','LONG TERM CAPITAL GAIN ($)','FREQUENCY']
        if [header.get(chr(65+i)) for i in range(10)]!=expected:raise ValueError('Distribution headers changed.')
        result=[];seen=set()
        for row in rows[1:]:
            c=cells(row)
            if c.get('B')!='SPY':continue
            if c.get('C')!='78462F103':raise ValueError('Distribution instrument identifier differs.')
            dates=[datetime.strptime(c[k],'%m/%d/%Y').date().isoformat() for k in ('D','E','F')]
            if dates[0] in seen:raise ValueError('Duplicate distribution ex-date is ambiguous.')
            seen.add(dates[0]);reported={k:str(amount(c[k],positive=False)) for k in ('G','H','I') if c.get(k)}
            if not reported:raise ValueError('Declared cash amount absent.')
            result.append({'ex_date':dates[0],'record_date':dates[1],'payable_date':dates[2],
                           'declared_cash_per_share':str(sum(map(Decimal,reported.values()),Decimal(0))),
                           'date_status':'reported_order_consistent' if dates[0]<=dates[1]<=dates[2] else 'issuer_date_order_requires_review',
                           'reported_components':reported,'blank_component_columns':[k for k in ('G','H','I') if not c.get(k)]})
        if not result:raise ValueError('No issuer SPY distributions found.')
        return sorted(result,key=lambda r:r['ex_date'])


def source_body(db,identifier):
    r=db.execute('SELECT * FROM market_source_documents WHERE id=?',(identifier,)).fetchone()
    if not r or r['status_code']!=200 or hashlib.sha256(r['body_bytes']).hexdigest()!=r['sha256']:raise ValueError('Original benchmark source status/hash differs.')
    return dict(r)


def vendor_price(db,payload,day):
    r=source_body(db,payload['original_source_document_id']);raw=json.loads(r['body_bytes'])
    result=raw['chart']['result'][0];meta=result['meta']
    if r['ticker']!=meta['symbol'] or r['source_kind']!='yahoo_original_chart' or meta['symbol']!=payload['metadata']['symbol'] or meta['currency']!='USD' or meta['exchangeTimezoneName']!='America/New_York':raise ValueError('Original chart identity differs.')
    timestamps=result['timestamp'];values=result['indicators']['quote'][0]['close']
    if len(timestamps)!=len(values):raise ValueError('Original chart arrays differ in length.')
    matches=[Decimal(str(v)) for t,v in zip(timestamps,values) if v is not None and datetime.fromtimestamp(t,timezone.utc).astimezone(NY).date().isoformat()==day]
    bar=[b for b in payload['bars'] if b['date']==day]
    if len(matches)!=1 or len(bar)!=1 or matches[0]!=Decimal(str(bar[0]['close'])):raise ValueError('Close is missing or differs from the original vendor array.')
    return matches[0],{k:r[k] for k in ('id','ticker','observed_at','sha256','source_url')}


def vendor_cash(db,payload,day):
    raw=json.loads(source_body(db,payload['original_source_document_id'])['body_bytes'])['chart']['result'][0]
    return sum((Decimal(str(e['amount'])) for kind in ('dividends','capitalGains') for e in raw.get('events',{}).get(kind,{}).values()
                if datetime.fromtimestamp(e['date'],timezone.utc).astimezone(NY).date().isoformat()==day),Decimal(0))


def study_row(db):
    r=db.execute('SELECT * FROM benchmark_studies ORDER BY id DESC LIMIT 1').fetchone()
    if r and (fingerprint(r['policy_json'])!=r['sha256'] or json.loads(r['policy_json'])!=POLICY):raise ValueError('Study policy differs from its immutable version.')
    return dict(r) if r else None


def register(path,at=None):
    at=at or now()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');old=study_row(db)
        if old:return {'created':False,'study':old}
        day=first_session(at)
        if not db.execute('SELECT 1 FROM paper_ledger_events LIMIT 1').fetchone():raise ValueError('Create virtual paper capital before registering a study.')
        raw=encoded(POLICY);identifier=db.execute('INSERT INTO benchmark_studies VALUES(NULL,?,?,?,?)',(at,day,raw,fingerprint(raw))).lastrowid
        db.execute('UPDATE benchmark_settings SET enabled=1 WHERE id=1')
        return {'created':True,'study':dict(db.execute('SELECT * FROM benchmark_studies WHERE id=?',(identifier,)).fetchone())}


def collect(path,fetch=requests.get,history=None,at=None):
    explicit_at=at;at=at or now()
    with connect(path) as db:study=study_row(db)
    if not study:raise ValueError('Register a prospective study first.')
    refs=[];bodies=[]
    for url,kind in ((ISSUER,'spy_issuer_price'),(DISTRIBUTIONS,'spy_issuer_distributions')):
        response=fetch(url,timeout=30)
        identifier=save_source(path,'SPY',url,kind,response.status_code,{},response.content,at)
        response.raise_for_status();refs.append(identifier);bodies.append(response.content)
    quote=issuer_price(bodies[0],at);distributions=issuer_distributions(bodies[1]);day=quote['session_date'];close=instant(quote['close_at'])
    with connect(path) as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM paper_ledger_events ORDER BY rowid')]
    events=[];issues=[]
    for r in rows:
        if fingerprint(r['payload'])!=r['digest']:raise ValueError('Ledger fingerprint differs.')
        e=json.loads(r['payload'])
        if e['event_id']!=r['event_id'] or e['occurred_at']!=r['occurred_at']:raise ValueError('Ledger identity/date differs.')
        if instant(e['occurred_at'])<=close:
            if instant(r['recorded_at'])>close:issues.append('An event affecting this close was recorded later; no retrospective fill is assumed.')
            events.append(e)
    book=replay(events,close)
    if len(book['positions'])>POLICY['maximum_holdings']:raise ValueError('More than 25 holdings requires a separately bounded study policy.')
    # Context before the prospective anchor does not fetch portfolio holdings.
    symbols=list(dict.fromkeys(['SPY']+([p['ticker'] for p in book['positions']] if day>=study['first_session'] else [])))
    start=min(study['first_session'],(date.fromisoformat(day)-timedelta(days=7)).isoformat())
    held={p['ticker'] for p in book['positions']}
    if day>=study['first_session']:
        buys=[instant(e['occurred_at']).astimezone(NY).date().isoformat() for e in events if e['kind']=='buy' and e['ticker'] in held]
        if buys:start=min(start,min(buys))
    histories={};prices={};source_refs=[]
    for symbol in symbols:
        payload=(history(symbol,start,day,at) if history else validate_history(fetch_original_chart(symbol,start,day,yf.Ticker(symbol)._data,path,at),symbol,start,day,at))
        validate_history(payload,symbol,start,day,at)
        with connect(path) as db:price,ref=vendor_price(db,payload,day)
        histories[symbol]=payload;prices[symbol]=str(price);source_refs.append(ref)
    if abs(Decimal(prices['SPY'])-Decimal(quote['price']))>Decimal('.005'):raise ValueError('Issuer and vendor SPY closes disagree beyond half a cent.')
    context={'issuer_price':quote,'issuer_vendor_close_match':True,'distribution_rows':len(distributions),'distribution_date_review_rows':[d['ex_date'] for d in distributions if d['date_status']!='reported_order_consistent'],'study_first_session':study['first_session'],'context_only':day<study['first_session'],'source_ids':refs+[r['id'] for r in source_refs]}
    if day<study['first_session']:return {'state':'waiting_for_prospective_close','context':context}
    proofs={}
    with connect(path) as db:
        for e in events:
            if e.get('currency')!='USD':issues.append('Every ledger event requires explicit USD currency.')
            try:
                if e['kind'] in ('buy','sell','mark'):
                    proof=preview_proof(db,e) if e['event_id'].startswith(('forward-paper-','forward-mark-')) else mark_proof(db,e) if e['kind']=='mark' and e['event_id'].startswith('forward-refresh-') else None
                    if not proof:raise ValueError('Fill/mark lacks frozen native source evidence.')
                    proofs[proof['id']]=proof
                elif e['kind'] in ('split','dividend','fee'):raise ValueError('Imported action/fee evidence needs independent reconciliation.')
            except (ValueError,KeyError,TypeError,ArithmeticError) as exc:issues.append(str(exc))
        for position in book['positions']:
            t=position['ticker']
            if any(b['split_ratio'] or b['dividend'] for b in histories[t]['bars']):issues.append(f'{t}: reported corporate actions need reconciliation before comparable NAV.')
            raw_chart=json.loads(source_body(db,histories[t]['original_source_document_id'])['body_bytes'])['chart']['result'][0]
            if raw_chart.get('events',{}).get('capitalGains'):issues.append(f'{t}: reported capital distributions need reconciliation before comparable NAV.')
            try:check_actions(db,events,t,quote['close_at'],quote['close_at'])
            except ValueError as exc:issues.append(str(exc))
    if any(b['split_ratio'] for b in histories['SPY']['bars']):issues.append('SPY split-adjusted units require separate issuer split reconciliation.')
    at=explicit_at or now()
    nav=Decimal(book['cash'])+sum((Decimal(p['shares'])*Decimal(prices[p['ticker']]) for p in book['positions']),Decimal(0))
    inputs={'study':study,'observed_at':at,'session_date':day,'close_at':quote['close_at'],'ledger':rows,'events_at_close':events,
            'sources':refs,'histories':histories,'issuer_price':quote,'distributions':distributions,'source_decisions':list(proofs.values())}
    result={'session_date':day,'close_at':quote['close_at'],'observed_at':at,'cash':book['cash'],'nav':str(nav),
            'positions':[{'ticker':p['ticker'],'shares':p['shares'],'close':prices[p['ticker']]} for p in book['positions']],
            'benchmark_close':quote['price'],'issues':issues,'status':'blocked' if issues else 'paper_close_reference',
            'verified_account_return':None,'verified_benchmark_return':None}
    raw,output=encoded(inputs),encoded(result)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT id,result_json FROM benchmark_observations WHERE study_id=? AND session_date=?',(study['id'],day)).fetchone()
        if old:
            existing=json.loads(old['result_json'])
            if any(existing[k]!=result[k] for k in ('nav','benchmark_close','positions','issues')):raise ValueError('A previously observed close differs; retain the earlier edition and review the correction.')
            return {'state':'already_observed','observation_id':old['id'],'context':context}
        identifier=db.execute('INSERT INTO benchmark_observations VALUES(NULL,?,?,?,?,?,?,?)',(study['id'],day,at,fingerprint(raw),fingerprint(output),raw,output)).lastrowid
    return {'state':result['status'],'observation_id':identifier,'context':context}


def verify_observation(db,row):
    if fingerprint(row['input_json'])!=row['input_sha256'] or fingerprint(row['result_json'])!=row['result_sha256']:raise ValueError('Daily observation fingerprint differs.')
    inputs,result=json.loads(row['input_json']),json.loads(row['result_json'])
    if json.loads(inputs['study']['policy_json'])!=POLICY or fingerprint(inputs['study']['policy_json'])!=inputs['study']['sha256']:raise ValueError('Frozen benchmark policy differs.')
    original_study=dict(db.execute('SELECT * FROM benchmark_studies WHERE id=?',(row['study_id'],)).fetchone())
    if inputs['study']!=original_study:raise ValueError('Frozen study identity differs.')
    for identifier,url,kind in zip(inputs['sources'],(ISSUER,DISTRIBUTIONS),('spy_issuer_price','spy_issuer_distributions')):
        source=source_body(db,identifier)
        if source['ticker']!='SPY' or source['source_url']!=url or source['source_kind']!=kind:raise ValueError('Issuer source identity differs.')
    if len(inputs['sources'])!=2:raise ValueError('Exactly two issuer sources required.')
    q=issuer_price(source_body(db,inputs['sources'][0])['body_bytes'],inputs['observed_at'])
    d=issuer_distributions(source_body(db,inputs['sources'][1])['body_bytes'])
    if q!=inputs['issuer_price'] or d!=inputs['distributions']:raise ValueError('Issuer facts differ on original-source reparse.')
    if row['session_date']!=q['session_date'] or inputs['session_date']!=q['session_date'] or result['session_date']!=q['session_date'] or result['close_at']!=q['close_at'] or inputs['close_at']!=q['close_at'] or row['observed_at']!=inputs['observed_at'] or result['observed_at']!=inputs['observed_at']:raise ValueError('Observation date or close differs.')
    if row['session_date']<original_study['first_session'] or instant(row['observed_at'])<instant(q['close_at']):raise ValueError('Observation is not prospective.')
    for r in inputs['ledger']:
        if fingerprint(r['payload'])!=r['digest']:raise ValueError('Frozen ledger fingerprint differs.')
        event=json.loads(r['payload'])
        if event['event_id']!=r['event_id'] or event['occurred_at']!=r['occurred_at']:raise ValueError('Frozen ledger identity/date differs.')
    for source in inputs['source_decisions']:
        if fingerprint(source['input_json'])!=source['sha256']:raise ValueError('Frozen decision fingerprint differs.')
    events=[json.loads(r['payload']) for r in inputs['ledger'] if instant(r['occurred_at'])<=instant(q['close_at'])]
    if events!=inputs['events_at_close']:raise ValueError('Session event selection differs.')
    proofs={p['id']:p for p in inputs['source_decisions']}
    for event in events:
        if event['kind'] not in ('buy','sell','mark'):continue
        try:
            proof=preview_proof(db,event) if event['event_id'].startswith(('forward-paper-','forward-mark-')) else mark_proof(db,event) if event['kind']=='mark' and event['event_id'].startswith('forward-refresh-') else None
            if proof and proofs.get(proof['id'])!=proof:raise ValueError('Native fill/mark source differs from frozen observation.')
        except (ValueError,KeyError,TypeError,ArithmeticError):
            if not result['issues']:raise ValueError('Observation has unverified native execution costs or source evidence.')
    book=replay(events,instant(q['close_at']));nav=Decimal(book['cash'])
    positions=[]
    for symbol,payload in inputs['histories'].items():
        if payload['metadata']['symbol']!=symbol:raise ValueError('Frozen history symbol differs.')
    for p in book['positions']:
        price,_=vendor_price(db,inputs['histories'][p['ticker']],q['session_date']);nav+=Decimal(p['shares'])*price
        positions.append({'ticker':p['ticker'],'shares':p['shares'],'close':str(price)})
    price,_=vendor_price(db,inputs['histories']['SPY'],q['session_date'])
    if abs(price-Decimal(q['price']))>Decimal('.005') or q['price']!=result['benchmark_close'] or str(nav)!=result['nav'] or book['cash']!=result['cash'] or positions!=result['positions']:raise ValueError('Daily NAV/price arithmetic differs on replay.')
    if result['status']!=('blocked' if result['issues'] else 'paper_close_reference') or result['verified_account_return'] is not None or result['verified_benchmark_return'] is not None:raise ValueError('Observation evidence status differs.')
    return inputs,result


def overview(db):
    db.row_factory=sqlite3.Row;study=study_row(db);observations=[];decoded=[];errors=[]
    if study:
        for row in db.execute('SELECT * FROM benchmark_observations WHERE study_id=? ORDER BY session_date',(study['id'],)):
            try:
                i,r=verify_observation(db,row);decoded.append((i,r));observations.append({'id':row['id'],'input_sha256':row['input_sha256'],'result_sha256':row['result_sha256'],**r})
            except (ValueError,KeyError,TypeError,ArithmeticError,ET.ParseError,zipfile.BadZipFile) as exc:errors.append(str(exc))
    portfolio=benchmark=Decimal(1);gaps=[];has_trades=False
    if decoded and decoded[0][1]['session_date']!=study['first_session']:gaps.append('The registered first-close observation is missing; no later baseline is substituted.')
    for (p,pr),(c,cr) in zip(decoded,decoded[1:]):
        if pr['issues'] or cr['issues']:gaps.append('A close lacks complete source/action evidence.');continue
        # Every scheduled close is required; never bridge missing ex-date prices.
        day=date.fromisoformat(pr['session_date'])+timedelta(days=1)
        while day<date.fromisoformat(cr['session_date']):
            if calendar_check(day.isoformat())['state']!='scheduled_closed':gaps.append('A scheduled close is missing.')
            day+=timedelta(days=1)
        old=p['events_at_close'];new=c['events_at_close']
        if new[:len(old)]!=old or any(instant(e['occurred_at'])<=instant(pr['close_at']) for e in new[len(old):]):gaps.append('Backdated or changed interval ledger.');continue
        if any(e['kind'] in ('deposit','withdrawal') for e in new[len(old):]):gaps.append('External funding requires a synchronized boundary valuation for this daily-close series.');continue
        if Decimal(pr['nav'])<=0:gaps.append('Positive starting NAV required.');continue
        distribution={d['ex_date']:d for d in c['distributions']}
        if distribution.get(cr['session_date'],{}).get('date_status','reported_order_consistent')!='reported_order_consistent':gaps.append('Issuer distribution date ordering needs review for this interval.');continue
        cash=Decimal(distribution.get(cr['session_date'],{}).get('declared_cash_per_share','0'))
        vendor=vendor_cash(db,c['histories']['SPY'],cr['session_date'])
        if abs(vendor-cash)>Decimal('.000001'):gaps.append('Issuer and vendor declared distribution amounts disagree.');continue
        portfolio*=Decimal(cr['nav'])/Decimal(pr['nav']);benchmark*=(Decimal(cr['benchmark_close'])+cash)/Decimal(pr['benchmark_close'])
    complete=len(decoded)>=2 and not gaps and not errors
    if decoded:has_trades=any(e['kind'] in ('buy','sell') for e in decoded[-1][0]['events_at_close'])
    return {'study':study,'policy':POLICY,'observations':observations[-30:],'observation_count':len(observations),'integrity_errors':errors,'gaps':gaps,
            'portfolio_reference_return':str(portfolio-1) if complete else None,'benchmark_reference_return':str(benchmark-1) if complete else None,
            'reference_difference':str(portfolio-benchmark) if complete and has_trades else None,'verified_account_return':None,'verified_benchmark_return':None,
            'state':'not_registered' if not study else 'waiting_for_prospective_closes' if len(decoded)<2 else 'blocked' if not complete else 'paper_reference_only' if has_trades else 'cash_only_no_strategy_results',
            'enabled':bool(db.execute('SELECT enabled FROM benchmark_settings WHERE id=1').fetchone()[0]),
            'runs':[dict(r) for r in db.execute('SELECT * FROM benchmark_runs ORDER BY id DESC LIMIT 5')]}


def make_benchmark_router(path):
    router=APIRouter(prefix='/api/benchmark-study',tags=['Prospective synchronized benchmark']);lock=threading.Lock()
    @router.get('')
    def inspect():
        with connect(path) as db:
            db.execute('BEGIN');return overview(db)
    @router.post('/register')
    def start():
        try:return register(path)
        except ValueError as exc:raise HTTPException(422,str(exc))
    @router.get('/observations/{identifier}')
    def export(identifier:int):
        with connect(path) as db:
            row=db.execute('SELECT * FROM benchmark_observations WHERE id=?',(identifier,)).fetchone()
            if not row:raise HTTPException(404,'Close observation not found.')
            try:i,r=verify_observation(db,row)
            except (ValueError,KeyError,TypeError,ArithmeticError,ET.ParseError,zipfile.BadZipFile) as exc:raise HTTPException(409,str(exc))
            return {'id':identifier,'input_sha256':row['input_sha256'],'result_sha256':row['result_sha256'],'inputs':i,'result':r}
    def worker(identifier):
        state='blocked';message='Source collection failed; existing evidence retained.';context=None
        try:
            data=collect(path);state=data['state'];message='Original issuer/vendor sources collected. Earlier context is not a prospective return.';context=encoded(data)
        except ValueError as exc:
            message=str(exc)[:500]
        except Exception:
            pass
        finally:
            try:
                with connect(path) as db:
                    db.execute('UPDATE benchmark_runs SET state=?,finished_at=?,message=?,context_json=? WHERE id=?',(state,now(),message,context,identifier))
                    if state=='blocked':db.execute('UPDATE benchmark_settings SET enabled=0 WHERE id=1')
            finally:lock.release()
    @router.post('/collect')
    def run():
        if not lock.acquire(blocking=False):raise HTTPException(409,'A benchmark source pass is already running.')
        identifier=None
        try:
            with connect(path) as db:
                if not study_row(db):raise HTTPException(409,'Register the prospective study first.')
                identifier=db.execute("INSERT INTO benchmark_runs(started_at,state,message) VALUES(?,'running','Bounded issuer and price-source collection')",(now(),)).lastrowid
                db.execute('UPDATE benchmark_settings SET enabled=1 WHERE id=1')
            threading.Thread(target=worker,args=(identifier,),daemon=True).start();return {'run_id':identifier,'state':'running'}
        except Exception:
            if identifier is not None:
                with connect(path) as db:
                    db.execute("UPDATE benchmark_runs SET state='blocked',finished_at=?,message='Worker could not start.' WHERE id=?",(now(),identifier))
                    db.execute('UPDATE benchmark_settings SET enabled=0 WHERE id=1')
            lock.release();raise
    def scheduled():
        with connect(path) as db:enabled=db.execute('SELECT enabled FROM benchmark_settings WHERE id=1').fetchone()[0]
        if enabled:
            try:run()
            except HTTPException:pass
    router.scheduled_collect=scheduled
    return router
