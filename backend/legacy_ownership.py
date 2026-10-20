"""Archive and inspect pre-XML Form 4s without inventing executions or returns."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from research_store import encoded, immutable, now

VERSION = 'legacy-form4-review-v1'
# This one decorative layout was compared cell-by-cell to the original filing.
# It is deliberately content-bound, not a heuristic for arbitrary HTML.
REVIEWED_LAYOUT_SHA = '76cb3947298ca73a79f81cd91c19efd61d4d49da9a8144b23bc40999cff91a9c'


def init_legacy(path):
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS legacy_ownership_documents (
            accession TEXT PRIMARY KEY, ticker TEXT NOT NULL, source_url TEXT NOT NULL,
            sha256 TEXT NOT NULL, observed_at TEXT NOT NULL, document_text TEXT NOT NULL,
            review_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS legacy_ownership_runs (
            id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, state TEXT NOT NULL, selected INTEGER NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL);''')
        immutable(db, 'legacy_ownership_documents')


def references(db, ticker=''):
    """Deduplicate raw index revisions; inconsistent identities are never merged."""
    result = {}
    for symbol, cik, url, sha, raw in db.execute("SELECT d.ticker,c.cik,d.source_url,d.sha256,d.document_json FROM ownership_index_documents d JOIN companies c ON c.ticker=d.ticker WHERE d.kind='historical' AND (?='' OR d.ticker=?)", (ticker, ticker)):
        if hashlib.sha256(raw.encode()).hexdigest() != sha:
            raise ValueError('Archived filing index hash mismatch')
        j = json.loads(raw)
        fields = ('form', 'accessionNumber', 'filingDate', 'primaryDocument')
        if any(not isinstance(j.get(f), list) for f in fields) or len({len(j[f]) for f in fields}) != 1:
            raise ValueError('Invalid historical index arrays')
        for form, accession, filed, document in zip(*(j[f] for f in fields)):
            if form not in ('4', '4/A') or (document and document.lower().endswith('.xml')):
                continue
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession) or not str(cik).isdigit():
                raise ValueError('Invalid filing identity')
            ref = dict(ticker=symbol, cik=str(cik).lstrip('0'), accession=accession,
                       filed_date=date.fromisoformat(filed).isoformat(), form=form,
                       primary_document=document, index_url=url, index_sha256=sha)
            if accession in result and any(result[accession][k] != ref[k] for k in ('ticker', 'cik', 'filed_date', 'form', 'primary_document')):
                raise ValueError('Conflicting historical filing identities')
            result[accession] = ref
    return sorted(result.values(), key=lambda r: (r['ticker'], r['filed_date'], r['accession']))


def submission_url(ref):
    # SEC documents this alternate complete-submission path for post-May-2000 filings.
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', ref['accession']) or not re.fullmatch(r'[1-9]\d*', ref['cik']):
        raise ValueError('Invalid accession/issuer')
    return f"https://www.sec.gov/Archives/edgar/data/{ref['cik']}/{ref['accession'].replace('-', '')}/{ref['accession']}.txt"


class Tables(HTMLParser):
    """Retain cells and spans. Never evaluate or render filing HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.hidden, self.text = [], [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'table':
            t = {'rows': [], 'row': None, 'cell': None}
            self.tables.append(t)
            self.stack.append(t)
        if not self.stack:
            return
        t = self.stack[-1]
        if tag == 'tr':
            t['row'], t['cell'] = [], None
            t['rows'].append(t['row'])
        if tag in ('td', 'th') and t['row'] is not None:
            attr = dict(attrs)
            t['cell'] = {'text': '', 'colspan': attr.get('colspan', '1'), 'rowspan': attr.get('rowspan', '1')}
            t['row'].append(t['cell'])
        if tag in ('br', 'p', 'div') and t['cell'] is not None:
            t['cell']['text'] += ' '

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if not self.stack:
            return
        if tag == 'table':
            self.stack.pop()
        elif tag in ('td', 'th'):
            self.stack[-1]['cell'] = None

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)
        if self.stack and self.stack[-1]['cell'] is not None and not self.hidden:
            self.stack[-1]['cell']['text'] += data


