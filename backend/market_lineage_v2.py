"""Additional frozen symbol policy: bind former and current registered classes."""
import json
from market_lineage import base_report, source, aware
from security_identity import cover_class

METHOD='unchanged-cusip-symbol-history-v2'
LINKS={
 'LSH':{'provider_symbol':'QNME','cik':'0001996192','exchange':'Nasdaq','chart_exchange':'NCM',
        'effective_date':'2026-08-04','filing_date':'2026-08-04','accession':'0001213900-26-085083',
        'notice_url':'https://www.sec.gov/Archives/edgar/data/1996192/000121390026085083/ea0300505-8k_quanome.htm',
        'anchors':['from “Lakeside Holding Limited” to “Quanome Technologies, Inc.”',
                   'Beginning with the opening of trading on August 4, 2026,', 'symbol “QNME”',
                   'There has been no change to the Common Stock’s CUSIP in connection with the Name Change Amendment or the Symbol Change.'],
        'filing':{'form':'8-K','accessionNumber':'0001213900-26-085083','filingDate':'2026-08-04',
                  'reportDate':'2026-08-03','primaryDocument':'ea0300505-8k_quanome.htm'},
        'former_filing':{'form':'10-Q','accessionNumber':'0001213900-26-018466','filingDate':'2026-02-19',
                  'reportDate':'2025-12-31','primaryDocument':'ea0276525-10q_lakeside.htm',
                  'url':'https://www.sec.gov/Archives/edgar/data/1996192/000121390026018466/ea0276525-10q_lakeside.htm'}}}


def reproduce(db,inputs):
    spec=LINKS.get(inputs['ticker'])
    report=base_report(db,inputs,spec,METHOD)
    original,body=source(db,inputs['former_id'],inputs['ticker'],'sec_former_symbol_filing',spec['former_filing']['url'])
    if original['request_json']!='{}' or not aware(original['observed_at']) or aware(original['observed_at'])>aware(inputs['captured_at']):
        raise ValueError('Former symbol source request or observation differs.')
    if spec['former_filing']['filingDate']>inputs['start_date']:
        raise ValueError('Former registered-class filing must precede this legacy holding.')
    index,_=source(db,inputs['index_id'],inputs['ticker'],'sec_symbol_submissions','https://data.sec.gov/submissions/CIK'+spec['cik']+'.json')
    index_body=db.execute('SELECT body_bytes FROM market_source_documents WHERE id=?',(index['id'],)).fetchone()[0]
    recent=json.loads(index_body)['filings']['recent'];fields=('form','accessionNumber','filingDate','reportDate','primaryDocument')
    if any(not isinstance(recent.get(k),list) for k in fields) or len({len(recent[k]) for k in fields})!=1: raise ValueError('Registered-class filing index arrays differ.')
    for f in (spec['former_filing'],spec['filing']):
        rows=[{k:recent[k][i] for k in fields} for i,a in enumerate(recent['accessionNumber']) if a==f['accessionNumber']]
        if rows!=[{k:f[k] for k in fields}]: raise ValueError('Former or current registered-class filing identity differs.')
    former=cover_class(body,inputs['ticker'],spec['cik'],spec['former_filing'],{'exchange':'NASDAQ'})
    _,current_body=source(db,inputs['notice_id'],inputs['ticker'],'sec_symbol_notice',spec['notice_url'])
    current=cover_class(current_body,spec['provider_symbol'],spec['cik'],spec['filing'],{'exchange':'NASDAQ'})
    report.update(former_registered_class=former,current_registered_class=current,former_filing=spec['former_filing'])
    report['sources'].append(original)
    return report
