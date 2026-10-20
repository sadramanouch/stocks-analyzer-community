import base64
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
from ibkr_flex import FlexInput, init_flex, inspect_sources, make_flex_router, preview_flex


def fixture():
    def source(begin, end, cash, shares, price, activity=False):
        root = ET.Element('FlexQueryResponse', type='AF'); wrapper = ET.SubElement(root, 'FlexStatements')
        statement = ET.SubElement(wrapper, 'FlexStatement', accountId='FIXTURE-REDACTED', fromDate=begin, toDate=end)
        report = ET.SubElement(statement, 'CashReport')
        ET.SubElement(report, 'CashReportCurrency', currency='USD', fromDate=begin, toDate=end,
                      startingCash='1000', endingCash=cash, endingSettledCash='777', commissions='-1' if activity else '0', depositsWithdrawals='0')
        positions = ET.SubElement(statement, 'OpenPositions')
        ET.SubElement(positions, 'OpenPosition', accountId='FIXTURE-REDACTED', currency='USD', assetCategory='STK',
                      multiplier='1', conid='123', symbol='TEST', isin='US0000000001', position=str(shares), markPrice=str(price),
                      positionValue=str(shares * price), reportDate=end, levelOfDetail='SUMMARY')
        if activity:
            trades = ET.SubElement(statement, 'Trades')
            ET.SubElement(trades, 'Trade', currency='USD', assetCategory='STK', multiplier='1', conid='123', symbol='TEST',
                          tradeID='fixture-fill', levelOfDetail='EXECUTION', dateTime=end+';140000', quantity='1', tradePrice='100',
                          buySell='BUY', proceeds='-100', ibCommission='-1', ibCommissionCurrency='USD', taxes='-2', netCash='-103')
            transactions = ET.SubElement(statement, 'CashTransactions')
            ET.SubElement(transactions, 'CashTransaction', transactionID='fixture-income', currency='USD', amount='4', type='Dividends', dateTime=end+';150000')
            ET.SubElement(transactions, 'CashTransaction', transactionID='fixture-fee', currency='USD', amount='-8', type='Other Fees', dateTime=end+';160000')
            ET.SubElement(statement, 'CorporateActions'); ET.SubElement(statement, 'Transfers')
        return root
    return source('20261012','20261012','1000',2,100), source('20261013','20261013','893',3,110,True)


class FlexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = str(Path(self.temp.name)/'test.db'); init_flex(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE paper_ledger_events(event_id TEXT)'); db.execute("INSERT INTO paper_ledger_events VALUES('virtual-capital')")
        app = FastAPI(); app.include_router(make_flex_router(self.path)); self.client = TestClient(app)
        self.opening, self.activity = fixture()
    def tearDown(self): self.temp.cleanup()
    def body(self):
        return FlexInput(opening_base64=base64.b64encode(ET.tostring(self.opening)).decode(),
                         activity_base64=base64.b64encode(ET.tostring(self.activity)).decode(), account_label='Synthetic test only', export_timezone='UTC')
    def result(self): return inspect_sources(self.body(), '2026-10-14T20:00:00Z')
    def row(self, tag): return self.activity.find('.//'+tag)

    def test_native_trade_date_cash_shares_fees_taxes_and_identity(self):
        r = self.result(); self.assertTrue(r['arithmetic_reconciled']); self.assertEqual(r['replayed_closing_cash'],'893')
        self.assertEqual(r['cash_difference'],'0'); self.assertEqual(r['share_differences'],{}); self.assertEqual(r['commission_cash_effect'],'-1'); self.assertEqual(r['execution_tax_cash_effect'],'-2')
        self.assertEqual(r['instruments'][0]['conid'],'123'); self.assertEqual(r['instruments'][0]['reported_identifiers']['isin'],'US0000000001')
        self.assertIsNone(r['verified_account_return']); self.assertIsNone(r['statement_reference_return']); self.assertFalse(r['broker_connected']); self.assertFalse(r['paper_ledger_changed'])

    def test_missing_cash_entry_blocks(self):
        self.row('CashTransactions').remove(self.row('CashTransactions')[-1]); r=self.result()
        self.assertEqual(r['cash_difference'],'8'); self.assertFalse(r['arithmetic_reconciled'])

    def test_missing_opening_positions_is_not_assumed_empty(self):
        statement=self.opening.find('.//FlexStatement'); statement.remove(statement.find('OpenPositions'))
        r=self.result(); self.assertIsNone(r['share_differences']); self.assertFalse(r['arithmetic_reconciled']); self.assertIn('empty holdings',' '.join(r['issues']))

    def test_missing_coverage_section_is_not_zero_activity(self):
        statement=self.activity.find('.//FlexStatement'); statement.remove(statement.find('CorporateActions'))
        self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_nonempty_transfer_action_and_unknown_economic_sections_block(self):
        for section in ('Transfers','CorporateActions','UnsettledTransfers'):
            o,a=fixture(); self.opening,self.activity=o,a
            s=self.row(section)
            if s is None: s=ET.SubElement(self.row('FlexStatement'),section)
            ET.SubElement(s,'Unmapped',amount='0')
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_duplicate_execution_and_cash_row_block_double_counting(self):
        for section,tag in (('Trades','Trade'),('CashTransactions','CashTransaction')):
            self.opening,self.activity=fixture(); self.row(section).append(copy.deepcopy(self.row(tag)))
            r=self.result(); self.assertTrue(r['row_reviews']); self.assertFalse(r['arithmetic_reconciled'])

    def test_orders_lots_and_cancelled_rows_are_not_executions(self):
        for key,value in (('levelOfDetail','ORDER'),('levelOfDetail','CLOSED_LOT'),('origTradeID','earlier'),('notes','Ca')):
            self.opening,self.activity=fixture(); self.row('Trade').set(key,value)
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_wrong_net_cash_and_commission_currency_block(self):
        for key,value in (('netCash','-100'),('ibCommissionCurrency','CAD'),('proceeds','100')):
            self.opening,self.activity=fixture(); self.row('Trade').set(key,value)
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_commission_rebate_remains_signed(self):
        self.row('Trade').set('ibCommission','1'); self.row('Trade').set('netCash','-101')
        self.row('CashReportCurrency').set('endingCash','895'); self.row('CashReportCurrency').set('commissions','1')
        r=self.result(); self.assertTrue(r['arithmetic_reconciled']); self.assertEqual(r['commission_cash_effect'],'1')

    def test_sales_replay_signed_shares(self):
        row=self.row('Trade'); row.set('quantity','-1'); row.set('buySell','SELL'); row.set('proceeds','100'); row.set('taxes','0'); row.set('netCash','99')
        self.row('CashTransactions').clear(); self.row('CashReportCurrency').set('endingCash','1099')
        self.row('OpenPosition').set('position','1'); self.row('OpenPosition').set('positionValue','110')
        self.assertTrue(self.result()['arithmetic_reconciled'])

    def test_contract_collision_and_identifier_change_block(self):
        self.row('Trade').set('conid','999'); self.assertFalse(self.result()['arithmetic_reconciled'])
        self.opening,self.activity=fixture(); self.row('OpenPosition').set('isin','US9999999999'); self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_multicurrency_derivatives_and_lots_need_another_model(self):
        for key,value in (('currency','CAD'),('assetCategory','OPT'),('multiplier','100'),('levelOfDetail','LOT')):
            self.opening,self.activity=fixture(); self.row('OpenPosition').set(key,value)
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_cash_report_boundary_and_reported_totals_crosscheck(self):
        for key,value in (('startingCash','999'),('commissions','-9'),('depositsWithdrawals','1')):
            self.opening,self.activity=fixture(); self.row('CashReportCurrency').set(key,value)
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_unknown_cash_type_and_out_of_period_records_block(self):
        for key,value in (('type','Mystery fee'),('dateTime','20261012;150000')):
            self.opening,self.activity=fixture(); self.row('CashTransaction').set(key,value)
            self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_date_only_cash_retains_day_precision_without_invented_clock(self):
        self.row('CashTransaction').set('dateTime','20261013'); r=self.result()
        self.assertTrue(r['arithmetic_reconciled']); event=next(e for e in r['mapped_events'] if e['kind']=='Dividends'); self.assertIsNone(event['occurred_at'])

    def test_wrong_accounts_overlapping_period_future_day_and_timezone_reject(self):
        for account,date in (('OTHER','20261013'),('FIXTURE-REDACTED','20261012')):
            self.opening,self.activity=fixture(); self.row('FlexStatement').set('accountId',account); self.row('FlexStatement').set('fromDate',date)
            with self.assertRaises(ValueError): self.result()
        self.opening,self.activity=fixture()
        with self.assertRaises(ValueError): inspect_sources(self.body(),'2026-10-13T20:00:00Z')
        b=self.body(); b.export_timezone='Not/AZone'; self.assertEqual(self.client.post('/api/broker-flex/preview',json=b.model_dump()).status_code,422)

    def test_dst_ambiguous_timestamp_is_not_assigned_a_fold(self):
        from ibkr_flex import event_time
        from zoneinfo import ZoneInfo
        for stamp in ('20261101;013000','20260308;023000'):
            with self.assertRaises(ValueError): event_time(stamp,ZoneInfo('America/New_York'))

    def test_dtd_entity_bad_base64_namespace_and_multiaccount_rejected(self):
        for xml in (b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>',b'<FlexQueryResponse xmlns="unexpected"/>',b'<FlexQueryResponse><FlexStatements><FlexStatement/><FlexStatement/></FlexStatements></FlexQueryResponse>'):
            body=self.body(); body.opening_base64=base64.b64encode(xml).decode()
            self.assertEqual(self.client.post('/api/broker-flex/preview',json=body.model_dump()).status_code,422)
        body=self.body(); body.opening_base64='not base64'; self.assertEqual(self.client.post('/api/broker-flex/preview',json=body.model_dump()).status_code,422)

    def test_preview_commit_exact_bytes_deduplication_and_virtual_isolation(self):
        b=self.body(); p=self.client.post('/api/broker-flex/preview',json=b.model_dump()); self.assertEqual(p.status_code,200)
        self.assertEqual(self.client.get('/api/broker-flex').json()['bundles'],[])
        b.preview_token='wrong'; self.assertEqual(self.client.post('/api/broker-flex/commit',json=b.model_dump()).status_code,422)
        b.preview_token=p.json()['preview_token']; saved=self.client.post('/api/broker-flex/commit',json=b.model_dump()); self.assertTrue(saved.json()['created'])
        self.assertFalse(self.client.post('/api/broker-flex/commit',json=b.model_dump()).json()['created'])
        exported=self.client.get('/api/broker-flex/bundles/1'); self.assertEqual(exported.status_code,200); self.assertEqual(exported.json()['sources']['opening_base64'],b.opening_base64)
        with sqlite3.connect(self.path) as db: self.assertEqual(db.execute('SELECT * FROM paper_ledger_events').fetchall(),[('virtual-capital',)])

    def test_immutable_corrupt_source_and_changed_result_are_withheld(self):
        b=self.body(); b.preview_token=preview_flex(b)['preview_token']; self.client.post('/api/broker-flex/commit',json=b.model_dump())
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM broker_flex_bundles')
            db.execute('DROP TRIGGER immutable_broker_flex_bundles_UPDATE'); r=json.loads(db.execute('SELECT result_json FROM broker_flex_bundles').fetchone()[0]); r['replayed_closing_cash']='999'; raw=json.dumps(r,sort_keys=True,separators=(',',':'))
            from forward_performance import fingerprint
            db.execute('UPDATE broker_flex_bundles SET result_json=?,result_sha256=?',(raw,fingerprint(raw)))
        self.assertEqual(self.client.get('/api/broker-flex/bundles/1').status_code,409); d=self.client.get('/api/broker-flex').json(); self.assertEqual(d['bundles'],[]); self.assertEqual(len(d['integrity_errors']),1)

    def test_source_identifiers_not_echoed_in_validation_errors(self):
        self.row('FlexStatement').set('accountId','private-account-number')
        r=self.client.post('/api/broker-flex/preview',json=self.body().model_dump()); self.assertEqual(r.status_code,422); self.assertNotIn('private-account-number',r.text)

    def test_equal_second_executions_do_not_invent_source_order(self):
        row=self.row('Trade'); row.set('tradeID','z-buy'); row.set('quantity','3'); row.set('proceeds','-300'); row.set('taxes','0'); row.set('ibCommission','0'); row.set('netCash','-300')
        sale=copy.deepcopy(row); sale.set('tradeID','a-sell'); sale.set('buySell','SELL'); sale.set('quantity','-3'); sale.set('proceeds','300'); sale.set('netCash','300'); self.row('Trades').append(sale)
        self.row('CashTransactions').clear(); self.row('CashReportCurrency').set('endingCash','1000'); self.row('CashReportCurrency').set('commissions','0')
        self.row('OpenPosition').set('position','2'); self.row('OpenPosition').set('positionValue','220')
        self.assertTrue(self.result()['arithmetic_reconciled'])

    def test_price_value_rounding_is_not_guessed_from_integer_prices(self):
        self.row('OpenPosition').set('positionValue','331')
        self.assertFalse(self.result()['arithmetic_reconciled'])

    def test_zero_original_reference_and_root_credentials(self):
        self.row('Trade').set('origTradeID','0'); self.row('Trade').set('origOrderID','0')
        self.assertTrue(self.result()['arithmetic_reconciled'])
        self.activity.set('token','private-token')
        response=self.client.post('/api/broker-flex/preview',json=self.body().model_dump())
        self.assertEqual(response.status_code,422); self.assertNotIn('private-token',response.text)
