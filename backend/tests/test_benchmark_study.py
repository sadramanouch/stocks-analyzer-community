import io,json,sqlite3,unittest,zipfile
from datetime import datetime,date,time,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch,Mock
from xml.etree import ElementTree as ET
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_equity_workspace as workspace_fixture
from benchmark_study import (init_benchmark,register,collect,overview,first_session,close_at,issuer_price,issuer_distributions,make_benchmark_router,ISSUER,POLICY)
from equity_workspace import POLICY as PAPER_POLICY
from market_provenance import save_source
from market_history import init_market_history
from research_store import encoded
from forward_performance import fingerprint

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

def page(day,price):
    label=datetime.strptime(day,'%Y-%m-%d').strftime('%b %d %Y')
    return f'<html><h1>SPY Fixture ETF</h1><section><h2>Listing Information</h2><table><tr><td>NYSE ARCA</td><td>Jan 22 1993</td><td>USD</td><td>SPY</td><td>78462F103</td><td>US78462F1030</td></tr></table></section><section><h2>Fund Market Price as of {label}</h2><table><tr><th>Closing Price</th><td>${price:.2f}</td></tr></table></section></html>'.encode()

def workbook(ex='10/15/2026',cash='1',record=None,payable='10/30/2026'):
    headers=['FUND NAME','TICKER','CUSIP','EX-DATE','RECORD DATE','PAYABLE DATE','DIVIDEND ($)','SHORT TERM CAPITAL GAIN ($)','LONG TERM CAPITAL GAIN ($)','FREQUENCY']
    data=[headers,['Fixture ETF','SPY','78462F103',ex,record or ex,payable,cash,'','','Quarterly']]
    strings=[];sheet=ET.Element('worksheet',xmlns=NS);rows=ET.SubElement(sheet,'sheetData')
    for n,values in enumerate(data,1):
        row=ET.SubElement(rows,'row',r=str(n))
        for i,value in enumerate(values):
            c=ET.SubElement(row,'c',r=f'{chr(65+i)}{n}',t='s');strings.append(value);ET.SubElement(c,'v').text=str(len(strings)-1)
    root=ET.Element('sst',xmlns=NS)
    for value in strings:ET.SubElement(ET.SubElement(root,'si'),'t').text=value
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}"><sheets><sheet name="dividend" sheetId="1"/></sheets></workbook>')
        z.writestr('xl/sharedStrings.xml',ET.tostring(root));z.writestr('xl/worksheets/sheet1.xml',ET.tostring(sheet))
    return f.getvalue()


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.fx=workspace_fixture.EquityWorkspaceTests();self.fx.setUp();self.path=self.fx.path;init_market_history(self.path);init_benchmark(self.path)
        self.day='2026-10-14';self.close=100;self.dividend=0;self.workbook=workbook()
        event={'event_id':'capital','kind':'deposit','currency':'USD','amount':'10000','occurred_at':'2026-10-14T16:00:00+00:00','source':'Explicit virtual fixture capital'}
        self.store_event(event)
        register(self.path,'2026-10-14T17:00:00+00:00')
        app=FastAPI();app.include_router(make_benchmark_router(self.path));self.client=TestClient(app)

    def tearDown(self):self.fx.tearDown()
    def store_event(self,e):
        raw=encoded(e)
        with sqlite3.connect(self.path) as db:db.execute('INSERT INTO paper_ledger_events VALUES(?,?,?,?,?)',(e['event_id'],e['occurred_at'],e['occurred_at'],raw,fingerprint(raw)))
    def fetch(self,url,**kwargs):return SimpleNamespace(status_code=200,content=page(self.day,self.close) if url==ISSUER else self.workbook,raise_for_status=lambda:None)
    def history(self,ticker,start,end,at):
        stamp=int(close_at(self.day).timestamp());price=self.close if ticker=='SPY' else 102
        meta={'symbol':ticker,'currency':'USD','exchangeTimezoneName':'America/New_York','instrumentType':'ETF' if ticker=='SPY' else 'EQUITY'}
        events={'dividends':{'1':{'date':stamp,'amount':self.dividend}}} if ticker=='SPY' and self.dividend else {}
        raw={'chart':{'result':[{'meta':meta,'timestamp':[stamp],'indicators':{'quote':[{'open':[price],'high':[price],'low':[price],'close':[price]}],'adjclose':[{'adjclose':[price]}]},'events':events}],'error':None}}
        body=json.dumps(raw).encode();source=save_source(self.path,ticker,'https://query2.finance.yahoo.com/v8/finance/chart/'+ticker,'yahoo_original_chart',200,{},body,at)
        return {'schema':'yahoo-reference-history-v1','price_basis':'split_adjusted','adjusted_close_basis':'vendor_total_return_proxy','metadata':meta,
                'bars':[{'date':self.day,'open':price,'high':price,'low':price,'close':price,'adjusted_close':price,'dividend':self.dividend if ticker=='SPY' else 0,'split_ratio':0}],
                'omitted_price_dates':[],'original_source_document_id':source}
    def collect(self):return collect(self.path,self.fetch,self.history,self.day+'T23:00:00+00:00')
    def state(self):
        with sqlite3.connect(self.path) as db:return overview(db)
    def native_buy(self):
        e={'event_id':'forward-paper-test','kind':'buy','currency':'USD','ticker':'TEST','shares':'1','price':'100.100000','fee':'0.10','quote_at':'2026-10-15T16:59:00+00:00','occurred_at':'2026-10-15T17:00:00+00:00','price_basis':'unadjusted','source':'Explicit native fixture preview'}
        data={'policy':PAPER_POLICY,'research':{'price':100,'quote_currency':'USD','quote_as_of':e['quote_at']}}
        raw=encoded(data)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO equity_decisions VALUES(?,?,?,?,?,?)',('decision',e['occurred_at'],'TEST','paper_preview',raw,fingerprint(raw)))
            db.execute('INSERT INTO equity_paper_previews VALUES(?,?,?,?,?)',('test','2026-10-15T17:10:00+00:00','decision',encoded([e]),'fixture'))
        self.store_event(e)

    def test_calendar_early_close_weekend_and_unknown_year(self):
        self.assertEqual(close_at('2026-11-27').isoformat(),'2026-11-27T18:00:00+00:00')
        self.assertEqual(first_session('2026-10-16T20:01:00+00:00'),'2026-10-19')
        with self.assertRaises(ValueError):first_session('2027-01-01T00:00:00+00:00')
        with self.assertRaises(ValueError):close_at('2026-10-17')

    def test_source_parser_checks_exact_identifier_dates_price_and_distribution(self):
        self.assertEqual(issuer_price(page(self.day,100),self.day+'T23:00:00+00:00')['price'],'100.00')
        self.assertEqual(issuer_distributions(self.workbook)[0]['declared_cash_per_share'],'1')
        with self.assertRaises(ValueError):issuer_price(page(self.day,100).replace(b'78462F103',b'000000000'),self.day+'T23:00:00+00:00')
        with self.assertRaises(ValueError):issuer_price(page(self.day,100),self.day+'T19:00:00+00:00')

    def test_prior_close_is_context_only_and_single_point_has_no_return(self):
        self.day='2026-10-13';r=self.collect();self.assertEqual(r['state'],'waiting_for_prospective_close');self.assertEqual(self.state()['observation_count'],0)
        self.day='2026-10-14';self.collect();self.assertIsNone(self.state()['benchmark_reference_return'])

    def test_cash_only_benchmark_distribution_and_trade_costs(self):
        self.collect();self.native_buy();self.day='2026-10-15';self.close=101;self.dividend=1;self.collect();s=self.state()
        self.assertEqual(Decimal(s['benchmark_reference_return']),Decimal('.02'));self.assertEqual(Decimal(s['portfolio_reference_return']),Decimal('.00018'))
        self.assertEqual(Decimal(s['reference_difference']),Decimal('-.01982'));self.assertIsNone(s['verified_account_return']);self.assertIsNone(s['verified_benchmark_return'])
        self.assertEqual(self.client.get('/api/benchmark-study/observations/2').status_code,200)

    def test_cash_only_series_does_not_claim_strategy_difference(self):
        self.collect();self.day='2026-10-15';self.close=101;self.dividend=1;self.collect()
        self.assertEqual(self.state()['state'],'cash_only_no_strategy_results');self.assertIsNone(self.state()['reference_difference'])

    def test_missing_registered_baseline_and_weekday_gap_block(self):
        self.day='2026-10-15';self.collect();self.day='2026-10-19';self.collect();s=self.state();self.assertIsNone(s['benchmark_reference_return']);self.assertGreaterEqual(len(s['gaps']),2)

    def test_conflicting_issuer_vendor_cash_blocks_comparison(self):
        self.collect();self.day='2026-10-15';self.close=101;self.dividend=2;self.collect();self.assertIsNone(self.state()['benchmark_reference_return'])
        self.assertIn('distribution',' '.join(self.state()['gaps']))

    def test_backdated_records_and_external_flows_do_not_become_returns(self):
        self.collect();e={'event_id':'extra','kind':'deposit','currency':'USD','amount':'100','occurred_at':'2026-10-15T15:00:00+00:00','source':'Fixture flow'};self.store_event(e)
        self.day='2026-10-15';self.collect();self.assertIsNone(self.state()['portfolio_reference_return']);self.assertIn('funding',' '.join(self.state()['gaps']))

    def test_idempotent_close_and_immutable_original_source(self):
        r=self.collect();self.assertEqual(self.collect()['state'],'already_observed')
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM benchmark_observations')
            db.execute('DROP TRIGGER immutable_market_source_documents_UPDATE');db.execute("UPDATE market_source_documents SET sha256='bad' WHERE source_kind='spy_issuer_price'")
        self.assertEqual(self.client.get('/api/benchmark-study/observations/'+str(r['observation_id'])).status_code,409)

    def test_register_idempotent_and_worker_failure_is_retryable(self):
        self.assertFalse(register(self.path,'2026-10-14T17:01:00+00:00')['created'])
        thread=Mock();thread.start.side_effect=RuntimeError('Fixture worker start failure')
        client=TestClient(self.client.app,raise_server_exceptions=False)
        with patch('benchmark_study.threading.Thread',return_value=thread):
            self.assertEqual(client.post('/api/benchmark-study/collect').status_code,500)
            self.assertEqual(client.post('/api/benchmark-study/collect').status_code,500)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT enabled FROM benchmark_settings').fetchone()[0],0)

    def test_issuer_anomaly_is_preserved_and_blocks_affected_interval(self):
        self.workbook=workbook(record='10/20/2026',payable='10/15/2026')
        row=issuer_distributions(self.workbook)[0]
        self.assertEqual(row['payable_date'],'2026-10-15');self.assertEqual(row['date_status'],'issuer_date_order_requires_review')
        self.collect();self.day='2026-10-15';self.close=101;self.dividend=1;self.collect()
        self.assertIsNone(self.state()['benchmark_reference_return']);self.assertIn('ordering',' '.join(self.state()['gaps']))

    def test_changed_result_with_new_fingerprint_is_independently_rejected(self):
        self.collect()
        with sqlite3.connect(self.path) as db:
            result=json.loads(db.execute('SELECT result_json FROM benchmark_observations').fetchone()[0]);result['benchmark_close']='999'
            output=encoded(result);db.execute('DROP TRIGGER immutable_benchmark_observations_UPDATE')
            db.execute('UPDATE benchmark_observations SET result_json=?,result_sha256=?',(output,fingerprint(output)))
        self.assertEqual(self.client.get('/api/benchmark-study/observations/1').status_code,409)
        self.assertTrue(self.state()['integrity_errors']);self.assertEqual(self.state()['observations'],[])
