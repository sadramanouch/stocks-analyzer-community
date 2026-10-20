"""Bounded read-only Flex retrieval. No orders, funding or paper-ledger writes."""
import base64
import json
import os
import re
import shutil
import sqlite3
from threading import Thread
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from ibkr_flex import FlexInput, document, inspect_sources
from research_store import encoded, immutable, instant, now
from forward_performance import fingerprint

METHOD = 'ibkr-flex-service-receipt-v1'
SEND = 'https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest'
PICKUP = {f'https://{host}.interactivebrokers.com{path}'
          for host in ('ndcdyn', 'gdcdyn')
          for path in ('/AccountManagement/FlexWebService/GetStatement', '/Universal/servlet/FlexStatementService.GetStatement')}
GUIDE = 'https://www.interactivebrokers.com/docs/web-api/api-reference/send-request'
LIMITS = 'Reporting access only. TLS retrieval records the delivery path; it is not an independent signature or proof of complete records, investment edge, NBBO quality or profitable execution. Native mappings still require real-export validation. No orders or virtual-ledger changes.'


class ServiceFailure(ValueError):
    """Only locally written messages/codes may reach job diagnostics."""


class ReportingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, 'Invalid reporting request. Credentials belong only in backend secrets; submitted values are not echoed.') from None
        return handler


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opening_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    closing_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    account_label: str = Field(min_length=1, max_length=80)
    export_timezone: str = Field(min_length=1, max_length=64)


def validate_request(body, at):
    start, end = date.fromisoformat(body.opening_date), date.fromisoformat(body.closing_date)
    local_day = instant(at).astimezone(ZoneInfo(body.export_timezone)).date()
    if not body.account_label.strip() or not start < end < local_day or (local_day-start).days > 365:
        raise ValueError('Use a nickname, the actual export timezone and completed prior-day periods within the preceding 365 days, with closing after opening.')
    return start, end


def settings():
    token, query = os.getenv('IBKR_FLEX_TOKEN', ''), os.getenv('IBKR_FLEX_QUERY_ID', '')
    ready = bool(re.fullmatch(r'[A-Za-z0-9_-]{8,256}', token) and re.fullmatch(r'\d{1,20}', query))
    return token, query, ready


