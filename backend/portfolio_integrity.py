"""Describe the limits of the legacy paper ledger without rewriting its history."""
import sqlite3
from completed_actions import reports, position_reviews
from lxml import etree


def portfolio_integrity(db_path):
    with sqlite3.connect(db_path, timeout=15) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        first, last = db.execute("SELECT MIN(entry_date), MAX(entry_date) FROM paper_positions WHERE status='open'").fetchone()
        missing, stale = db.execute("""
            SELECT SUM(CASE WHEN p.entry_price IS NULL OR p.entry_price<=0 OR
                            ca.current_price IS NULL OR ca.current_price<=0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN ca.analyzed_at IS NULL OR julianday('now')-julianday(ca.analyzed_at)>2 THEN 1 ELSE 0 END)
            FROM paper_positions p LEFT JOIN company_analyses ca ON ca.ticker=p.ticker
            WHERE p.status='open'
        """).fetchone()
        outliers = db.execute("""SELECT ticker, entry_date, close_date, realized_return_pct
            FROM paper_positions WHERE status='closed' AND
            (realized_return_pct>200 OR realized_return_pct < -100)
            ORDER BY realized_return_pct DESC""").fetchall()
        contributions = db.execute("SELECT COALESCE(SUM(virtual_dollars),0) FROM paper_positions").fetchone()[0]
        completed=[];completed_state='not_collected'
        try:
            evidence=reports(db);completed_state='saved_notices_replayed' if evidence else 'not_collected'
            for row in db.execute("SELECT ticker,entry_date,date('now') AS end_date FROM paper_positions WHERE status='open'"):
                completed.extend(position_reviews(dict(row),evidence))
        except (ValueError,KeyError,TypeError,etree.LxmlError):completed_state='integrity_failed';completed=None
    return {
        "status": "unverified",
        "basis": "Open-position cost and marked value; not a fixed-capital account or time-weighted return.",
        "open_entry_first": first, "open_entry_last": last,
        "cumulative_position_allocations_usd": contributions,
        "missing_price_count": missing or 0,
        "older_analysis_count": stale or 0,
        "outlier_count": len(outliers),
        "completed_action_evidence_status":completed_state,
        "open_completed_action_count":len(completed) if completed is not None else None,
        "open_completed_action_reviews":completed,
        "outliers": [dict(zip(("ticker", "entry_date", "close_date", "reported_return_pct"), row)) for row in outliers[:20]],
        "limitations": [
            "Legacy returns do not reconcile share splits, dividends, fees or execution slippage.",
            "Open-position entries and closed trades cover different periods; their averages are not a portfolio CAGR.",
            "The chart sums available position snapshots. Coverage changes and closed-trade cash is not carried forward.",
            "Analysis timestamps are not verified quote timestamps. Missing prices are carried at cost in the legacy summary.",
            "Factor correlations are descriptive associations, not validated predictive performance.",
        ],
    }
