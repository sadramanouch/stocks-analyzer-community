import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from unittest.mock import patch
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from market_history import (VERSION,init_market_history,validate_history,save_history,latest_histories,compare_position,completed_day,fetch_history,make_market_router)
from copy_study import init_copy,create_study,study_event,eligible,POLICY
from ownership_history import init_ownership
from paper_ledger import replay,append_event,read_ledger,preview_import,commit_import

AT='2026-10-13T23:00:00+00:00'
def history(symbol='TEST'):
    bars=[]
    for i,day in enumerate(['2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09','2026-10-12','2026-10-13']):
        n=100+i;bars.append(dict(date=day,open=n,high=n+1,low=n-1,close=n,adjusted_close=n,dividend=0,split_ratio=0))
    return dict(schema=VERSION,price_basis='split_adjusted',adjusted_close_basis='vendor_total_return_proxy',metadata=dict(symbol=symbol,currency='USD',instrumentType='EQUITY',exchangeTimezoneName='America/New_York'),bars=bars,omitted_price_dates=[])
def event(kind,hour,**fields):
    stamp=f'2026-10-01T{hour:02}:00:00+00:00'
    return dict(event_id=f'{kind}-{hour}',kind=kind,occurred_at=stamp,source='Explicit fixture reference',currency='USD',price_basis='unadjusted',quote_at=stamp,**fields)
def signal():
    return dict(id='fixture-purchase',actor='Fixture',asset='TEST',kind='insiders',transaction_code='P',side='buy',table='nonDerivative',instrument='CS',source_verification='primary_parsed',accession='fixture',available_at='2026-10-05T15:00:00+00:00',first_seen_at='2026-10-05T15:00:00+00:00',disclosed_at='2026-10-05T00:00:00+00:00',footnotes={},amendment=False,trading_plan=None)

class MarketCopyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT,cik TEXT);CREATE TABLE filings(company_id INTEGER,form_type TEXT,url TEXT,accession_number TEXT,filed_date TEXT);
                CREATE TABLE paper_positions(id INTEGER PRIMARY KEY,ticker TEXT,status TEXT,entry_date TEXT,entry_price REAL,close_date TEXT,close_price REAL,realized_return_pct REAL);''')
        init_ownership(self.path);init_market_history(self.path);init_copy(self.path)
    def tearDown(self):self.temp.cleanup()
    def documents(self):
        for s in ('TEST','SPY'):save_history(self.path,s,'2026-10-05','2026-10-13',history(s),AT)
        with sqlite3.connect(self.path) as db:return latest_histories(db)
    def test_completed_day_is_exchange_local_and_does_not_use_an_incomplete_day(self):
        self.assertEqual(str(completed_day('2026-10-14T00:00:00Z')),'2026-10-13')
        self.assertEqual(str(completed_day('2026-10-13T19:59:00Z')),'2026-10-12')
        self.assertEqual(str(completed_day('2026-10-13T21:30:00Z')),'2026-10-13')
    def test_currency_symbol_adjustment_future_date_and_duplicate_guards(self):
        base=history()
        for change in [('currency','JPY'),('symbol','OTHER'),('instrumentType','FUTURE'),('exchangeTimezoneName','Asia/Tokyo')]:
            p=copy.deepcopy(base);p['metadata'][change[0]]=change[1]
            with self.assertRaises(ValueError):validate_history(p,'TEST','2026-10-05','2026-10-13',AT)
        for bad in ('unadjusted','dividend_adjusted'):
            p=copy.deepcopy(base);p['price_basis']=bad
            with self.assertRaises(ValueError):validate_history(p,'TEST','2026-10-05','2026-10-13',AT)
        p=copy.deepcopy(base);p['bars'].append(p['bars'][-1])
        with self.assertRaises(ValueError):validate_history(p,'TEST','2026-10-05','2026-10-13',AT)
        with self.assertRaises(ValueError):validate_history(base,'TEST','2026-10-05','2026-10-14',AT)
    def test_invalid_ohlc_and_nonfinite_values_cannot_be_archived(self):
        for key,bad in [('open',999),('close',float('nan')),('adjusted_close',0),('split_ratio',-1),('adjusted_close','1e-1000')]:
            p=history();p['bars'][0][key]=bad
            with self.assertRaises(ValueError):save_history(self.path,'TEST','2026-10-05','2026-10-13',p,AT)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM market_history_documents').fetchone()[0],0)
    def test_sdk_disables_automatic_adjustment_and_repair_and_retains_missing_price_days(self):
        dates=pd.to_datetime(['2026-10-05','2026-10-06']).tz_localize('America/New_York')
        frame=pd.DataFrame({'Open':[10,None],'High':[11,None],'Low':[9,None],'Close':[10,None],'Adj Close':[10,None],'Dividends':[0,0],'Stock Splits':[0,0]},index=dates)
        fake=type('Ticker',(),{'history':lambda self,**kwargs:frame,'get_history_metadata':lambda self:history()['metadata']})()
        with patch('market_history.yf.Ticker',return_value=fake),patch.object(fake,'history',wraps=fake.history) as call:
            data=fetch_history('TEST','2026-10-05','2026-10-13',AT)
            self.assertEqual(data['omitted_price_dates'],['2026-10-06'])
            self.assertFalse(call.call_args.kwargs['auto_adjust']);self.assertFalse(call.call_args.kwargs['repair']);self.assertFalse(call.call_args.kwargs['back_adjust'])
    def test_immutable_price_hashes_and_archive_tampering_detection(self):
        self.documents()
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM market_history_documents')
            db.execute('DROP TRIGGER immutable_market_history_documents_UPDATE');db.execute("UPDATE market_history_documents SET payload_json='{}'")
            with self.assertRaises(ValueError):latest_histories(db)
    def test_reverse_split_warning_uses_share_ratio_once_without_claiming_execution(self):
        payload=history();payload['bars']=[dict(date='2026-10-05',open=4.2,high=4.4,low=4,close=4.24,adjusted_close=4.24,dividend=0,split_ratio=0),dict(date='2026-10-06',open=7,high=8,low=6,close=7.35,adjusted_close=7.35,dividend=0,split_ratio=.05),dict(date='2026-10-07',open=11,high=12,low=10,close=11.48,adjusted_close=11.48,dividend=0,split_ratio=0)]
        doc={'id':'fixture','source_url':'https://example.test/history','observed_at':AT,'start_date':'2026-10-05','end_date':'2026-10-13','history':payload}
        p=dict(id=1,ticker='TEST',status='closed',entry_date='2026-10-05',entry_price=.212,close_date='2026-10-07',close_price=11.48,realized_return_pct=5315)
        notices=[dict(ticker='TEST',effective_date='2026-10-06',new_shares=1,old_shares=20,source='https://example.test/primary')]
        with patch('market_history.CONFIRMED_SPLITS',notices):result=compare_position(p,doc)
        self.assertEqual(result['status'],'split_share_change_missing_from_price_ratio')
        self.assertAlmostEqual(result['conditional_recorded_split_return_pct'],170.75471698)
        self.assertAlmostEqual(result['reference_price_return_pct'],170.75471698)
        self.assertFalse(result['account_return_verified'])
        p['entry_price']=4.24
        with patch('market_history.CONFIRMED_SPLITS',notices):adjusted=compare_position(p,doc)
        self.assertEqual(adjusted['status'],'compatible_adjusted_reference_ranges')
        self.assertAlmostEqual(adjusted['reference_price_return_pct'],170.75471698)
    def test_uncorroborated_vendor_adjustments_and_distributions_block_share_scenarios(self):
        payload=history();payload['bars'][1]['split_ratio']=6.665
        doc=dict(id='fixture',source_url='https://example.test/history',observed_at=AT,start_date='2026-10-05',end_date='2026-10-13',history=payload)
        p=dict(id=1,ticker='TEST',status='closed',entry_date='2026-10-05',entry_price=100,close_date='2026-10-13',close_price=106)
        r=compare_position(p,doc)
        self.assertEqual(r['status'],'corporate_action_review_required')
        self.assertNotIn('conditional_recorded_split_return_pct',r)
        self.assertAlmostEqual(r['reference_price_return_pct'],6)
        notices=[dict(ticker='TEST',effective_date='2026-10-06',new_shares=1333,old_shares=200,source='https://example.test/primary')]
        distributions=[dict(ticker='TEST',effective_date='2026-10-06',source='https://example.test/distribution',note='Separate security entitlement')]
        with patch('market_history.CONFIRMED_SPLITS',notices),patch('market_history.REVIEWED_DISTRIBUTIONS',distributions):
            r=compare_position(p,doc)
        self.assertTrue(r['primary_split_checks'][0]['vendor_matches'])
        self.assertEqual(r['status'],'corporate_action_review_required')
        self.assertNotIn('conditional_recorded_split_return_pct',r)
    def test_missing_endpoint_not_substituted_and_open_mark_time_remains_unknown(self):
        docs=self.documents();p=dict(id=1,ticker='TEST',status='closed',entry_date='2026-10-04',entry_price=100,close_date='2026-10-13',close_price=106)
        self.assertEqual(compare_position(p,docs['TEST'])['status'],'missing_reference')
        p.update(status='open',entry_date='2026-10-05',close_date=None)
        self.assertEqual(compare_position(p,docs['TEST'])['status'],'entry_checked_exit_time_unknown')
    def test_primary_split_comparison_survives_a_missing_entry_bar(self):
        payload=history('HKIT')
        payload['bars'][0]['split_ratio']=.02
        doc=dict(id='fixture',source_url='https://example.test/history',observed_at=AT,start_date='2026-03-24',end_date='2026-10-13',history=payload)
        p=dict(id=1,ticker='HKIT',status='closed',entry_date='2026-03-24',entry_price=.0717,close_date='2026-10-13',close_price=1.33)
        result=compare_position(p,doc)
        self.assertEqual(result['status'],'missing_reference')
        self.assertEqual(len(result['primary_split_checks']),1)
        self.assertFalse(result['primary_split_checks'][0]['vendor_matches'])
        self.assertIsNone(result['reference_price_return_pct'])
        payload['bars'][0]['date']='2026-04-06'
        self.assertTrue(compare_position(p,doc)['primary_split_checks'][0]['vendor_matches'])
    def test_rate_limit_stops_queue_and_releases_worker_lock(self):
        with sqlite3.connect(self.path) as db:db.executemany('INSERT INTO paper_positions(id,ticker,entry_date) VALUES(?,?,?)',[(1,'TEST','2026-10-05'),(2,'OTHER','2026-10-05')])
        class Immediate:
            def __init__(self,target,args,**kwargs):self.target,self.args=target,args
            def start(self):self.target(*self.args)
        error=type('YFRateLimitError',(Exception,),{})
        app=FastAPI();app.include_router(make_market_router(self.path));client=TestClient(app)
        with patch('market_history.Thread',Immediate),patch('market_history.fetch_history',side_effect=error()),patch('market_history.time.sleep'):
            self.assertEqual(client.post('/api/market-history/refresh',json={'tickers':['TEST','OTHER']}).status_code,200)
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM market_history_attempts').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT state FROM market_history_runs').fetchone()[0],'blocked')
            self.assertEqual(client.post('/api/market-history/refresh',json={'tickers':['TEST']}).status_code,429)
            with sqlite3.connect(self.path) as db:db.execute("UPDATE market_history_runs SET finished_at='2000-01-01T00:00:00Z'")
            self.assertEqual(client.post('/api/market-history/refresh',json={'tickers':['TEST']}).status_code,200)
    def test_next_batch_moves_past_unavailable_symbols_for_the_same_reference_day(self):
        with sqlite3.connect(self.path) as db:
            db.executemany('INSERT INTO paper_positions(id,ticker,entry_date) VALUES(?,?,?)',[(1,'AAA','2026-10-05'),(2,'BBB','2026-10-05')])
            db.execute("INSERT INTO market_history_runs(id,started_at,state,selected,message,tickers_json,end_date) VALUES(1,?,'partial',1,'Unavailable','[\"AAA\"]',?)",(AT,str(completed_day(datetime.now(timezone.utc).isoformat()))))
            db.execute("INSERT INTO market_history_attempts(run_id,ticker,observed_at,state,message) VALUES(1,'AAA',?,'unavailable','Fixture')",(AT,))
        app=FastAPI();app.include_router(make_market_router(self.path));client=TestClient(app)
        with patch('market_history.Thread') as thread:
            self.assertEqual(client.post('/api/market-history/refresh',json={'limit':1}).status_code,200)
            self.assertEqual(thread.call_args.kwargs['args'][1][0][0],'BBB')
    def test_copy_ignores_current_financial_scores_and_excludes_stale_or_flagged_signals(self):
        s=signal();s['quant_score']=999
        self.assertEqual(eligible(s),[])
        for fields in ({'first_seen_at':'2026-10-13T00:00:00Z','available_at':'2026-10-13T00:00:00Z'},{'amendment':True},{'footnotes':{'F1':'context'}},{'trading_plan':'true'},{'instrument':'ETF'},{'source_verification':'vendor_only'}):
            self.assertTrue(eligible({**s,**fields}))
    def test_copy_entry_is_after_first_observation_and_costs_match_benchmark_window(self):
        docs=self.documents();result=study_event(signal(),docs,AT)
        self.assertEqual(result['entry_date'],'2026-10-06');self.assertEqual(result['exit_date'],'2026-10-12')
        factor=(1-Decimal('.002'))/(1+Decimal('.002'))
        expected=(Decimal(105)/Decimal(101)*factor-1)*100
        self.assertAlmostEqual(result['net_reference_return_pct'],float(expected))
        self.assertEqual(result['excess_reference_return_pct'],0)
        self.assertIsNone(result['executed_copy_return_pct'])
    def test_copy_rejects_future_price_archives_missing_benchmark_and_incomplete_windows(self):
        docs=self.documents()
        early=study_event(signal(),docs,'2026-10-07T23:00:00Z');self.assertIsNone(early['net_reference_return_pct'])
        self.assertIn('after this study',early['reasons'][0])
        missing=study_event(signal(),{'TEST':docs['TEST']},AT);self.assertEqual(missing['status'],'needs_reference_prices')
        docs['TEST']['history']['bars']=docs['TEST']['history']['bars'][:2]
        self.assertEqual(study_event(signal(),docs,AT)['status'],'pending_reference_window')
    def test_copy_blocks_asset_and_benchmark_actions_and_replays_prior_policy_explicitly(self):
        prior=dict(POLICY,version='copy-close-reference-v1');prior.pop('corporate_actions')
        for symbol in ('TEST','SPY'):
            docs=self.documents();docs[symbol]['history']['bars'][2]['split_ratio']=6.665
            result=study_event(signal(),docs,AT)
            self.assertIsNone(result['net_reference_return_pct'])
            self.assertIn('distribution or uncorroborated',result['reasons'][0])
            original=study_event(signal(),docs,AT,policy=prior)
            self.assertEqual(original['status'],'reference_study_only')
        docs=self.documents()
        reviews={'splits':[],'distributions':[dict(ticker='TEST',effective_date='2026-10-07')]}
        self.assertIsNone(study_event(signal(),docs,AT,action_reviews=reviews)['net_reference_return_pct'])
    def test_copy_study_retains_input_policy_and_never_promotes_actual_returns(self):
        self.documents();s=signal()
        with sqlite3.connect(self.path) as db:db.execute('INSERT INTO trade_events(id,kind,event_json,first_seen_at) VALUES(?,?,?,?)',(s['id'],'insiders',json.dumps(s),s['first_seen_at']))
        result=create_study(self.path,AT)
        self.assertEqual(result['completed_reference_studies'],1);self.assertEqual(result['verified_executed_returns'],0)
        with sqlite3.connect(self.path) as db:
            stored=json.loads(db.execute('SELECT inputs_json FROM copy_reference_studies').fetchone()[0]);self.assertEqual(stored['policy'],POLICY)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM copy_reference_studies')
    def test_preview_never_writes_and_commit_carries_split_and_closed_cash(self):
        events=[event('deposit',1,amount='1000'),event('buy',2,ticker='TEST',shares='100',price='2',fee='1'),event('split',3,ticker='TEST',new_shares='1',old_shares='20'),event('sell',4,ticker='TEST',shares='5',price='44',fee='1')]
        preview=preview_import(self.path,events)
        self.assertEqual(preview['result']['cash'],'1018');self.assertEqual(read_ledger(self.path)['event_count'],0)
        self.assertEqual(commit_import(self.path,events,preview['preview_token'])['created'],4)
        again=preview_import(self.path,events);self.assertEqual(again['new_events'],0);self.assertEqual(again['identical_existing_events'],4)
    def test_entire_import_rejected_for_duplicate_foreign_currency_and_late_failure(self):
        deposit=event('deposit',1,amount='100')
        for events in ([deposit,deposit],[{**deposit,'currency':'JPY'}],[deposit,event('buy',2,ticker='TEST',shares='20',price='10')]):
            with self.assertRaises(ValueError):preview_import(self.path,events)
            self.assertEqual(read_ledger(self.path)['event_count'],0)
    def test_stale_preview_and_changed_content_cannot_commit(self):
        events=[event('deposit',2,amount='100')];preview=preview_import(self.path,events)
        with self.assertRaises(ValueError):commit_import(self.path,[{**events[0],'amount':'200'}],preview['preview_token'])
        append_event(self.path,event('deposit',1,amount='50'))
        with self.assertRaises(ValueError):commit_import(self.path,events,preview['preview_token'])
        self.assertEqual(read_ledger(self.path)['event_count'],1)
    def test_database_failure_rolls_back_the_entire_commit(self):
        events=[event('deposit',1,amount='100'),event('fee',2,amount='1')];preview=preview_import(self.path,events)
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON paper_ledger_events WHEN NEW.event_id='fee-2' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):commit_import(self.path,events,preview['preview_token'])
        self.assertEqual(read_ledger(self.path)['event_count'],0)

if __name__=='__main__':unittest.main()
