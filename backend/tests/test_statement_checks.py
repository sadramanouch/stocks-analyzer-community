import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from lxml import etree
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_financial_context as fixture
from research_store import encoded
from statement_checks import (Capture, DEFINITIONS, METHOD, build, collect, comparison,
    filing_reference, init_statements, make_statement_router, numeric, parse_statement,
    replay, save_source, scope, sha)

AT = fixture.AT
CIK = '0000000001'
FILING = {'form':'10-K','accessionNumber':'0000000001-26-000001','filingDate':'2026-02-01',
          'primaryDocument':'annual.htm','reportDate':'2025-12-31',
          'url':'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/annual.htm'}


def nonnumeric(name, value):
    return f'<ix:nonNumeric name="dei:{name}" contextRef="c2025">{value}</ix:nonNumeric>'


def number(concept, value, year=2025, **attrs):
    data={'name':'us-gaap:'+concept,'contextRef':f'c{year}','unitRef':'USD','decimals':'0','scale':'0'}
    if value < 0: data['sign']='-'
    data.update(attrs)
    return '<ix:nonFraction '+' '.join(f'{k}="{v}"' for k,v in data.items())+'>'+str(abs(value))+'</ix:nonFraction>'


def document(overrides=None):
    overrides=overrides or {}
    contexts=''.join(f'<xbrli:context id="c{y}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{y}-01-01</xbrli:startDate><xbrli:endDate>{y}-12-31</xbrli:endDate></xbrli:period></xbrli:context>' for y in (2025,2024,2023))
    rows={'NetCashProvidedByUsedInOperatingActivities':('Total operating cash flow',200),
          'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations':('Continuing OCF',210),
          'CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations':('Discontinued OCF',-10),
          'PaymentsToAcquirePropertyPlantAndEquipment':('Property purchases',20),
          'PaymentsToAcquireProductiveAssets':('Capital expenditures',35),
          'PaymentsToAcquireOtherProductiveAssets':('Capital Spending',40),
          'NetCashProvidedByUsedInInvestingActivities':('Investing cash flow',-100),
          'NetCashProvidedByUsedInFinancingActivities':('Financing cash flow',-50),
          'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect':('Cash change',50)}
    rows.update(overrides)
    cells=''.join('<tr><td>'+label+'</td>'+''.join('<td>'+number(concept,value,y)+'</td>' for y in (2025,2024,2023))+'</tr>' for concept,(label,value) in rows.items() if value is not None)
    return ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">'+contexts+'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'+nonnumeric('EntityCentralIndexKey',CIK)+nonnumeric('DocumentType','10-K')+'<table><tr><td>'+nonnumeric('Security12bTitle','Common Stock')+'</td><td>'+nonnumeric('TradingSymbol','TEST')+'</td><td>'+nonnumeric('SecurityExchangeName','NYSE')+'</td></tr></table><table>'+cells+'</table></html>').encode()


class StatementChecksTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.FinancialContextTests();self.fx.setUp();self.path=self.fx.path
        init_statements(self.path)
        self.fx.item['identity']={'ticker':'TEST','exchange':'N','security_type':'CS'}
        for key,value in (('productive_asset_payments',35),('other_productive_asset_payments',40),('continuing_operating_cf',210),('discontinued_operating_cf',-10)):
            self.fx.payload['facts']['us-gaap'][DEFINITIONS[key][0]]={'units':{'USD':[dict(self.fx.fact(y),val=value) for y in (2025,2024,2023)]}}
        self.fx.publish()
        with self.db() as db:
            db.executescript('CREATE TABLE saved_list_versions(id INTEGER PRIMARY KEY); INSERT INTO saved_list_versions VALUES(33); CREATE TABLE saved_list_items(version_id INTEGER,ticker TEXT,payload_json TEXT); CREATE TABLE identity_source_documents(observed_at TEXT,status_code INTEGER);')
            db.execute('INSERT INTO saved_list_items VALUES(?,?,?)',(33,'TEST',encoded(self.fx.item)))
        self.index={'cik':1,'tickers':['TEST'],'filings':{'recent':{k:[FILING[k]] for k in ('form','accessionNumber','filingDate','primaryDocument','reportDate')}}}
        self.body=document()
        self.source_ids={'submissions':save_source(self.path,CIK,f'https://data.sec.gov/submissions/CIK{CIK}.json',encoded(self.index).encode(),200,fixture.OBS),
                         'filing':save_source(self.path,CIK,FILING['url'],self.body,200,fixture.OBS)}
        app=FastAPI();app.include_router(make_statement_router(self.path,lambda:{'User-Agent':'fixture contact'}));self.client=TestClient(app)

    def tearDown(self): self.fx.tearDown()
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;return db
    def report(self,body=None,payload=None):
        return build(self.fx.item,33,self.fx.context(),payload or self.fx.payload,body or self.body,FILING,[],AT)
    def request(self):
        with self.db() as db: inputs=scope(db,'TEST',33)[2]
        return Capture(research_version=33,scope_sha256=sha(encoded(inputs)),request_id='a'*32)
    def capture(self):
        with patch('statement_checks.now',return_value=AT),patch('statement_checks.requests.get') as get:
            out=collect(self.path,'TEST',self.request(),{'User-Agent':'fixture'})
            get.assert_not_called();return out

    def test_three_annual_periods_and_distinct_cash_scopes(self):
        report=self.report();self.assertEqual(report['status'],'statement_cross_checks_available')
        self.assertEqual(report['coverage']['matched_original_facts'],18)
        p=report['periods'][0]
        self.assertEqual(p['cash_measures']['cash_after_ppe_payments']['value'],'180')
        self.assertEqual(p['cash_measures']['cash_after_productive_asset_payments']['value'],'165')
        self.assertEqual(p['cash_measures']['cash_after_other_productive_asset_payments']['value'],'160')
        self.assertEqual(p['operating_component_reconciliation'],'matched')
        self.assertEqual(p['facts']['other_productive_asset_payments']['row_label'],'Capital Spending')
        self.assertFalse(report['legacy_capex_replaced']);self.assertFalse(report['profitability_verified'])

    def test_missing_ppe_does_not_inherit_broader_payments(self):
        body=document({'PaymentsToAcquirePropertyPlantAndEquipment':('Property purchases',None)})
        p=self.report(body)['periods'][0]
        self.assertEqual(p['facts']['ppe_payments']['status'],'unavailable')
        self.assertIsNone(p['cash_measures']['cash_after_ppe_payments']['value'])
        self.assertEqual(p['cash_measures']['cash_after_other_productive_asset_payments']['value'],'160')

    def test_explicit_zero_and_negative_operating_cash_flow_remain_values(self):
        for value in (0,-200):
            payload=copy.deepcopy(self.fx.payload)
            for f in payload['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD']:f['val']=value
            body=document({'NetCashProvidedByUsedInOperatingActivities':('Total operating cash flow',value),'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations':('Continuing OCF',None),'CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations':('Discontinued OCF',None)})
            p=self.report(body,payload)['periods'][0]
            self.assertEqual(p['cash_measures']['cash_after_productive_asset_payments']['value'],str(value-35))

    def test_continuing_ocf_cannot_replace_missing_total(self):
        p=self.report(document({'NetCashProvidedByUsedInOperatingActivities':('Total operating cash flow',None)}))
        self.assertEqual(p['status'],'requires_review')  # total OCF is required to identify this supported table
        self.assertFalse(p['periods'])

    def test_contradictory_components_withhold_derived_cash(self):
        body=document({'CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations':('Discontinued OCF',-20)})
        payload=copy.deepcopy(self.fx.payload)
        for f in payload['facts']['us-gaap']['CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations']['units']['USD']:f['val']=-20
        p=self.report(body,payload)['periods'][0]
        self.assertEqual(p['operating_component_reconciliation'],'source_mismatch')
        self.assertTrue(all(m['value'] is None for m in p['cash_measures'].values()))

    def test_negative_payment_is_not_absolutized(self):
        payload=copy.deepcopy(self.fx.payload)
        for f in payload['facts']['us-gaap']['PaymentsToAcquireProductiveAssets']['units']['USD']:f['val']=-35
        p=self.report(document({'PaymentsToAcquireProductiveAssets':('Capital expenditures',-35)}),payload)['periods'][0]
        self.assertEqual(p['facts']['productive_asset_payments']['status'],'requires_review')
        self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'])

    def test_numeric_scale_precision_sign_and_registered_transform(self):
        xml='<ix:nonFraction xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" scale="6" decimals="-5" sign="-" format="ixt:num-dot-decimal">3,220.4</ix:nonFraction>'
        f=numeric(etree.fromstring(xml));self.assertEqual(f['value'],'-3220400000.0');self.assertEqual(f['reported_rounding_unit_usd'],'100000')

    def test_fixed_zero_requires_supported_transform_and_explicit_rendering(self):
        xml='<ix:nonFraction xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" format="ixt:fixed-zero" decimals="-6">—</ix:nonFraction>'
        self.assertEqual(numeric(etree.fromstring(xml))['value'],'0')
        for value in ('5','unknown'):
            with self.assertRaises(ValueError):numeric(etree.fromstring(xml.replace('—',value)))

    def test_unknown_format_namespace_untransformed_commas_and_nil_fail(self):
        base='<ix:nonFraction xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="http://example.invalid/transform" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" decimals="0" {attr}>{value}</ix:nonFraction>'
        for attr,value in (('format="ixt:num-dot-decimal"','1,200'),('','1,200'),('xsi:nil="true"','0'),('scale="99"','1'),('sign="+"','1'),('continuedAt="next"','1')):
            with self.subTest(attr=attr),self.assertRaises(ValueError):numeric(etree.fromstring(base.format(attr=attr,value=value)))

    def test_quarter_segment_wrong_currency_hidden_and_unsupported_fact_do_not_match(self):
        for mode in ('quarter','segment','currency','hidden','format'):
            body=self.body
            if mode=='quarter':body=body.replace(b'2025-01-01',b'2025-10-01')
            if mode=='segment':body=body.replace(b'</xbrli:entity>',b'<xbrli:segment/></xbrli:entity>')
            if mode=='currency':body=body.replace(b'iso4217:USD',b'iso4217:CAD')
            if mode=='hidden':body=body.replace(b'<td>Capital expenditures</td>',b'<td>Capital expenditures</td>').replace(b'name="us-gaap:PaymentsToAcquireProductiveAssets"',b'style="display:none" name="us-gaap:PaymentsToAcquireProductiveAssets"')
            if mode=='format':body=body.replace(b'name="us-gaap:PaymentsToAcquireProductiveAssets"',b'format="ixt:unknown" name="us-gaap:PaymentsToAcquireProductiveAssets"')
            p=self.report(body)
            if p['periods']:self.assertNotEqual(p['periods'][0]['facts']['productive_asset_payments']['status'],'matched',mode)
            else:self.assertEqual(p['status'],'requires_review')

    def test_duplicate_cash_tables_and_wrong_issuer_fail_closed(self):
        body=self.body.replace(b'</html>',self.body[self.body.rfind(b'<table>'):self.body.rfind(b'</table>')+8]+b'</html>')
        for source in (body,self.body.replace(CIK.encode(),b'0000000002')):
            self.assertEqual(self.report(source)['status'],'requires_review')

    def test_company_facts_different_value_filing_or_period_does_not_match(self):
        for key,value in (('val',999),('accn','0000000001-26-999999'),('start','2025-02-01')):
            payload=copy.deepcopy(self.fx.payload);facts=payload['facts']['us-gaap']['PaymentsToAcquireProductiveAssets']['units']['USD'];facts[0][key]=value
            p=self.report(payload=payload)['periods'][0];self.assertNotEqual(p['facts']['productive_asset_payments']['status'],'matched')
            self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'])

    def test_outside_statement_table_cannot_fill_missing_ppe(self):
        body=document({'PaymentsToAcquirePropertyPlantAndEquipment':('Property purchases',None)})
        extra='<table><tr><td>Property purchases</td><td>'+number('PaymentsToAcquirePropertyPlantAndEquipment',20)+'</td></tr></table>'
        body=body.replace(b'</html>',extra.encode()+b'</html>')
        self.assertEqual(self.report(body)['periods'][0]['facts']['ppe_payments']['status'],'unavailable')

    def test_conflicting_unsegmented_annual_contexts_are_not_selected_arbitrarily(self):
        root=etree.fromstring(self.body);ns='{http://www.xbrl.org/2003/instance}'
        context=copy.deepcopy(next(root.iter(ns+'context')));context.set('id','alternate');context.find(ns+'period/'+ns+'startDate').text='2025-01-02';root.append(context)
        table=list(root.iter('{http://www.w3.org/1999/xhtml}table'))[-1]
        extra='<tr xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><td>Capital expenditures</td><td>'+number('PaymentsToAcquireProductiveAssets',35,contextRef='alternate')+'</td></tr>'
        table.append(etree.fromstring(extra));p=self.report(etree.tostring(root))['periods'][0]
        self.assertEqual(p['facts']['productive_asset_payments']['status'],'requires_review')
        self.assertIsNone(p['cash_measures']['cash_after_productive_asset_payments']['value'])

    def test_conflicting_company_facts_values_are_not_reduced_to_any_match(self):
        payload=copy.deepcopy(self.fx.payload);values=payload['facts']['us-gaap']['PaymentsToAcquireProductiveAssets']['units']['USD'];values.append({**values[0],'val':36})
        self.assertEqual(self.report(payload=payload)['periods'][0]['facts']['productive_asset_payments']['status'],'source_mismatch')

    def test_forged_scope_hash_cannot_replay(self):
        self.capture()
        with self.db() as db:
            row=dict(db.execute('SELECT * FROM statement_check_reports').fetchone());inputs=json.loads(row['input_json']);inputs['scope_sha256']='0'*64;row['input_json']=encoded(inputs);row['id']=sha(row['input_json'])
            with self.assertRaisesRegex(ValueError,'scope differs'):replay(db,row)

    def test_capture_before_saved_inputs_or_source_time_is_rejected(self):
        self.capture()
        with self.db() as db:
            original=dict(db.execute('SELECT * FROM statement_check_reports').fetchone())
            for at in ('2026-10-14T17:45:00+00:00','2026-10-14T18:00:00'):
                row=copy.deepcopy(original);inputs=json.loads(row['input_json']);inputs['captured_at']=at;row['captured_at']=at;row['input_json']=encoded(inputs);row['id']=sha(row['input_json'])
                with self.subTest(at=at),self.assertRaises(ValueError):replay(db,row)
            future_id=save_source(self.path,CIK,FILING['url'],self.body,200,'2026-10-15T00:00:00+00:00')
            row=copy.deepcopy(original);inputs=json.loads(row['input_json']);inputs['source_ids']['filing']=future_id;row['input_json']=encoded(inputs);row['id']=sha(row['input_json'])
            with self.assertRaisesRegex(ValueError,'future-dated'):replay(db,row)
        with patch('statement_checks.now',return_value='2026-10-14T17:45:00+00:00'),patch('statement_checks.requests.get') as get:
            request=Capture(**{**self.request().model_dump(),'request_id':'b'*32})
            with self.assertRaisesRegex(ValueError,'predate'):collect(self.path,'TEST',request,{'User-Agent':'fixture'})
            get.assert_not_called()

    def test_new_editions_can_show_explicitly_dated_prior_evidence_without_using_future_reports(self):
        old=self.capture()
        with self.db() as db:
            db.execute('INSERT INTO saved_list_versions VALUES(34)');db.execute('INSERT INTO saved_list_items VALUES(?,?,?)',(34,'TEST',encoded(self.fx.item)))
        current=self.client.get('/api/statement-checks/TEST?version=34').json()
        self.assertEqual(current['id'],old['id']);self.assertTrue(current['historical_report'])
        self.assertEqual(current['requested_research_version'],34);self.assertEqual(current['report']['research_version'],33)
        self.assertEqual(self.client.get('/api/statement-checks/TEST?version=32').json()['status'],'unavailable')

    def test_download_denial_is_archived_without_following_redirects(self):
        from statement_checks import download
        class Denied:
            status_code=403
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,*args):yield b'access denied'
        with patch('statement_checks.requests.get',return_value=Denied()) as get:
            with self.assertRaisesRegex(ValueError,'denied'):download(self.path,CIK,FILING['url'],{'User-Agent':'fixture'},__import__('requests'))
            self.assertEqual(get.call_count,1);self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with self.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM statement_source_documents WHERE status_code=403').fetchone()[0],1)

    def test_report_replay_idempotency_and_original_export_hash(self):
        first=self.capture();second=self.capture();self.assertEqual(first,second)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM statement_check_reports').fetchone()[0],1)
            self.assertEqual(replay(db,db.execute('SELECT * FROM statement_check_reports').fetchone()),first)
        r=self.client.get('/api/statement-checks/reports/'+first['id']);self.assertEqual(r.status_code,200)
        source=self.client.get('/api/statement-checks/sources/'+self.source_ids['filing']);self.assertEqual(source.content,self.body);self.assertEqual(sha(source.content),source.headers['X-Content-SHA256']);self.assertTrue(source.headers['content-type'].startswith('application/octet-stream'))

    def test_capture_rejects_stale_scope_extra_values_and_reused_id(self):
        req=self.request().model_dump()
        self.assertEqual(self.client.post('/api/statement-checks/TEST/capture',json={**req,'value':1}).status_code,422)
        self.assertEqual(self.client.post('/api/statement-checks/TEST/capture',json={**req,'scope_sha256':'0'*64}).status_code,409)
        self.capture()
        self.assertEqual(self.client.post('/api/statement-checks/TEST/capture',json={**req,'scope_sha256':'0'*64}).status_code,409)

    def test_new_edition_stops_capture_before_network_but_preserves_old_replay(self):
        old=self.capture();req=self.request()
        with self.db() as db:db.execute('INSERT INTO saved_list_versions VALUES(34)')
        with patch('statement_checks.requests.get') as get:
            req=Capture(**{**req.model_dump(),'request_id':'b'*32})
            with self.assertRaises(Exception) as error:collect(self.path,'TEST',req,{'User-Agent':'fixture'})
            self.assertEqual(error.exception.status_code,409);get.assert_not_called()
        self.assertEqual(self.client.get('/api/statement-checks/reports/'+old['id']).status_code,200)

    def test_report_and_originals_are_immutable(self):
        self.capture()
        with self.db() as db:
            for table in ('statement_source_documents','statement_check_reports'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(f'DELETE FROM {table}')

    def test_corrupted_original_rejects_replay_and_export(self):
        saved=self.capture()
        with self.db() as db:
            db.execute('DROP TRIGGER immutable_statement_source_documents_UPDATE');db.execute('UPDATE statement_source_documents SET sha256=? WHERE id=?',('0'*64,self.source_ids['filing']))
        self.assertEqual(self.client.get('/api/statement-checks/reports/'+saved['id']).status_code,409)
        self.assertEqual(self.client.get('/api/statement-checks/sources/'+self.source_ids['filing']).status_code,409)

    def test_corrupt_compressed_original_fails_closed(self):
        saved=self.capture()
        with self.db() as db:
            db.execute('DROP TRIGGER immutable_statement_source_documents_UPDATE');db.execute('UPDATE statement_source_documents SET body_gzip=? WHERE id=?',(b'\x1f\x8b\x08\x00broken archive',self.source_ids['filing']))
        self.assertEqual(self.client.get('/api/statement-checks/reports/'+saved['id']).status_code,409)
        self.assertEqual(self.client.get('/api/statement-checks/sources/'+self.source_ids['filing']).status_code,409)

    def test_storage_reserve_and_recent_denial_stop_requests(self):
        with patch('statement_checks.shutil.disk_usage') as usage,patch('statement_checks.requests.get') as get:
            usage.return_value.free=1
            with self.assertRaisesRegex(ValueError,'reserve'):collect(self.path,'TEST',self.request(),{'User-Agent':'fixture'})
            get.assert_not_called()
        save_source(self.path,CIK,FILING['url'],b'denied',403,AT)
        with patch('statement_checks.now',return_value=AT),patch('statement_checks.requests.get') as get:
            with self.assertRaisesRegex(ValueError,'paused'):collect(self.path,'TEST',self.request(),{'User-Agent':'fixture'})
            get.assert_not_called()

    def test_exact_index_identity_rejects_wrong_symbol_issuer_and_paths(self):
        for kind in ('ticker','cik','path','duplicate'):
            index=copy.deepcopy(self.index)
            if kind=='ticker':index['tickers']=['TESTP']
            if kind=='cik':index['cik']=2
            if kind=='path':index['filings']['recent']['primaryDocument']=['../other.htm']
            if kind=='duplicate':
                for values in index['filings']['recent'].values():values*=2
            with self.subTest(kind=kind),self.assertRaises(ValueError):filing_reference(index,CIK,'TEST',FILING['accessionNumber'],'2025-12-31')


if __name__=='__main__':unittest.main()
