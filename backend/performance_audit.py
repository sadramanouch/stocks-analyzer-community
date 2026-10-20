"""Versioned arithmetic and evidence audit. Missing performance is never zero."""
import hashlib
import gzip
import json
import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from research_store import encoded, immutable, now
from paper_ledger import init_ledger

VERSION = 'performance-evidence-v8'


def init_performance(path):
    with sqlite3.connect(path) as db:
        init_ledger(db)
        db.execute('''CREATE TABLE IF NOT EXISTS performance_audits (
            id INTEGER PRIMARY KEY, generated_at TEXT NOT NULL, methodology TEXT NOT NULL,
            input_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL)''')
        immutable(db, 'performance_audits')
        db.execute('''CREATE TABLE IF NOT EXISTS performance_audit_inputs (
            sha256 TEXT PRIMARY KEY, captured_at TEXT NOT NULL, payload_gzip BLOB NOT NULL)''')
        immutable(db, 'performance_audit_inputs')


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def discrepancy(actual, expected, tolerance):
    value = number(actual)
    return value is None or abs(value - expected) > Decimal(tolerance)


def audit(db, at):
    db.row_factory = sqlite3.Row
    positions = [dict(r) for r in db.execute('SELECT * FROM paper_positions ORDER BY id')]
    snapshots = [dict(r) for r in db.execute('SELECT * FROM position_snapshots ORDER BY id')]
    events = [json.loads(r[0]) for r in db.execute('SELECT event_json FROM trade_events ORDER BY id')]
    sources = {
        'paper_positions':positions, 'position_snapshots':snapshots,
        'trade_events':events,
        'ownership_documents':[list(r) for r in db.execute('SELECT accession,sha256,observed_at FROM ownership_documents ORDER BY accession')],
        'legacy_ownership_documents':[list(r) for r in db.execute('SELECT accession,sha256,observed_at,review_json FROM legacy_ownership_documents ORDER BY accession')],
        'paper_ledger_events':[list(r) for r in db.execute('SELECT * FROM paper_ledger_events ORDER BY rowid')],
    }
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='market_history_documents'").fetchone():
        sources['market_history_documents']=[list(r) for r in db.execute('SELECT * FROM market_history_documents ORDER BY id')]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='copy_reference_studies'").fetchone():
        sources['copy_reference_studies']=[list(r) for r in db.execute('SELECT * FROM copy_reference_studies ORDER BY id')]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='market_source_documents'").fetchone():
        # Hex retains exact original bytes, including HTML encoding and failures.
        sources['market_source_documents']=[list(r) for r in db.execute('SELECT id,ticker,observed_at,source_url,source_kind,status_code,request_json,sha256,hex(body_bytes) FROM market_source_documents ORDER BY id')]
    for table in ('equity_decisions','equity_paper_previews','forward_checkpoints','benchmark_studies','benchmark_observations','broker_record_bundles','broker_flex_bundles','source_campaigns','source_campaign_receipts'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
            sources[table]=[list(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='financial_source_documents'").fetchone():
        sources['financial_source_documents']=[list(r) for r in db.execute('SELECT id,cik,observed_at,source_url,status_code,sha256,hex(body_gzip) FROM financial_source_documents ORDER BY id')]
    # Preserve all exact inputs, including invalid numeric values that must be flagged.
    input_bytes = json.dumps(sources,sort_keys=True,separators=(',',':'),allow_nan=True).encode()
    fingerprint = hashlib.sha256(input_bytes).hexdigest()
    by_id = {p['id']:p for p in positions}
    issues, counts = [], Counter()
    def flag(kind, row, detail):
        counts[kind] += 1
        if len(issues) < 100:
            issues.append({'kind':kind, **row, 'detail':detail})
    valid_positions = {}
    for p in positions:
        row = {'position_id':p['id'], 'ticker':p['ticker']}
        try:
            start = date.fromisoformat(p['entry_date'][:10])
            end = date.fromisoformat(p['close_date'][:10]) if p['status']=='closed' else date.fromisoformat(at[:10])
            if start > end or end > date.fromisoformat(at[:10]) or p['status'] not in ('open','closed'):
                raise ValueError()
        except (ValueError, TypeError):
            flag('invalid_position_dates',row,'Holding dates/status cannot be used for coverage checks.')
            continue
        entry, allocation = number(p['entry_price']), number(p['virtual_dollars'])
        if entry is None or allocation is None or entry<=0 or allocation<=0:
            flag('invalid_position_amounts',row,'Entry price and allocation must be finite and positive.')
            continue
        valid_positions[p['id']] = (start,end,entry,allocation)
        if p['status']=='closed':
            exit_price = number(p['close_price'])
            if exit_price is None or exit_price<=0:
                flag('invalid_exit_price',row,'Recorded close price is missing or nonpositive.')
            else:
                expected = (exit_price/entry-1)*100
                if discrepancy(p['realized_return_pct'],expected,'.011'):
                    flag('closed_return_mismatch',row,'Stored percentage differs from the recorded entry/exit price ratio by more than 0.011 percentage points.')
    coverage = {}
    checked = 0
    for s in snapshots:
        p = by_id.get(s['position_id'])
        row = {'snapshot_id':s['id'], 'position_id':s['position_id'], 'ticker':p['ticker'] if p else None, 'date':s['snapshot_date']}
        if p is None:
            flag('orphan_snapshot',row,'No original position exists for this snapshot.')
            continue
        try:
            day = date.fromisoformat(s['snapshot_date'])
            if day > date.fromisoformat(at[:10]):
                raise ValueError()
        except (ValueError, TypeError):
            flag('invalid_snapshot_date',row,'Snapshot date is invalid or in the future.')
            continue
        coverage.setdefault(day,set()).add(p['id'])
        if p['id'] not in valid_positions:
            continue
        start,end,entry,allocation = valid_positions[p['id']]
        if not start<=day<=end:
            flag('snapshot_outside_holding_dates',row,'Snapshot lies outside the recorded holding period.')
        mark = number(s['current_price'])
        if mark is None or mark<=0:
            flag('invalid_snapshot_price',row,'Snapshot price is missing, nonpositive or nonfinite.')
            continue
        expected = (mark/entry-1)*100
        checked += 1
        if discrepancy(s['return_pct'],expected,'.011'):
            flag('snapshot_return_mismatch',row,'Stored percentage differs from the recorded price ratio by more than 0.011 percentage points.')
        if discrepancy(s['unrealized_pnl'],allocation*expected/100,'.011'):
            flag('snapshot_pnl_mismatch',row,'Stored dollar P&L differs from allocation × recorded price return by more than $0.011.')
    dates = []
    for day, recorded in sorted(coverage.items()):
        expected = {i for i,(start,end,_,_) in valid_positions.items() if start<=day<=end}
        dates.append({'date':day.isoformat(),'recorded_positions':len(recorded),'expected_positions':len(expected),
                      'missing_positions':len(expected-recorded),'outside_positions':len(recorded-expected)})
    primary = [e for e in events if e.get('accession')]
    purchases = [e for e in primary if e.get('transaction_code')=='P' and e.get('table')=='nonDerivative']
    owners = {e['actor_id'] for e in primary}
    legacy_reviews = [json.loads(r[3]) for r in sources['legacy_ownership_documents']]
    report = {'methodology':VERSION, 'generated_at':at, 'input_sha256':fingerprint,
        'source_counts':{name:len(rows) for name,rows in sources.items()},
        'arithmetic':{'positions_checked':len(positions),'snapshots_checked':checked,
            'issue_counts':dict(counts),'issue_count':sum(counts.values()),'examples':issues,
            'examples_truncated':sum(counts.values())>len(issues),
            'tolerance':'0.011 percentage points or $0.011; arithmetic uses recorded prices, without validating their historical basis.'},
        'snapshot_coverage':{'recorded_days':len(dates),'days_with_missing_positions':sum(d['missing_positions']>0 for d in dates),
            'days_with_outside_positions':sum(d['outside_positions']>0 for d in dates),'days':dates,
            'basis':'Coverage checked only on dates with saved snapshots; this is not a trading-calendar completeness claim. Same-day closures may precede the snapshot.'},
        'legacy_review':{'documents':len(legacy_reviews),'context_rows':sum(len(r['transactions']) for r in legacy_reviews),
            'documents_with_mapping_gaps':sum(r['status']=='needs_manual_mapping' for r in legacy_reviews),
            'transaction_codes':dict(Counter(t['transaction_code'] for r in legacy_reviews for t in r['transactions']))},
        'purchase_evidence':[{k:e.get(k) for k in ('actor','actor_id','asset','accession','traded_at_display','available_at','source_url','source_verification','reported_shares','reported_price')} for e in purchases],
        'metrics':[
            {'metric':'Trader profitability','status':'unverified','return_pct':None,
             'evidence':f'{len(owners)} observed actor IDs; {len(primary)} XML transaction rows. Legacy HTML rows remain separate context.',
             'blockers':['Complete actor portfolios, initial cost basis, transfers and instruments outside these issuers are unknown.',
                         'Awards, exercise proceeds, taxes, gifts, derivatives, amendments and corporate actions have not been reconciled.']},
            {'metric':'Copy-trading returns','status':'unverified','return_pct':None,
             'evidence':f'{len(purchases)} reported non-derivative purchase rows; {sum(e.get("source_verification")=="primary_parsed" for e in purchases)} pass the XML field parser without review flags.',
             'blockers':['No archived entry/exit price series tied to executable times after this system first observed each signal.',
                         'A fixed strategy, complete eligible signal universe, fees, slippage and corporate actions are required. Historical filing dates cannot substitute for observed availability.']},
            {'metric':'Historical portfolio returns','status':'unverified','return_pct':None,
             'evidence':f'{len(positions)} original paper positions; {len(snapshots)} saved snapshots; {len(sources["paper_ledger_events"])} events in the separate cash/share ledger.',
             'blockers':['Legacy positions lack documented cash funding, share quantities, quote timestamps and adjusted/unadjusted price basis.',
                         'Closed-position cash, all splits/dividends and trading costs are missing. Changing snapshot coverage prevents a comparable account return.']}
        ], 'methodology_note':'The original rows are preserved. Passing arithmetic checks does not verify market data or investment performance. Missing returns remain null.'}
    return report, input_bytes


def create_audit(path):
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('BEGIN')
        report, input_bytes = audit(db,now())
        payload = encoded(report)
        sha = hashlib.sha256(payload.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO performance_audit_inputs VALUES(?,?,?)',
                   (report['input_sha256'],report['generated_at'],gzip.compress(input_bytes,mtime=0)))
        audit_id = db.execute('INSERT INTO performance_audits(generated_at,methodology,input_sha256,content_sha256,payload_json) VALUES(?,?,?,?,?)',
            (report['generated_at'],VERSION,report['input_sha256'],sha,payload)).lastrowid
    return {**report,'audit_id':audit_id,'content_sha256':sha}


def make_performance_router(path):
    router = APIRouter(prefix='/api/performance-audit', tags=['Performance evidence'])
    @router.get('')
    def latest():
        with sqlite3.connect(path,timeout=15) as db:
            row = db.execute('SELECT id,content_sha256,payload_json FROM performance_audits ORDER BY id DESC LIMIT 1').fetchone()
        if row is None:
            return {'audit_id':None,'metrics':[],'message':'Run an audit to capture current source evidence.'}
        if hashlib.sha256(row[2].encode()).hexdigest()!=row[1]:
            raise HTTPException(409,'Archived audit hash mismatch')
        return {**json.loads(row[2]),'audit_id':row[0],'content_sha256':row[1]}
    @router.post('')
    def refresh():
        return create_audit(path)
    @router.get('/{audit_id}/inputs')
    def inputs(audit_id:int):
        with sqlite3.connect(path,timeout=15) as db:
            row = db.execute('SELECT a.input_sha256,i.payload_gzip FROM performance_audits a JOIN performance_audit_inputs i ON i.sha256=a.input_sha256 WHERE a.id=?',(audit_id,)).fetchone()
        if row is None:
            raise HTTPException(404,'Audit inputs not found')
        raw = gzip.decompress(row[1])
        if hashlib.sha256(raw).hexdigest()!=row[0]:
            raise HTTPException(409,'Archived audit input hash mismatch')
        # Exact bytes include any invalid source numeric literals; preserve them for audit.
        from fastapi.responses import Response
        return Response(raw,media_type='application/json',headers={'Content-Disposition':f'attachment; filename="performance-audit-{audit_id}-inputs.json"'})
    return router
