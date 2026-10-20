"""Original response custody and a bounded, explicitly dated exchange calendar."""
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from research_store import encoded, immutable, now

CALENDAR = {
    'version': 'nyse-regular-calendar-2026-v1',
    'source_url': 'https://www.nyse.com/trade/hours-calendars',
    'year': 2026,
    'scope': 'NYSE scheduled regular sessions; literal record dates are not verified quote dates or execution times',
    'holidays': {
        '2026-01-01': "New Year's Day", '2026-01-19': 'Martin Luther King Jr. Day',
        '2026-02-16': "Washington's Birthday", '2026-04-03': 'Good Friday',
        '2026-05-25': 'Memorial Day', '2026-06-19': 'Juneteenth',
        '2026-07-03': 'Independence Day observed', '2026-09-07': 'Labor Day',
        '2026-11-26': 'Thanksgiving', '2026-12-25': 'Christmas',
    },
}


def calendar_check(raw):
    try:day=date.fromisoformat(raw[:10])
    except (ValueError, TypeError):return {'state':'invalid_date'}
    state='unknown_year'
    reason='Published calendar coverage is limited to 2026.'
    if day.year==CALENDAR['year']:
        reason=CALENDAR['holidays'].get(day.isoformat())
        if not reason and day.weekday()>4:reason='Weekend'
        state='scheduled_closed' if reason else 'scheduled_session'
    return {'date':day.isoformat(),'state':state,'reason':reason, 'source_url':CALENDAR['source_url'],
            'calendar_version':CALENDAR['version'],'scope':CALENDAR['scope']}


def init_sources(path):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS market_source_documents (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, observed_at TEXT NOT NULL,
            source_url TEXT NOT NULL, source_kind TEXT NOT NULL, status_code INTEGER NOT NULL,
            request_json TEXT NOT NULL, sha256 TEXT NOT NULL, body_bytes BLOB NOT NULL)''')
        immutable(db,'market_source_documents')


def save_source(path,ticker,url,kind,status,request,body,at=None):
    if not isinstance(body,bytes) or len(body)>2_000_000:raise ValueError('Source response size/type unsupported')
    at=at or now();sha=hashlib.sha256(body).hexdigest();raw=encoded(request)
    source_id=hashlib.sha256(encoded([ticker,url,kind,status,raw,sha]).encode()).hexdigest()
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO market_source_documents VALUES(?,?,?,?,?,?,?,?,?)',
                   (source_id,ticker,at,url,kind,status,raw,sha,body))
    return source_id


def source_references(db):
    db.row_factory=sqlite3.Row
    result={}
    for row in db.execute('SELECT * FROM market_source_documents ORDER BY observed_at,id'):
        d=dict(row)
        if hashlib.sha256(d.pop('body_bytes')).hexdigest()!=d['sha256']:raise ValueError('Original response hash mismatch')
        result[d['source_url']]={k:d[k] for k in ('id','observed_at','sha256','status_code','source_kind')}
    return result


def fetch_original_chart(ticker,start,end,client,path,at):
    """Request the historical interval without depending on a current quote timezone.

    NY only forms request bounds. The response's actual timezone, identity,
    currency and values still must pass the ordinary history validator.
    """
    ny=ZoneInfo('America/New_York')
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    params={'period1':int(datetime.combine(first,datetime.min.time(),ny).timestamp()),
            'period2':int(datetime.combine(last+timedelta(days=1),datetime.min.time(),ny).timestamp()),
            'interval':'1d','includePrePost':False,'events':'div,splits,capitalGains'}
    url='https://query2.finance.yahoo.com/v8/finance/chart/'+ticker
    response=client.get(url,params=params,timeout=20)
    source_id=save_source(path,ticker,url,'yahoo_original_chart',response.status_code,params,response.content,at)
    if response.status_code in (403,429,503):
        from yfinance.exceptions import YFRateLimitError
        raise YFRateLimitError()
    response.raise_for_status()
    return parse_original_chart(response.content,ticker,start,end,source_id)


def parse_original_chart(body,ticker,start,end,source_id):
    """Reproduce normalized bars from retained bytes without another network call."""
    ny=ZoneInfo('America/New_York')
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    raw=json.loads(body)
    chart=raw.get('chart',{});results=chart.get('result')
    if chart.get('error') or not isinstance(results,list) or len(results)!=1:raise ValueError('Historical source has no unambiguous result')
    data=results[0];meta=data.get('meta',{})
    if meta.get('exchangeTimezoneName')!='America/New_York':raise ValueError('Original chart exchange timezone mismatch')
    stamps=data.get('timestamp',[]);ind=data.get('indicators',{});quotes=ind.get('quote',[]);adjusted=ind.get('adjclose',[])
    if not isinstance(stamps,list) or not stamps or len(stamps)>800 or len(quotes)!=1 or len(adjusted)!=1:raise ValueError('Original chart price schema unsupported')
    q=quotes[0];a=adjusted[0].get('adjclose',[])
    if any(len(q.get(k,[]))!=len(stamps) for k in ('open','high','low','close')) or len(a)!=len(stamps):raise ValueError('Original chart arrays disagree')
    def day(stamp):
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)):raise ValueError('Invalid source timestamp')
        return datetime.fromtimestamp(stamp,timezone.utc).astimezone(ny).date().isoformat()
    actions=data.get('events',{});dividends={};splits={}
    for e in actions.get('dividends',{}).values():dividends[day(e['date'])]=e['amount']
    for e in actions.get('splits',{}).values():
        n,d=Decimal(str(e['numerator'])),Decimal(str(e['denominator']))
        if not n.is_finite() or not d.is_finite() or n<=0 or d<=0:raise ValueError('Invalid source split ratio')
        splits[day(e['date'])]=float(n/d)
    bars=[];omitted=[]
    for i,stamp in enumerate(stamps):
        current=day(stamp);values=[q[k][i] for k in ('open','high','low','close')]+[a[i]]
        if any(v is None for v in values):omitted.append(current);continue
        bars.append({'date':current,**dict(zip(('open','high','low','close','adjusted_close'),values)),
                     'dividend':dividends.get(current,0),'split_ratio':splits.get(current,0)})
    valid_days={b['date'] for b in bars}
    if any(first.isoformat()<=d<=last.isoformat() and d not in valid_days for d in set(dividends)|set(splits)):
        raise ValueError('A source action has no usable price bar; do not silently drop the action')
    return {'schema':'yahoo-reference-history-v1','price_basis':'split_adjusted','adjusted_close_basis':'vendor_total_return_proxy',
            'metadata':{k:meta.get(k) for k in ('symbol','currency','instrumentType','exchangeTimezoneName','longName','shortName')},
            'bars':bars,'omitted_price_dates':omitted,'original_source_document_id':source_id,
            'processing':'Parsed from archived original Yahoo chart bytes. Daily OHLC remains vendor-adjusted reference data, not broker fills.'}