def init_connection(path):
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS broker_flex_downloads(
          id TEXT PRIMARY KEY,created_at TEXT NOT NULL,finished_at TEXT,state TEXT NOT NULL,
          request_json TEXT NOT NULL,message TEXT NOT NULL,receipt_id TEXT);
          CREATE TABLE IF NOT EXISTS broker_flex_receipts(
          id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,input_json TEXT NOT NULL,input_sha256 TEXT NOT NULL,
          result_json TEXT NOT NULL,result_sha256 TEXT NOT NULL);''')
        immutable(db, 'broker_flex_receipts')
        db.execute("UPDATE broker_flex_downloads SET state='interrupted',finished_at=?,message='Service restarted; no automatic retry. Review and request again.' WHERE state IN ('queued','running')", (now(),))


def xml_root(raw):
    try:
        root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False))
        if root.getroottree().docinfo.doctype or len(list(root.iter())) > 6000:
            raise ValueError('Unsupported service XML.')
        if any(not isinstance(e.tag, str) or e.tag.startswith('{') for e in root.iter()):
            raise ValueError('Unsupported service XML.')
        return root
    except etree.XMLSyntaxError:
        raise ValueError('IBKR returned unsupported XML; use an Activity Flex XML query.') from None


class Fetcher:
    def __init__(self, client, token, clock=time.monotonic, sleep=time.sleep):
        self.client, self.token, self.clock, self.sleep = client, token, clock, sleep
        self.deadline, self.last, self.count = clock()+120, None, 0

    def fetch(self, url, query, maximum=640000, dates=None):
        if url != SEND and url not in PICKUP:
            raise ServiceFailure('Unapproved IBKR statement endpoint; no credential sent.')
        if self.last is not None:
            self.sleep(max(0, 7-(self.clock()-self.last)))
        if self.clock() >= self.deadline or self.count >= 8:
            raise ServiceFailure('Bounded download deadline/request limit reached. No automatic retry.')
        self.last = self.clock(); self.count += 1
        try:
            params = {'t':self.token, 'q':query, 'v':'3'}
            if dates:
                params.update(fd=dates[0].strftime('%Y%m%d'), td=dates[1].strftime('%Y%m%d'))
            r = self.client.get(url, params=params,
                                headers={'User-Agent':'StockAnalyzer Flex Reporting/1.0', 'Accept':'application/xml'},
                                timeout=(5, 10), allow_redirects=False, stream=True)
            try:
                if r.status_code != 200:
                    raise ServiceFailure(f'IBKR HTTP {r.status_code}; download stopped without redirect or retry.')
                raw = bytearray()
                for chunk in r.iter_content(16384):
                    if self.clock() >= self.deadline or len(raw)+len(chunk) > maximum:
                        raise ServiceFailure('Statement exceeds the bounded size/time limit.')
                    raw.extend(chunk)
                result = bytes(raw)
                if self.token.encode() in result:
                    raise ServiceFailure('Credential-like response rejected; no response retained.')
                return result
            finally:
                r.close()
        except requests.RequestException:
            raise ServiceFailure('IBKR transport failed; credential-bearing request details are withheld. No automatic retry.') from None

    def statement(self, query, begin, end, kind):
        # Dates are explicit overrides of the single preconfigured report template.
        root = xml_root(self.fetch(SEND, query, 32768, dates=(begin,end)))
        if self.token.encode() in etree.tostring(root):
            raise ServiceFailure('Credential-like service XML rejected; no response retained.')
        if root.tag != 'FlexStatementResponse' or root.findtext('Status') != 'Success':
            code = root.findtext('ErrorCode') or ''
            safe = code if re.fullmatch(r'\d{4}',code) else 'unknown'
            raise ServiceFailure(f'IBKR report generation failed (code {safe}); check token, query, permissions and IP restrictions.')
        reference = root.findtext('ReferenceCode') or ''
        url = root.findtext('Url') or root.findtext('url') or ''
        if not re.fullmatch(r'\d{1,30}', reference) or url not in PICKUP:
            raise ServiceFailure('IBKR response reference/endpoint is unsupported; no pickup credential sent.')
        for attempt in range(3):
            raw = self.fetch(url, reference)
            response = xml_root(raw)
            if self.token.encode() in etree.tostring(response):
                raise ServiceFailure('Credential-like service XML rejected; no response retained.')
            if response.tag == 'FlexQueryResponse':
                parsed = document(base64.b64encode(raw).decode())
                if parsed['begin'] != begin or parsed['end'] != end:
                    raise ServiceFailure('Returned report dates differ from the requested interval. No period inferred.')
                return raw, {'kind':kind, 'url':url, 'from_date':begin.isoformat(), 'to_date':end.isoformat(),
                             'received_at':now(), 'body_sha256':parsed['sha256']}
            if response.tag != 'FlexStatementResponse' or response.findtext('ErrorCode') != '1019':
                code = response.findtext('ErrorCode') or ''
                safe = code if re.fullmatch(r'\d{4}',code) else 'unknown'
                raise ServiceFailure(f'IBKR statement retrieval failed (code {safe}); check reporting configuration. No response values retained.')
        raise ServiceFailure('IBKR report is still generating after the bounded pickup attempts. Request again later.')


def collect_receipt(body, token, query, client, clock=time.monotonic, sleep=time.sleep):
    if token in encoded(body.model_dump()):
        raise ServiceFailure('Credential-like request content rejected; use an account nickname.')
    begin, end = validate_request(body, now())
    fetcher = Fetcher(client, token, clock, sleep)
    opening, first = fetcher.statement(query, begin, begin, 'opening')
    activity, second = fetcher.statement(query, begin+timedelta(days=1), end, 'activity')
    if len(opening)+len(activity) > 640000:
        raise ValueError('Paired records exceed the 640 KB supported source bound.')
    at = now()
    native = FlexInput(opening_base64=base64.b64encode(opening).decode(), activity_base64=base64.b64encode(activity).decode(),
                       account_label=body.account_label.strip(), export_timezone=body.export_timezone)
    review = inspect_sources(native, at)
    inputs = {'method':METHOD, 'observed_at':at, 'request':body.model_dump(), 'native':native.model_dump(exclude={'preview_token'}),
              'transport':{'send_url':SEND,'records':[first,second],'request_count':fetcher.count,'redirects_followed':False,
                           'tls_certificate_validation':True, 'independent_signature_verified':False}}
    return inputs, review


def checked_receipt(row):
    if row['id'] != row['input_sha256'] or fingerprint(row['input_json']) != row['input_sha256'] or fingerprint(row['result_json']) != row['result_sha256']:
        raise ValueError('Broker download receipt hash differs.')
    data = json.loads(row['input_json']); body = DownloadRequest.model_validate(data['request'])
    begin, end = validate_request(body, row['observed_at'])
    native = FlexInput.model_validate(data['native'])
    if data['method'] != METHOD or data['observed_at'] != row['observed_at'] or native.account_label != body.account_label.strip() or native.export_timezone != body.export_timezone:
        raise ValueError('Broker receipt identity/profile differs.')
    transport = data['transport']
    if transport['send_url'] != SEND or transport['redirects_followed'] is not False or transport['tls_certificate_validation'] is not True or transport['independent_signature_verified'] is not False or not 4 <= transport['request_count'] <= 8:
        raise ValueError('Broker transport receipt differs.')
    for metadata, raw, kind, start, finish in zip(transport['records'], (native.opening_base64,native.activity_base64), ('opening','activity'), (begin,begin+timedelta(days=1)), (begin,end)):
        parsed = document(raw)
        if metadata['url'] not in PICKUP or metadata['kind'] != kind or metadata['body_sha256'] != parsed['sha256'] or (parsed['begin'],parsed['end']) != (start,finish) or (metadata['from_date'],metadata['to_date']) != (start.isoformat(),finish.isoformat()) or instant(metadata['received_at']) > instant(row['observed_at']):
            raise ValueError('Broker statement custody differs.')
    if len(transport['records']) != 2:
        raise ValueError('Two complete source receipts are required.')
    result = inspect_sources(native, row['observed_at'])
    if encoded(result) != row['result_json']:
        raise ValueError('Broker native review replay differs.')
    return {'id':row['id'],'observed_at':row['observed_at'],'input_sha256':row['input_sha256'],
            'result_sha256':row['result_sha256'],'inputs':data,'result':result,'limitations':LIMITS}


def make_connection_router(path):
    router = APIRouter(prefix='/api/broker-connection', route_class=ReportingRoute, tags=['Read-only IBKR reporting connection'])

    @router.get('')
    def status():
        _, _, ready = settings()
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            jobs = [dict(r) for r in db.execute('SELECT id,created_at,finished_at,state,message,receipt_id FROM broker_flex_downloads ORDER BY rowid DESC LIMIT 10')]
        return {'method':METHOD,'configured':ready,'orders_supported':False,'paper_ledger_changed':False,
                'required_secrets':['IBKR_FLEX_TOKEN','IBKR_FLEX_QUERY_ID'],'jobs':jobs,'guide':GUIDE,'limitations':LIMITS}

    def run(identifier, body, token, query):
        try:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE broker_flex_downloads SET state='running' WHERE id=?", (identifier,))
            with requests.Session() as client:
                client.trust_env = False
                inputs, result = collect_receipt(body, token, query, client)
            raw, output = encoded(inputs), encoded(result); key = fingerprint(raw)
            with sqlite3.connect(path, timeout=30) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO broker_flex_receipts VALUES(?,?,?,?,?,?)',(key,inputs['observed_at'],raw,key,output,fingerprint(output)))
                db.execute("UPDATE broker_flex_downloads SET state='completed',finished_at=?,receipt_id=?,message='Broker-reported XML archived and reviewed. Inspect any reconciliation gaps; no orders or paper changes.' WHERE id=?", (now(),key,identifier))
        except Exception as exc:
            # Never serialize requests exceptions/URLs, IBKR response text or account values.
            message = str(exc) if isinstance(exc, ServiceFailure) else 'Download or source validation failed. Check reporting token/query, dates, XML fields, timezone and IP permissions. No automatic retry or ledger changes.'
            with sqlite3.connect(path, timeout=30) as db:
                db.execute("UPDATE broker_flex_downloads SET state='failed',finished_at=?,message=? WHERE id=?",(now(),message,identifier))

    @router.post('/downloads', status_code=202)
    def start(body: DownloadRequest):
        try:
            at = now(); validate_request(body, at)
            token, query, ready = settings()
            if not ready:
                raise HTTPException(409, 'IBKR reporting credentials are not configured. No broker request sent. Set backend secrets using the setup guide.')
            if token in encoded(body.model_dump()):
                raise HTTPException(422, 'Credential-like request content rejected. Use an account nickname; no request sent.')
            if shutil.disk_usage(Path(path).resolve().parent).free < 260*1024*1024:
                raise HTTPException(409, 'Storage reserve reached; no broker request sent.')
            identifier = uuid.uuid4().hex
            with sqlite3.connect(path, timeout=30) as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT created_at,state,finished_at FROM broker_flex_downloads ORDER BY rowid DESC LIMIT 1').fetchone()
                if previous and (previous[1] in ('queued','running') or (instant(at)-instant(previous[2] or previous[0])).total_seconds() < 120):
                    raise HTTPException(409, 'A download is running or the two-minute service cooldown is active.')
                db.execute('INSERT INTO broker_flex_downloads VALUES(?,?,NULL,?,?,?,NULL)', (identifier,at,'queued',encoded(body.model_dump()),'Bounded read-only statement request queued.'))
            try:
                Thread(target=run,args=(identifier,body,token,query),daemon=True).start()
            except Exception:
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE broker_flex_downloads SET state='failed',finished_at=?,message='Download worker could not start; no automatic retry.' WHERE id=?", (now(),identifier))
                raise HTTPException(503,'Download worker unavailable.') from None
            return {'id':identifier,'paper_ledger_changed':False}
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422,'Invalid completed-date interval, nickname or export timezone.') from None

    @router.get('/receipts/{identifier}')
    def receipt(identifier: str):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM broker_flex_receipts WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise HTTPException(404,'Broker download receipt not found.')
        try:
            return checked_receipt(row)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise HTTPException(409,'Broker source receipt hash or replay failed; values withheld.') from None
    return router
