"""Read-only legacy reconciliation. Confirmed actions never certify price basis."""
from datetime import date
import sqlite3
from financial_integrity import finite

CONFIRMED_SPLITS = [
    # Dates below are the start of split-adjusted market trading, rather than
    # a board approval date or the prior evening's legal effective time.
    {'ticker': 'SVRN', 'effective_date': '2026-03-30', 'new_shares': 1, 'old_shares': 20,
     'source': 'https://www.sec.gov/Archives/edgar/data/1869467/000091957426001900/d12128691_ex99-1.htm'},
    {'ticker': 'HCAI', 'effective_date': '2026-04-13', 'new_shares': 1, 'old_shares': 30,
     'source': 'https://www.sec.gov/Archives/edgar/data/1958399/000121390026041513/ea028499601ex99-1.htm'},
    {'ticker': 'ADV', 'effective_date': '2026-03-27', 'new_shares': 1, 'old_shares': 25,
     'source': 'https://www.sec.gov/Archives/edgar/data/1776661/000119312526109473/d14474d8k.htm',
     'corroborating_source': 'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-183',
     'note': 'Trading date March 27; the filing gives legal effectiveness at 5 p.m. ET on March 26.'},
    {'ticker': 'SKK', 'effective_date': '2026-04-06', 'new_shares': 1, 'old_shares': 10,
     'source': 'https://www.sec.gov/Archives/edgar/data/1991261/000149315226014559/ex99-1.htm'},
    {'ticker': 'HKIT', 'effective_date': '2026-04-06', 'new_shares': 1, 'old_shares': 50,
     'source': 'https://www.sec.gov/Archives/edgar/data/1742341/000121390026038051/ea0284045-6k_hitek.htm',
     'corroborating_source': 'https://www.sec.gov/Archives/edgar/data/1742341/000121390026047560/ea0286182-20f_hitek.htm'},
    {'ticker': 'TLIH', 'effective_date': '2026-05-01', 'new_shares': 1, 'old_shares': 10,
     'requires_review': True,
     'source': 'https://www.sec.gov/Archives/edgar/data/1982012/000149315226019456/ex99-1.htm',
     'corroborating_source': 'https://www.sec.gov/Archives/edgar/data/1982012/000149315226045028/ex99-1.htm',
     'note': 'The announcement specifies May 1 split-adjusted trading. Later financial statements describe April 13 effectiveness and May 1 fractional-share issuance; the legal date needs reconciliation. Only the May 1 trading action is compared, once.'},
    {'ticker': 'BIYA', 'effective_date': '2026-07-13', 'new_shares': 1, 'old_shares': 10,
     'source': 'https://www.sec.gov/Archives/edgar/data/1944712/000121390026076193/ea029733701ex99-1.htm'},
    {'ticker': 'BOXL', 'effective_date': '2026-06-22', 'new_shares': 1, 'old_shares': 6,
     'source': 'https://www.sec.gov/Archives/edgar/data/1624512/000121390026070452/ea029502501ex99-1.htm'},
    {'ticker': 'CHSN', 'effective_date': '2026-05-07', 'new_shares': 1, 'old_shares': 100,
     'source': 'https://www.sec.gov/Archives/edgar/data/1825349/000121390026052839/ea028942701ex99-1.htm'},
    {'ticker': 'CSAI', 'effective_date': '2026-07-31', 'new_shares': 1, 'old_shares': 30,
     'source': 'https://www.sec.gov/Archives/edgar/data/1709628/000168316826005913/cloud_ex9901.htm'},
    {'ticker': 'ELAB', 'effective_date': '2026-08-21', 'new_shares': 1, 'old_shares': 10,
     'source': 'https://www.sec.gov/Archives/edgar/data/1840563/000121390026094443/ea0303620-8k_pmgc.htm'},
    {'ticker': 'ELPW', 'effective_date': '2026-08-10', 'new_shares': 1, 'old_shares': 45,
     'source': 'https://www.sec.gov/Archives/edgar/data/2015691/000149315226037160/form6-k.htm'},
    {'ticker': 'FEED', 'effective_date': '2026-09-01', 'new_shares': 1, 'old_shares': 12,
     'source': 'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-625'},
    {'ticker': 'GCTK', 'effective_date': '2026-08-31', 'new_shares': 1, 'old_shares': 15,
     'source': 'https://www.sec.gov/Archives/edgar/data/1506983/000149315226040653/form8-k.htm'},
    {'ticker': 'YYAI', 'effective_date': '2026-05-18', 'new_shares': 1, 'old_shares': 40,
     'source': 'https://www.sec.gov/Archives/edgar/data/1674440/000149315226024417/form8-k.htm'},
    {'ticker': 'YYAI', 'effective_date': '2026-08-17', 'new_shares': 1, 'old_shares': 20,
     'source': 'https://www.sec.gov/Archives/edgar/data/1674440/000149315226043748/form10-q.htm'},
]

