import base64
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ibkr_connection import (DownloadRequest, Fetcher, SEND, PICKUP, checked_receipt, collect_receipt,
                             init_connection, make_connection_router, settings, validate_request)
from research_store import encoded
from test_ibkr_flex import fixture

AT='2026-10-14T20:00:00+00:00'
TOKEN='synthetic-token-987654321'
URL=sorted(PICKUP)[0]


def generation(url=URL):
    return f'<FlexStatementResponse><Status>Success</Status><ReferenceCode>123456</ReferenceCode><Url>{url}</Url></FlexStatementResponse>'.encode()


class Clock:
    def __init__(self): self.value=0
    def __call__(self): return self.value
    def sleep(self, seconds): self.value+=seconds


class Reply:
    def __init__(self, body, status=200): self.body,self.status_code,self.closed=body,status,False
    def iter_content(self,size):
        for index in range(0,len(self.body),size): yield self.body[index:index+size]
    def close(self): self.closed=True


class Client:
    def __init__(self, replies, clock): self.replies,self.clock,self.calls=replies,clock,[];self.trust_env=True
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs,self.clock()))
        answer=self.replies.pop(0)
        if isinstance(answer,Exception):raise answer
        return answer


class InlineThread:
    def __init__(self,target,args,**kwargs):self.target,self.args=target,args
    def start(self):self.target(*self.args)


class BrokerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'broker.db'
        init_connection(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE paper_ledger_events(event_id TEXT)');db.execute("INSERT INTO paper_ledger_events VALUES('preserve-virtual-funding')")
        self.body=DownloadRequest(opening_date='2026-10-12',closing_date='2026-10-13',account_label='Synthetic fixtures only',export_timezone='UTC')
        self.clock=Clock();opening,activity=fixture()
        self.opening,self.activity=ET.tostring(opening),ET.tostring(activity)
        self.env=patch.dict(os.environ,{'IBKR_FLEX_TOKEN':TOKEN,'IBKR_FLEX_QUERY_ID':'12345'});self.env.start()
        self.now=patch('ibkr_connection.now',return_value=AT);self.now.start()
        app=FastAPI();app.include_router(make_connection_router(self.path));self.api=TestClient(app)
    def tearDown(self):self.now.stop();self.env.stop();self.temp.cleanup()
    def client(self, replies=None):return Client(replies or [Reply(generation()),Reply(self.opening),Reply(generation()),Reply(self.activity)],self.clock)
    def collect(self,client=None):return collect_receipt(self.body,TOKEN,'12345',client or self.client(),self.clock,self.clock.sleep)
    def start(self,client):
        original=collect_receipt
        with patch('ibkr_connection.requests.Session',return_value=client),patch('ibkr_connection.Thread',InlineThread),patch('ibkr_connection.collect_receipt',side_effect=lambda *args:original(*args,clock=self.clock,sleep=self.clock.sleep)):
            return self.api.post('/api/broker-connection/downloads',json=self.body.model_dump())
    def overview(self):return self.api.get('/api/broker-connection').json()

    def test_successful_generation_pickup_and_exact_native_replay(self):
        client=self.client();inputs,result=self.collect(client)
        self.assertTrue(result['arithmetic_reconciled']);self.assertEqual(result['replayed_closing_cash'],'893')
        self.assertIsNone(result['verified_account_return']);self.assertFalse(result['paper_ledger_changed'])
        self.assertEqual(inputs['transport']['request_count'],4)
        self.assertEqual([call[2] for call in client.calls],[0,7,14,21])
        self.assertEqual(client.calls[0][1]['params'],{'t':TOKEN,'q':'12345','v':'3','fd':'20261012','td':'20261012'})
        self.assertEqual(client.calls[2][1]['params']['fd'],'20261013')
        self.assertEqual(client.calls[1][1]['params']['q'],'123456')
        self.assertNotIn('fd',client.calls[1][1]['params'])
        self.assertTrue(all(not call[1]['allow_redirects'] for call in client.calls))
        self.assertNotIn(TOKEN,encoded(inputs));self.assertNotIn('12345',encoded(inputs))

    def test_no_credentials_sends_nothing_and_writes_no_job(self):
        with patch.dict(os.environ,{'IBKR_FLEX_TOKEN':'','IBKR_FLEX_QUERY_ID':''}),patch('ibkr_connection.requests.Session') as network:
            self.assertFalse(self.overview()['configured'])
            response=self.api.post('/api/broker-connection/downloads',json=self.body.model_dump())
            self.assertEqual(response.status_code,409);network.assert_not_called()
        self.assertEqual(self.overview()['jobs'],[])

    def test_explicit_dates_timezone_and_single_native_profile_are_enforced_before_network(self):
        for change in ({'opening_date':'2026-10-13'},{'closing_date':'2026-10-14'},{'opening_date':'2024-01-01'},{'opening_date':'2026-02-30'},{'account_label':' '},{'export_timezone':'not/a/timezone'},{'url':'https://invalid.example'}):
            with self.subTest(change=change),patch('ibkr_connection.requests.Session') as network:
                response=self.api.post('/api/broker-connection/downloads',json={**self.body.model_dump(),**change})
                self.assertEqual(response.status_code,422);network.assert_not_called()
        self.assertEqual(self.overview()['jobs'],[])

    def test_unapproved_pickup_redirect_and_http_denials_stop_without_retry(self):
        for first in (Reply(generation('https://attacker.example/steal')),Reply(generation('http://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement')),Reply(generation(),302),Reply(b'denied',403),Reply(b'rate limited',429)):
            client=self.client([first])
            with self.assertRaises(ValueError):self.collect(client)
            self.assertEqual(len(client.calls),1);self.assertTrue(first.closed)

    def test_get_statement_does_not_follow_redirects(self):
        client=self.client([Reply(generation()),Reply(b'redirect',302)])
        with self.assertRaisesRegex(ValueError,'HTTP 302'):self.collect(client)
        self.assertEqual(len(client.calls),2)

    def test_only_generation_in_progress_is_polled_and_attempts_are_bounded(self):
        pending=b'<FlexStatementResponse><Status>Fail</Status><ErrorCode>1019</ErrorCode></FlexStatementResponse>'
        client=self.client([Reply(generation()),Reply(pending),Reply(self.opening),Reply(generation()),Reply(self.activity)])
        self.assertEqual(self.collect(client)[0]['transport']['request_count'],5)
        self.clock=Clock();client=self.client([Reply(generation()),Reply(pending),Reply(pending),Reply(pending)])
        with self.assertRaisesRegex(ValueError,'still generating'):self.collect(client)
        self.assertEqual(len(client.calls),4)
        failed=b'<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode></FlexStatementResponse>'
        client=self.client([Reply(failed)])
        with self.assertRaisesRegex(ValueError,'1012'):self.collect(client)
        self.assertEqual(len(client.calls),1)

    def test_wrong_returned_range_and_mixed_accounts_never_produce_a_receipt(self):
        opening,activity=fixture();opening.find('.//FlexStatement').set('fromDate','20261011');opening.find('.//FlexStatement').set('toDate','20261011')
        with self.assertRaisesRegex(ValueError,'dates differ'):
            self.collect(self.client([Reply(generation()),Reply(ET.tostring(opening))]))
        activity.find('.//FlexStatement').set('accountId','ANOTHER-ACCOUNT')
        with self.assertRaises(ValueError):self.collect(self.client([Reply(generation()),Reply(self.opening),Reply(generation()),Reply(ET.tostring(activity))]))

    def test_xml_entity_credential_echo_size_and_stream_deadline_are_rejected(self):
        for content in (b'<!DOCTYPE x [<!ENTITY bad SYSTEM "file:///etc/passwd">]><FlexStatementResponse>&bad;</FlexStatementResponse>',TOKEN.encode(),b'x'*32769,b'<not-closed>'):
            with self.assertRaises(ValueError):self.collect(self.client([Reply(content)]))
        with self.assertRaisesRegex(ValueError,'size/time'):
            self.collect(self.client([Reply(generation()),Reply(b'x'*640001)]))
        fetcher=Fetcher(self.client(),TOKEN,self.clock,self.clock.sleep);self.clock.value+=121
        with self.assertRaisesRegex(ValueError,'deadline'):fetcher.fetch(SEND,'12345')

    def test_successful_job_saves_only_isolated_replayable_immutable_receipt(self):
        client=self.client();response=self.start(client);self.assertEqual(response.status_code,202)
        job=self.overview()['jobs'][0];self.assertEqual(job['state'],'completed');self.assertFalse(client.trust_env)
        saved=self.api.get('/api/broker-connection/receipts/'+job['receipt_id']);self.assertEqual(saved.status_code,200)
        self.assertTrue(saved.json()['result']['arithmetic_reconciled']);self.assertNotIn(TOKEN,saved.text)
        self.assertEqual(base64.b64decode(saved.json()['inputs']['native']['opening_base64']),self.opening)
        self.assertFalse(self.overview()['orders_supported'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT * FROM paper_ledger_events').fetchall(),[('preserve-virtual-funding',)])
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE broker_flex_receipts SET result_json='{}'")
        self.assertEqual(self.api.post('/api/broker-connection/downloads',json=self.body.model_dump()).status_code,409)

    def test_failed_transport_hides_credential_urls_and_saves_no_partial_sources(self):
        client=self.client([requests.ConnectionError('https://secret.example?t='+TOKEN)])
        self.assertEqual(self.start(client).status_code,202)
        data=self.overview();self.assertEqual(data['jobs'][0]['state'],'failed');self.assertNotIn(TOKEN,json.dumps(data));self.assertNotIn('secret.example',json.dumps(data))
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM broker_flex_receipts').fetchone()[0],0)

    def test_source_corruption_is_withheld_and_reading_makes_no_external_calls(self):
        self.start(self.client());identifier=self.overview()['jobs'][0]['receipt_id']
        with patch('ibkr_connection.requests.Session') as network:
            self.assertEqual(self.api.get('/api/broker-connection/receipts/'+identifier).status_code,200);network.assert_not_called()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER immutable_broker_flex_receipts_UPDATE');db.execute("UPDATE broker_flex_receipts SET input_json='{}'")
        self.assertEqual(self.api.get('/api/broker-connection/receipts/'+identifier).status_code,409)
        self.assertEqual(self.api.get('/api/broker-connection/receipts/missing').status_code,404)

    def test_restart_marks_only_interrupted_jobs_without_resume_or_source_calls(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO broker_flex_downloads VALUES('unfinished',?,NULL,'running','{}','queued',NULL)",(AT,))
        with patch('ibkr_connection.requests.Session') as network:init_connection(self.path);network.assert_not_called()
        self.assertEqual(self.overview()['jobs'][0]['state'],'interrupted')

    def test_reconciled_delivery_does_not_hide_missing_economic_records(self):
        _,activity=fixture();activity.find('.//CashTransactions').remove(activity.find('.//CashTransactions')[-1])
        _,review=self.collect(self.client([Reply(generation()),Reply(self.opening),Reply(generation()),Reply(ET.tostring(activity))]))
        self.assertFalse(review['arithmetic_reconciled']);self.assertIsNone(review['verified_account_return'])

    def test_accidental_credentials_in_request_are_not_echoed_or_retained(self):
        for change in ({'token':TOKEN},{'account_label':TOKEN}):
            with patch('ibkr_connection.requests.Session') as network:
                r=self.api.post('/api/broker-connection/downloads',json={**self.body.model_dump(),**change})
                self.assertEqual(r.status_code,422);self.assertNotIn(TOKEN,r.text);network.assert_not_called()
        self.assertEqual(self.overview()['jobs'],[])

    def test_request_count_and_combined_source_size_limits(self):
        client=self.client([Reply(b'ok') for _ in range(8)])
        fetcher=Fetcher(client,TOKEN,self.clock,self.clock.sleep)
        for _ in range(8):fetcher.fetch(SEND,'12345')
        with self.assertRaisesRegex(ValueError,'request limit'):fetcher.fetch(SEND,'12345')
        self.assertEqual(len(client.calls),8)
        sources=[raw.replace(b'</FlexQueryResponse>',b' '*320000+b'</FlexQueryResponse>') for raw in (self.opening,self.activity)]
        with self.assertRaisesRegex(ValueError,'Paired records'):
            self.collect(self.client([Reply(generation()),Reply(sources[0]),Reply(generation()),Reply(sources[1])]))

    def test_failure_after_opening_and_atomic_archive_failure_save_no_partial_receipt(self):
        client=self.client([Reply(generation()),Reply(self.opening),Reply(b'denied',403)])
        self.start(client);self.assertEqual(self.overview()['jobs'][0]['state'],'failed')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM broker_flex_receipts').fetchone()[0],0)
            db.execute('DELETE FROM broker_flex_downloads')
            db.execute("CREATE TRIGGER prevent_completion BEFORE UPDATE ON broker_flex_downloads WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'Synthetic rollback test'); END")
        self.start(self.client());self.assertEqual(self.overview()['jobs'][0]['state'],'failed')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM broker_flex_receipts').fetchone()[0],0)

    def test_cooldown_is_measured_from_completion_even_for_a_long_running_job(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO broker_flex_downloads VALUES('prior','2026-10-14T19:50:00Z','2026-10-14T19:59:30Z','completed','{}','done',NULL)")
        with patch('ibkr_connection.requests.Session') as network:
            self.assertEqual(self.api.post('/api/broker-connection/downloads',json=self.body.model_dump()).status_code,409);network.assert_not_called()

    def test_receipt_semantics_and_native_replay_are_checked_beyond_checksums(self):
        from forward_performance import fingerprint
        self.start(self.client())
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row;original=dict(db.execute('SELECT * FROM broker_flex_receipts').fetchone())
        edited=dict(original);data=json.loads(edited['input_json']);data['transport']['records'][0]['url']='https://attacker.example/'
        edited['input_json']=encoded(data);edited['id']=edited['input_sha256']=fingerprint(edited['input_json'])
        with self.assertRaisesRegex(ValueError,'custody'):checked_receipt(edited)
        edited=dict(original);data=json.loads(edited['result_json']);data['replayed_closing_cash']='9999';edited['result_json']=encoded(data);edited['result_sha256']=fingerprint(edited['result_json'])
        with self.assertRaisesRegex(ValueError,'replay'):checked_receipt(edited)