def numeric(raw):
    # A numeric literal followed by numbered notes is retained as reported, with
    # the original cell alongside it. Ranges, formulas, N/A and blanks are unknown.
    clean = raw.strip().removeprefix('$')
    match = re.fullmatch(r'((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)(?:\s*\(\d+\))*', clean)
    if not match:
        return None
    try:
        value = Decimal(match[1].replace(',', ''))
        return str(value) if value.is_finite() and 0 <= value <= Decimal('1e18') else None
    except InvalidOperation:
        return None


def reported_date(raw, filed):
    match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})', raw)
    if not match:
        raise ValueError('Date has footnotes or an unsupported format')
    month, day, year = map(int, match.groups())
    if year < 100:
        year += (filed.year // 100) * 100
        if year > filed.year:
            year -= 100
    value = date(year, month, day)
    if value > filed:
        raise ValueError('Reported transaction follows filing')
    return value.isoformat()


def review_submission(raw, ref, observed_at):
    if len(raw) > 2_000_000:
        raise ValueError('Oversized complete submission')
    observed = datetime.fromisoformat(observed_at.replace('Z','+00:00'))
    if observed.tzinfo is None or observed.date() < date.fromisoformat(ref['filed_date']):
        raise ValueError('Observation cannot precede filing')
    text = raw.decode('utf-8', errors='strict')
    header = re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>', text, re.S | re.I)
    if not header:
        raise ValueError('Missing complete-submission header')
    header = header[1]
    def field(label):
        matches = re.findall(r'^' + re.escape(label) + r':\s*([^\r\n]+)', header, re.M)
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous ' + label)
        return matches[0].strip()
    if field('ACCESSION NUMBER') != ref['accession'] or field('CONFORMED SUBMISSION TYPE') != ref['form'] or field('FILED AS OF DATE') != ref['filed_date'].replace('-', ''):
        raise ValueError('Header accession/form/date differs from archived index')
    sections = re.findall(r'^(SUBJECT COMPANY|REPORTING-OWNER):\s*\n(.*?)(?=^(?:SUBJECT COMPANY|REPORTING-OWNER):|\Z)', header, re.M | re.S)
    subjects, owners = [], []
    for kind, body in sections:
        cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', body)
        name = re.search(r'COMPANY CONFORMED NAME:\s*([^\r\n]+)', body)
        if not cik or not name:
            raise ValueError('Missing header party identity')
        party = {'cik': cik[1].lstrip('0'), 'name': name[1].strip()}
        (subjects if kind == 'SUBJECT COMPANY' else owners).append(party)
    if len(subjects) != 1 or subjects[0]['cik'] != ref['cik'] or not owners:
        raise ValueError('Header issuer/owner cannot be matched')
    docs = re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>', text, re.S | re.I)
    primary = []
    for doc in docs:
        kind = re.search(r'<TYPE>\s*([^\r\n]+)', doc, re.I)
        filename = re.search(r'<FILENAME>\s*([^\r\n]+)', doc, re.I)
        body = re.search(r'<TEXT>(.*?)</TEXT>', doc, re.S | re.I)
        if kind and kind[1].strip() == ref['form'] and filename and filename[1].strip() == ref['primary_document'] and body:
            primary.append(body[1])
    if len(primary) != 1:
        raise ValueError('Primary document cannot be matched to archived index')
    parser = Tables()
    parser.feed(primary[0])
    plain = re.sub(r'\s+', ' ', ' '.join(parser.text)).strip()
    explanation = re.search(r'Explanation of Responses:\s*(.*?)(?=(?:\*\*\s*)?Signature of Reporting Person|\Z)',plain,re.I)
    tables, transactions, holdings, unresolved = [], [], [], []
    reviewed_layout = hashlib.sha256(raw).hexdigest() == REVIEWED_LAYOUT_SHA
    for number, table in enumerate(parser.tables, 1):
        rows = [[{**c, 'text': re.sub(r'\s+', ' ', c['text']).strip()} for c in row] for row in table['rows']]
        header_rows = [i for i, row in enumerate(rows) if 'title of' in ' '.join(c['text'] for c in row).lower() and 'instr.' in ' '.join(c['text'] for c in row).lower()]
        if not header_rows:
            continue
        tables.append({'table_number': number, 'rows': rows})
        i = header_rows[0]
        cells = rows[i]
        derivative = 'title of derivative' in ' '.join(c['text'] for c in cells).lower()
        if reviewed_layout and number == 5 and derivative:
            # All derivative data cells in this exact source were blank.
            if any(c['text'] for row in rows[i+3:] for c in row):
                raise ValueError('Reviewed blank derivative layout no longer matches')
            continue
        manual = reviewed_layout and number == 3 and not derivative
        width, columns = (16, 12) if derivative else (11, 8)
        expected_spans = [1,1,1,1,2,2,2,2,1,1,1,1] if derivative else [1,1,1,2,3,1,1,1]
        child_expected = ('code','v','a','d') if derivative else ('code','v','amount')
        child = [c['text'].lower().strip('()') for c in rows[i+1]] if i+1 < len(rows) else []
        if not manual and (len(cells) != columns or [c['colspan'] for c in cells] != list(map(str, expected_spans)) or tuple(child[:len(child_expected)]) != child_expected):
            unresolved.append({'table_number': number, 'reason': 'Unsupported HTML column layout; every raw cell retained for manual mapping.'})
            continue
        # Verify the semantically important grouped headings before using offsets.
        headings = [re.sub(r'-\s*', '', c['text'].lower()) for c in cells]
        expected_headings = [r'^1\.\s*title of derivative security', r'^2\.\s*conversion or exercise price',
            r'^3\.\s*transaction date', r'^3a\.\s*deemed execution date', r'^4\.\s*transaction code',
            r'^5\.\s*number of derivative securities acquired', r'^6\.\s*date exercisable',
            r'^7\.\s*title and amount of underlying securities', r'^8\.\s*price of derivative security',
            r'^9\.\s*number of derivative securities', r'^10\.\s*ownership form', r'^11\.\s*nature of indirect'] if derivative else [
            r'^1\.\s*title of security', r'^2\.\s*transaction date', r'^2a\.\s*deemed execution date',
            r'^3\.\s*transaction code', r'^4\.\s*securities acquired', r'^5\.\s*amount of securities',
            r'^6\.\s*ownership form', r'^7\.\s*nature of indirect']
        child_valid = len(child)==8 and child[6]=='title' and child[7].startswith('amount') if derivative else len(child)==5 and child[3] in ('a/d','a) or (d') and child[4]=='price'
        if not manual and (not all(re.search(pattern,value) for pattern,value in zip(expected_headings,headings)) or not child_valid):
            unresolved.append({'table_number': number, 'reason': 'Unrecognized column headings.'})
            continue
        start = i+3 if manual else i+2
        for line, row in enumerate(rows[start:], start+1):
            values = [c['text'] for c in row]
            if not any(values):
                continue
            if len(row)==1 and row[0]['colspan']==str(width) and values[0].startswith('* The reporting officer disclaims beneficial ownership'):
                # Explicit explanatory prose, not a missing trade row; retained in raw cells.
                continue
            source_cells = values
            if manual:
                chosen = {0:0, 1:2, 3:10, 5:16, 6:18, 7:20}
                if len(values)!=33 or any(v for n,v in enumerate(values) if n not in chosen.values()):
                    raise ValueError('Reviewed decorative data layout no longer matches')
                values = [source_cells[chosen[n]] if n in chosen else '' for n in range(11)]
            if not manual and (len(values) != width or any(c['colspan'] != '1' or c['rowspan'] != '1' for c in row)):
                unresolved.append({'table_number': number, 'row_number': line, 'reason': 'Merged or unexpected data cells; raw row retained.'})
                continue
            date_raw, code_raw = values[2 if derivative else 1], values[4 if derivative else 3]
            code = code_raw[0] if re.fullmatch(r'[A-Z](?:\s*\(\d+\))*',code_raw) else code_raw
            if not date_raw and not code:
                holdings.append({'table_number': number, 'row_number': line, 'cells': values})
                continue
            result = {'table_number': number, 'row_number': line, 'table': 'derivative' if derivative else 'nonDerivative', 'security': values[0], 'cells': values, 'source_cells':source_cells,
                      'mapping': 'source_hash_bound_review' if manual else 'validated_standard_columns', 'transaction_code': code, 'issues': []}
            result['reported_code_cell'] = code_raw
            result['numbered_annotations'] = sorted(set(re.findall(r'\((\d+)\)', ' '.join(values))))
            try:
                result['reported_date'] = reported_date(date_raw, date.fromisoformat(ref['filed_date']))
            except ValueError as e:
                result['issues'].append(str(e))
            if not re.fullmatch('[A-Z]', code):
                result['issues'].append('Unresolved transaction code')
            if derivative:
                a, d = numeric(values[6]), numeric(values[7])
                direction = 'A' if a and Decimal(a)>0 and not d else 'D' if d and Decimal(d)>0 and not a else None
                shares = a if direction == 'A' else d if direction == 'D' else None
            else:
                shares, direction = numeric(values[5]), values[6]
            price = numeric(values[12 if derivative else 7])
            result.update(reported_shares=shares, reported_price=price, direction=direction,
                          reported_notional=str(Decimal(shares)*Decimal(price)) if shares and price else None)
            if not shares or Decimal(shares) <= 0 or direction not in ('A', 'D') or (code=='P' and direction!='A') or (code=='S' and direction!='D'):
                result['issues'].append('Share amount or direction requires manual mapping')
            if price is None and code in ('P','S'):
                result['issues'].append('Reported price is blank, footnoted or not a single numeric value')
            # All legacy rows remain context: table arithmetic never proves discretionary execution.
            result['source_verification'] = 'legacy_context_review'
            transactions.append(result)
    if not tables:
        unresolved.append({'reason': 'No recognized ownership table headings; raw document retained.'})
    return {**ref, 'schema': VERSION, 'observed_at': observed_at, 'issuer': subjects[0], 'owners': owners,
            'amendment':ref['form']=='4/A', 'explanation_text':explanation[1][:10000].strip() if explanation else '',
            'identity_status': 'header_matched', 'tables': tables, 'transactions': transactions,
            'holdings_only_rows': holdings, 'unresolved': unresolved,
            'transaction_codes': dict(Counter(t['transaction_code'] for t in transactions)),
            'status': 'needs_manual_mapping' if unresolved or any(t['issues'] for t in transactions) else 'context_reviewed',
            'copy_eligible': False, 'trader_return': None,
            'limitations': ['Header identity, filing date and explicit table cells were checked against the archived index.',
                'Raw quantities and numeric prices are reported facts; not broker executions, tax-adjusted profit or discretionary signals.',
                'Footnotes, options, cancellations, gifts and amendments require economic reconciliation. No legacy row enables copy research.',
                'Availability begins at this archive observation; historical filing dates do not establish when this system could trade.']}


def save_legacy(path, raw, ref, observed_at=None):
    observed_at = observed_at or now()
    review = review_submission(raw, ref, observed_at)
    sha = hashlib.sha256(raw).hexdigest()
    with sqlite3.connect(path, timeout=30) as db:
        old = db.execute('SELECT sha256 FROM legacy_ownership_documents WHERE accession=?', (ref['accession'],)).fetchone()
        if old:
            if old[0] != sha:
                raise ValueError('Existing accession content changed; source reconciliation required')
            return False
        db.execute('INSERT INTO legacy_ownership_documents VALUES(?,?,?,?,?,?,?)', (ref['accession'], ref['ticker'], submission_url(ref), sha, observed_at, raw.decode('utf-8'), encoded(review)))
    return True


def summaries(db, ticker=''):
    saved = {}
    for accession, sha, at, raw in db.execute("SELECT accession,sha256,observed_at,review_json FROM legacy_ownership_documents WHERE (?='' OR ticker=?)", (ticker, ticker)):
        review = json.loads(raw)
        saved[accession] = {'sha256':sha, 'observed_at':at, 'status':review['status'], 'owners':review['owners'],
            'transactions':len(review['transactions']), 'unresolved_tables_or_rows':len(review['unresolved']),
            'rows_with_issues':sum(bool(t['issues']) for t in review['transactions']), 'transaction_codes':review['transaction_codes']}
    refs = references(db, ticker)
    result = [{**ref, **saved.get(ref['accession'], {'status':'unreviewed'})} for ref in refs]
    return {'total':len(result), 'archived':sum(r['accession'] in saved for r in result),
            'context_reviewed':sum(r['status']=='context_reviewed' for r in result),
            'needs_manual_mapping':sum(r['status']=='needs_manual_mapping' for r in result),
            'unreviewed':sum(r['status']=='unreviewed' for r in result), 'references':result,
            'copy_eligible':0, 'methodology':VERSION}


class ReviewRequest(BaseModel):
    ticker: str = Field(default='', max_length=16, pattern=r'^[A-Za-z0-9.\-^]*$')
    limit: int = Field(default=25, ge=1, le=100)


def make_legacy_router(path):
    router = APIRouter(prefix='/api/trade-history/legacy', tags=['Legacy ownership review'])
    lock = threading.Lock()

    @router.get('')
    def overview(ticker: str = Query('', max_length=16, pattern=r'^[A-Za-z0-9.\-^]*$'), offset: int = Query(0,ge=0), limit: int = Query(20,ge=1,le=100)):
        with sqlite3.connect(path,timeout=15) as db:
            result = summaries(db, ticker.upper())
            db.row_factory = sqlite3.Row
            runs = [dict(r) for r in db.execute('SELECT * FROM legacy_ownership_runs ORDER BY id DESC LIMIT 3')]
        result['references'] = result['references'][offset:offset+limit]
        return {**result, 'offset':offset, 'limit':limit, 'runs':runs}

    @router.get('/documents/{accession}')
    def document(accession: str):
        with sqlite3.connect(path,timeout=15) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM legacy_ownership_documents WHERE accession=?',(accession,)).fetchone()
        if not row:
            raise HTTPException(404,'Legacy document has not been archived')
        result = dict(row)
        result['review'] = json.loads(result.pop('review_json'))
        return result

    def run(run_id, refs):
        reviewed = failed = 0
        state, message = 'bounded', 'Selected original filings archived and reviewed; no performance claims established.'
        try:
            for ref in refs:
                try:
                    r = requests.get(submission_url(ref), headers={'User-Agent':os.environ.get('EDGAR_USER_AGENT','StockAnalyzer https://github.com/sadramanouch/stocks-analyzer')}, timeout=(5,20), allow_redirects=False)
                    if r.status_code in (403,429,503):
                        failed += 1
                        state, message = 'blocked', f'SEC HTTP {r.status_code}; stopped requests. Existing evidence retained.'
                        break
                    if r.status_code != 200:
                        raise ValueError(f'HTTP {r.status_code}')
                    save_legacy(path, r.content, ref)
                    reviewed += 1
                except (requests.RequestException, ValueError, UnicodeError) as e:
                    failed += 1
                    message = f"{ref['accession']}: {str(e)[:200]}"
                finally:
                    with sqlite3.connect(path,timeout=30) as db:
                        db.execute('UPDATE legacy_ownership_runs SET reviewed=?,failed=?,message=? WHERE id=?',(reviewed,failed,message,run_id))
                time.sleep(.3)
            if state != 'blocked' and failed:
                state = 'partial'
        except Exception:
            state, message = 'blocked', 'Review interrupted by a storage/source failure; existing evidence retained.'
        finally:
            try:
                with sqlite3.connect(path,timeout=30) as db:
                    db.execute('UPDATE legacy_ownership_runs SET state=?,finished_at=?,reviewed=?,failed=?,message=? WHERE id=?',(state,now(),reviewed,failed,message,run_id))
            finally:
                lock.release()

    @router.post('/review')
    def review(body: ReviewRequest):
        if not lock.acquire(False):
            raise HTTPException(409,'A legacy filing review is running')
        try:
            with sqlite3.connect(path,timeout=15) as db:
                archived = {r[0] for r in db.execute('SELECT accession FROM legacy_ownership_documents')}
                refs = [r for r in references(db,body.ticker.upper()) if r['accession'] not in archived][:body.limit]
                if not refs:
                    raise HTTPException(409,'All legacy references in this scope are already archived. Inspect the remaining manual mapping issues.')
                run_id = db.execute("INSERT INTO legacy_ownership_runs(ticker,started_at,state,selected,message) VALUES(?,?,'running',?,'Reviewing original legacy submissions')",(body.ticker.upper(),now(),len(refs))).lastrowid
            threading.Thread(target=run,args=(run_id,refs),daemon=True).start()
            return {'run_id':run_id,'selected':len(refs)}
        except Exception:
            lock.release()
            raise
    return router