REVIEWED_DISTRIBUTIONS = [
    {'ticker': 'HON', 'effective_date': '2026-06-29',
     'source': 'https://www.sec.gov/Archives/edgar/data/773840/000077384026000084/hon-20260625.htm',
     'note': 'Honeywell distributed Aerospace shares and also effected a 1-for-2 reverse split. A vendor adjustment factor cannot replace both securities, cash and share entitlements.'},
    {'ticker': 'CTVA', 'effective_date': '2026-10-01',
     'source': 'https://www.sec.gov/Archives/edgar/data/1755672/000119312526414490/ck0001755672-20260929.htm',
     'note': 'Corteva distributed Vylor shares. The vendor Stock Splits factor of 6.665 is not a corroborated Corteva share-count multiplier; both securities and entitlements need reconciliation.'},
]

UNRESOLVED_ACTION_NOTES = [
    {'ticker': 'APH', 'source': 'https://www.sec.gov/Archives/edgar/data/820313/000110465926091969/tm2622441d1_ex99-1.htm',
     'note': 'The issuer announced a 2-for-1 stock dividend distributed September 2. A separate trading/ex-date source is still needed to corroborate the vendor September 3 date. No share-only scenario is certified.'},
]


def reconcile_position(position, actions, completed=None):
    row = dict(position)
    entry, mark, cost = [finite(row.get(key)) for key in ('entry_price', 'mark_price', 'virtual_dollars')]
    result = {key: row.get(key) for key in ('id', 'ticker', 'status', 'entry_date', 'end_date')}
    result.update(status='unverified', legacy_return_pct=None, split_scenario_return_pct=None,
                  actions=[], missing=['Historical price basis', 'Complete corporate-action/dividend coverage', 'Fees and execution evidence'])
    try:
        first, last = date.fromisoformat(row['entry_date'][:10]), date.fromisoformat(row['end_date'][:10])
        if last < first or last > date.today():
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        result['missing'].append('Valid holding dates')
        return result
    from completed_actions import position_reviews
    result['primary_completed_actions']=position_reviews(row,completed or {})
    if result['primary_completed_actions']:
        result['missing'].append('Issuer reports a completed conversion. Successor shares, cash, fractional proceeds and tax need actual records; the old-symbol mark is unverified.')
    if any(x is None or x <= 0 for x in (entry, mark, cost)):
        result['missing'].append('Valid positive entry, mark and allocation')
        return result
    relevant = [a for a in actions if a['ticker'] == row['ticker'] and first.isoformat() < a['effective_date'] <= last.isoformat()]
    distributions = [a for a in REVIEWED_DISTRIBUTIONS if a['ticker'] == row['ticker'] and first.isoformat() < a['effective_date'] <= last.isoformat()]
    shares = cost / entry
    result.update(legacy_return_pct=round((mark / entry - 1) * 100, 4), initial_implied_shares=shares, actions=relevant, distributions=distributions)
    if result['primary_completed_actions']: return result
    if distributions:
        result['missing'].append('Distributed securities and entitlements; a split-only scenario would omit assets.')
        return result
    if any(a.get('requires_review') for a in relevant):
        result['missing'].append('Primary corporate-action dates need reconciliation.')
        return result
    if relevant:
        for action in relevant:
            shares *= action['new_shares'] / action['old_shares']
        result.update(split_scenario_return_pct=round((shares * mark / cost - 1) * 100, 4), scenario_shares=shares,
                      scenario_basis='Only if stored entry/exit prices are unadjusted as traded; excludes dividends, other actions and costs.')
    return result


def reconciliation_report(db_path):
    from completed_actions import reports
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row;db.execute('BEGIN')
        completed=reports(db)
        rows = db.execute("""SELECT p.*, CASE WHEN p.status='closed' THEN p.close_price ELSE ca.current_price END mark_price,
            CASE WHEN p.status='closed' THEN p.close_date ELSE date('now') END end_date
            FROM paper_positions p LEFT JOIN company_analyses ca ON ca.ticker=p.ticker""").fetchall()
    positions = [reconcile_position(row, CONFIRMED_SPLITS,completed) for row in rows]
    positions.sort(key=lambda p: (not bool(p.get('primary_completed_actions')),not bool(p['actions']), -(p['legacy_return_pct'] or 0)))
    return {'status': 'unverified', 'total': len(positions), 'verified_positions': 0,
            'positions_with_confirmed_splits': sum(bool(p['actions']) for p in positions),
            'positions_with_reviewed_distributions': sum(bool(p.get('distributions')) for p in positions),
            'positions_with_completed_action_review':sum(bool(p.get('primary_completed_actions')) for p in positions),
            'confirmed_actions': CONFIRMED_SPLITS, 'positions': positions,
            'methodology': 'Original rows remain unchanged. A split scenario is not corrected performance: applying a split to an already adjusted price would double-adjust it. No account return is published until shares, price basis, cash flows and corporate actions reconcile.'}
