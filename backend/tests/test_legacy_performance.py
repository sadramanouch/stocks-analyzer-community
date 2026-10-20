import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from legacy_ownership import init_legacy, review_submission, save_legacy, summaries, numeric, make_legacy_router
from ownership_history import init_ownership
from performance_audit import init_performance, create_audit, make_performance_router

REF = dict(ticker='TEST',cik='123',accession='0000000123-03-000001',filed_date='2003-03-21',form='4',primary_document='form.htm',index_url='https://data.sec.gov/submissions/CIK0000000123-submissions-001.json',index_sha256='fixture')
AT = '2026-10-13T21:00:00+00:00'
HEADER = '''<SEC-HEADER>
ACCESSION NUMBER: 0000000123-03-000001
CONFORMED SUBMISSION TYPE: 4
FILED AS OF DATE: 20030321
SUBJECT COMPANY:
 COMPANY CONFORMED NAME: Example
 CENTRAL INDEX KEY: 123
REPORTING-OWNER:
 COMPANY CONFORMED NAME: Example Owner
 CENTRAL INDEX KEY: 456
</SEC-HEADER>'''
TABLE = '''<table><tr><td>1. Title of Security (Instr. 3)</td><td>2. Transaction Date</td><td>2A. Deemed Execution Date</td><td colspan="2">3. Transaction Code</td><td colspan="3">4. Securities Acquired (A) or Disposed of (D)</td><td>5. Amount of Securities Owned</td><td>6. Ownership Form</td><td>7. Nature of Indirect</td></tr><tr><td>Code</td><td>V</td><td>Amount</td><td>A/D</td><td>Price</td></tr><tr><td>Common Stock</td><td>3/19/03</td><td></td><td>S(1)</td><td></td><td>1,000(2)</td><td>D</td><td>$20.25</td><td>500</td><td>D</td><td></td></tr></table>'''
RAW = (HEADER+'<DOCUMENT>\n<TYPE>4\n<FILENAME>form.htm\n<TEXT>'+TABLE+'</TEXT></DOCUMENT>').encode()


class LegacyPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT,cik TEXT);
                CREATE TABLE filings(company_id INTEGER,form_type TEXT,url TEXT,accession_number TEXT,filed_date TEXT);
                CREATE TABLE paper_positions(id INTEGER PRIMARY KEY,ticker TEXT,status TEXT,virtual_dollars REAL,entry_price REAL,entry_date TEXT,close_date TEXT,close_price REAL,realized_return_pct REAL);
                CREATE TABLE position_snapshots(id INTEGER PRIMARY KEY,position_id INTEGER,snapshot_date TEXT,current_price REAL,unrealized_pnl REAL,return_pct REAL);''')
            db.execute("INSERT INTO companies VALUES(1,'TEST','123')")
        init_ownership(self.path);init_legacy(self.path);init_performance(self.path)
        payload=json.dumps({'form':['4'],'accessionNumber':[REF['accession']],'filingDate':[REF['filed_date']],'primaryDocument':['form.htm']})
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO ownership_index_documents VALUES(?,?,?,?,?,?,?,?,?)',('TEST',REF['index_url'],hashlib.sha256(payload.encode()).hexdigest(),AT,payload,'historical','{}',0,1))

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_cells_annotations_and_real_observation_are_preserved(self):
        review=review_submission(RAW,REF,AT)
        row=review['transactions'][0]
        self.assertEqual((row['reported_shares'],row['reported_price'],row['reported_notional']),('1000','20.25','20250.00'))
        self.assertEqual(row['cells'][5],'1,000(2)')
        self.assertEqual(row['reported_code_cell'],'S(1)')
        self.assertEqual(row['transaction_code'],'S')
        self.assertEqual(row['numbered_annotations'],['1','2'])
        self.assertEqual(row['reported_date'],'2003-03-19')
        self.assertEqual(review['observed_at'],AT)
        self.assertFalse(review['copy_eligible'])
        self.assertIsNone(review['trader_return'])
        self.assertEqual(review['status'],'context_reviewed')

    def test_header_and_filename_mismatches_fail_before_storage(self):
        for needle,replacement in [(b'CENTRAL INDEX KEY: 123',b'CENTRAL INDEX KEY: 987'),(b'20030321',b'20030322'),(b'FILENAME>form.htm',b'FILENAME>other.htm'),(b'TYPE>4\n',b'TYPE>5\n'),(b'ACCESSION NUMBER: 0000000123-03-000001',b'ACCESSION NUMBER: 0000000123-03-000002')]:
            with self.subTest(needle=needle),self.assertRaises(ValueError):
                save_legacy(self.path,RAW.replace(needle,replacement),REF,AT)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM legacy_ownership_documents').fetchone()[0],0)
        for value in ['2003-01-01T00:00:00Z','2026-10-13T00:00:00']:
            with self.assertRaises(ValueError):review_submission(RAW,REF,value)

    def test_unknown_price_missing_date_and_changed_layout_remain_unresolved(self):
        for value in ['20-25','N/A','1,00','NaN','Infinity','$20 plus fee','20(1) + 2']:
            self.assertIsNone(numeric(value))
        review=review_submission(RAW.replace(b'$20.25',b'$20-25').replace(b'3/19/03',b''),REF,AT)
        row=review['transactions'][0]
        self.assertIsNone(row['reported_price']);self.assertIsNone(row['reported_notional'])
        self.assertNotIn('reported_date',row)
        self.assertEqual(review['status'],'needs_manual_mapping')
        changed=review_submission(RAW.replace(b'colspan="3"',b'colspan="4"'),REF,AT)
        self.assertEqual(changed['transactions'],[])
        self.assertTrue(changed['unresolved'])
        explained=review_submission(RAW.replace(b'</TEXT>',b'<p>Explanation of Responses: This option was not exercised.</p><p>Signature of Reporting Person</p></TEXT>'),REF,AT)
        self.assertEqual(explained['explanation_text'],'This option was not exercised.')

    def test_archives_are_idempotent_immutable_and_keep_rows_out_of_signal_feed(self):
        self.assertTrue(save_legacy(self.path,RAW,REF,AT))
        self.assertFalse(save_legacy(self.path,RAW,REF,AT))
        with self.assertRaises(ValueError):save_legacy(self.path,RAW.replace(b'$20.25',b'$21.25'),REF,AT)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM trade_events').fetchone()[0],0)
            result=summaries(db)
            self.assertEqual((result['total'],result['archived'],result['context_reviewed'],result['copy_eligible']),(1,1,1,0))
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM legacy_ownership_documents')

    def test_blocked_source_finishes_run_retains_data_and_releases_lock(self):
        app=FastAPI();app.include_router(make_legacy_router(self.path));client=TestClient(app)
        class Immediate:
            def __init__(self,target,args,**kwargs):self.target,self.args=target,args
            def start(self):self.target(*self.args)
        response=type('Response',(),{'status_code':403})()
        with patch('legacy_ownership.threading.Thread',Immediate),patch('legacy_ownership.requests.get',return_value=response) as fetch:
            for _ in range(2):
                self.assertEqual(client.post('/api/trade-history/legacy/review',json={'limit':1}).status_code,200)
            self.assertEqual(fetch.call_count,2)
        result=client.get('/api/trade-history/legacy').json()
        self.assertEqual(result['runs'][0]['state'],'blocked');self.assertEqual(result['archived'],0)
        self.assertEqual(client.get('/api/trade-history/legacy?limit=101').status_code,422)
        self.assertEqual(client.get('/api/trade-history/legacy?offset=-1').status_code,422)

    def test_all_snapshot_arithmetic_and_coverage_are_checked_without_certifying_returns(self):
        with sqlite3.connect(self.path) as db:
            db.executemany('INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?)',[(1,'TEST','open',100,10,'2026-10-01',None,None,None),(2,'OLD','closed',100,20,'2026-10-01','2026-10-05',30,51)])
            db.executemany('INSERT INTO position_snapshots VALUES(?,?,?,?,?,?)',[(1,1,'2026-10-04',11,10,10),(2,1,'2026-10-06',12,19,21),(3,2,'2026-10-06',30,50,50),(4,999,'2026-10-06',30,0,0)])
        result=create_audit(self.path)
        self.assertEqual(result['arithmetic']['snapshots_checked'],3)
        self.assertEqual(result['arithmetic']['issue_counts'],{'closed_return_mismatch':1,'snapshot_return_mismatch':1,'snapshot_pnl_mismatch':1,'snapshot_outside_holding_dates':1,'orphan_snapshot':1})
        self.assertEqual(result['snapshot_coverage']['days_with_missing_positions'],1)
        self.assertEqual(result['snapshot_coverage']['days_with_outside_positions'],1)
        self.assertTrue(all(m['return_pct'] is None and m['status']=='unverified' for m in result['metrics']))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0],2)

    def test_audit_inputs_and_results_remain_reproducible_after_source_changes(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO paper_positions VALUES(1,'TEST','open',100,10,'2026-10-01',NULL,NULL,NULL)")
        first=create_audit(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE paper_positions SET entry_price=20')
        second=create_audit(self.path)
        self.assertNotEqual(first['input_sha256'],second['input_sha256'])
        app=FastAPI();app.include_router(make_performance_router(self.path));client=TestClient(app)
        archived=client.get('/api/performance-audit/1/inputs')
        self.assertEqual(archived.status_code,200)
        self.assertEqual(hashlib.sha256(archived.content).hexdigest(),first['input_sha256'])
        self.assertEqual(archived.json()['paper_positions'][0]['entry_price'],10)
        self.assertEqual(client.get('/api/performance-audit').json()['audit_id'],2)
        self.assertEqual(client.get('/api/performance-audit/999/inputs').status_code,404)
        with sqlite3.connect(self.path) as db:
            for table in ['performance_audits','performance_audit_inputs']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)


if __name__=='__main__':unittest.main()
