"""
Stock Analysis API — FastAPI backend
Provides: data fetching, scoring models, red flag detection, Ollama AI analysis
Run: python -m uvicorn main:app --reload --port 8000
"""

import json
import math
import asyncio
import sqlite3
import threading
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, AsyncGenerator

import re
import html as _html_lib
import requests
import numpy as np
import yfinance as yf
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import hmac
from trade_evidence import make_router
from portfolio_integrity import portfolio_integrity
from portfolio_reconciliation import reconciliation_report
from paper_ledger import read_ledger, append_event, preview_import, commit_import
from research_integrity import instrument_eligibility, valuation_scenario, current_share_basis
from research_store import (init_research, make_research_router, identities, identity_eligibility,
                            rebuild_list, now as research_now)
from security_identity import make_identity_router, collect_due as collect_identity_classes
from ownership_history import init_ownership, make_ownership_router
from legacy_ownership import init_legacy, make_legacy_router
from performance_audit import init_performance, make_performance_router
from market_history import init_market_history, make_market_router
from market_lineage import make_lineage_router
from completed_actions import make_actions_router
from copy_study import init_copy, make_copy_router
from equity_workspace import init_workspace, seed_watchlist, make_workspace_router
from forward_performance import init_forward, make_forward_router, capture as capture_forward
from financial_sources import init_financial_sources, save_financial_source
from source_campaigns import init_campaigns, make_campaign_router
from benchmark_study import init_benchmark, make_benchmark_router
from signal_trials import init_trials, make_trials_router
from number_evidence import init_number_evidence, make_number_router, current_report, public_values, dupont_supported
from quote_checks import init_quote_checks, make_quote_router
from price_readiness import make_price_router
from cashflow_evidence import init_cashflow_evidence, make_cashflow_router
from statement_checks import init_statements, make_statement_router
from broker_reconciliation import init_broker, make_broker_router
from ibkr_flex import init_flex, make_flex_router
from ibkr_connection import init_connection, make_connection_router
from financial_context import make_context_router
from system_status import make_status_router
from financial_integrity import (finite, SCHEMA_VERSION, annual_facts, extract_annual_financials,
                                 dividend_yield_pct, debt_equity_ratio, free_cash_flow, statement_value, xbrl_total_debt, net_debt_amount)

app = FastAPI(title="Stock Analysis API", version="1.0.0")

_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Block all requests that don't carry the correct API secret.
    Skipped when API_SECRET is not set (local dev fallback).
    CORS preflight OPTIONS and health-check GET /health are always allowed through."""
    protected = request.method != "OPTIONS" and request.url.path != "/health"
    if protected and os.environ.get("FLY_APP_NAME") and not API_SECRET:
        return JSONResponse(status_code=503, content={"detail": "API authentication is not configured"})
    if protected and API_SECRET:
        token = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(token.encode("utf-8"), API_SECRET.encode("utf-8")):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

API_SECRET = os.environ.get("API_SECRET", "")  # set in Fly.io secrets — empty = no auth (local dev)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
# Header required by localtunnel to bypass the reminder page for non-browser clients
OLLAMA_HEADERS = {"bypass-tunnel-reminder": "true"}

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
_POLYGON_BASE   = "https://api.polygon.io"


def _polygon_price(ticker: str) -> float | None:
    if not POLYGON_API_KEY:
        return None
    """Current price for a single ticker via Polygon snapshot.
    Prefers today's close → last trade → previous close."""
    try:
        r = requests.get(
            f"{_POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
            params={"apiKey": POLYGON_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        t = r.json().get("ticker", {})
        price = (
            (t.get("day") or {}).get("c")
            or (t.get("lastTrade") or {}).get("p")
            or (t.get("prevDay") or {}).get("c")
        )
        return float(price) if price else None
    except Exception:
        return None


def _polygon_bulk_prices(tickers: list) -> dict:
    if not POLYGON_API_KEY:
        return {}
    """Prices for many tickers via Polygon snapshot, chunked at 250 per request."""
    prices: dict = {}
    for i in range(0, len(tickers), 250):
        chunk = tickers[i : i + 250]
        try:
            r = requests.get(
                f"{_POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"tickers": ",".join(chunk), "apiKey": POLYGON_API_KEY},
                timeout=30,
            )
            if r.status_code != 200:
                continue
            for t in r.json().get("tickers", []):
                sym = t.get("ticker")
                price = (
                    (t.get("day") or {}).get("c")
                    or (t.get("lastTrade") or {}).get("p")
                    or (t.get("prevDay") or {}).get("c")
                )
                if sym and price:
                    prices[sym] = float(price)
        except Exception:
            continue
    return prices


def _polygon_historical(ticker: str, start: str, end: str) -> dict:
    if not POLYGON_API_KEY:
        return {}
    """Daily adjusted close prices from Polygon. start/end are 'YYYY-MM-DD'.
    Returns {date_obj: price}."""
    try:
        r = requests.get(
            f"{_POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
            params={"adjusted": "true", "sort": "asc", "limit": 1000, "apiKey": POLYGON_API_KEY},
            timeout=20,
        )
        if r.status_code != 200:
            return {}
        from datetime import timezone
        return {
            datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).date(): float(bar["c"])
            for bar in r.json().get("results", [])
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────
# EDGAR / DATABASE CONFIG
# ─────────────────────────────────────────────────────────────────

# DB_PATH: use /data volume on Fly.io (set DATA_DIR=/data), fallback to local
_data_dir = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "stocks.db"
app.include_router(make_router(DB_PATH))
_research_router = make_research_router(DB_PATH, lambda *args, **kwargs: gather_stock_data(*args, **kwargs),
                                      lambda: collect_identity_classes(DB_PATH, EDGAR_HEADERS))
app.include_router(_research_router)
app.include_router(make_identity_router(DB_PATH, lambda: EDGAR_HEADERS))
_ownership_router = make_ownership_router(DB_PATH, lambda ticker: get_cik_map().get(ticker))
app.include_router(_ownership_router)
_campaign_router = make_campaign_router(DB_PATH, _research_router.refresh_inputs, _ownership_router.sync_sec)
app.include_router(_campaign_router)
app.include_router(make_legacy_router(DB_PATH))
app.include_router(make_performance_router(DB_PATH))
app.include_router(make_market_router(DB_PATH))
app.include_router(make_lineage_router(DB_PATH))
app.include_router(make_actions_router(DB_PATH))
app.include_router(make_copy_router(DB_PATH))
_equity_router = make_workspace_router(DB_PATH, _ownership_router.sync_sec, lambda: capture_forward(DB_PATH))
app.include_router(_equity_router)
_forward_router = make_forward_router(DB_PATH, _equity_router.record_marks)
app.include_router(_forward_router)
_benchmark_router = make_benchmark_router(DB_PATH)
app.include_router(_benchmark_router)
_trials_router = make_trials_router(DB_PATH)
app.include_router(_trials_router)
app.include_router(make_number_router(DB_PATH))
app.include_router(make_cashflow_router(DB_PATH))
app.include_router(make_statement_router(DB_PATH, lambda: EDGAR_HEADERS))
_quote_router = make_quote_router(DB_PATH)
app.include_router(_quote_router)
app.include_router(make_price_router(DB_PATH))
app.include_router(make_broker_router(DB_PATH))
app.include_router(make_flex_router(DB_PATH))
app.include_router(make_connection_router(DB_PATH))
app.include_router(make_context_router(DB_PATH))
app.include_router(make_status_router(DB_PATH, lambda: getattr(app.state, 'collector_scheduler', None)))
EDGAR_HEADERS = {"User-Agent": os.environ.get("EDGAR_USER_AGENT", "StockAnalyzer https://github.com/sadramanouch/stocks-analyzer")}
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_FILING_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
EDGAR_CACHE_DAYS = 7
TRACKED_FORMS = {"10-K", "10-Q", "8-K", "4", "DEF 14A"}

_cik_map: dict | None = None
_cik_map_fetched_at: datetime | None = None


def init_db():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        # WAL mode: readers never block on writers — eliminates lock contention during bulk analyze
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")  # faster disk sync, still crash-safe
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL UNIQUE,
                cik          TEXT    NOT NULL,
                name         TEXT,
                exchange     TEXT,
                sic          TEXT,
                last_updated TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filings (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id        INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                form_type         TEXT    NOT NULL,
                filed_date        TEXT    NOT NULL,
                period_of_report  TEXT,
                accession_number  TEXT    NOT NULL,
                description       TEXT,
                url               TEXT    NOT NULL,
                UNIQUE(company_id, accession_number)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_filings_company_form
            ON filings(company_id, form_type, filed_date DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_analyses (
                ticker            TEXT PRIMARY KEY,
                company_name      TEXT,
                sector            TEXT,
                industry          TEXT,
                current_price     REAL,
                piotroski_score   REAL,
                signal            TEXT,
                confidence        REAL,
                price_target_low  REAL,
                price_target_base REAL,
                price_target_high REAL,
                upside_pct        REAL,
                verdict_text      TEXT,
                filings_count     INTEGER,
                filings_detail    TEXT,
                analyzed_at       TEXT NOT NULL
            )
        """)
        # Migrate: add scoring columns if they don't exist yet
        for col, coltype in [
            ("altman_z_score",        "REAL"),
            ("sloan_accrual",         "REAL"),
            ("red_flags_count",       "INTEGER"),
            ("red_flags_high",        "INTEGER"),
            # Advanced frameworks (added 2026-03)
            ("beneish_score",         "REAL"),
            ("reverse_dcf_growth",    "REAL"),
            ("magic_ey",              "REAL"),
            ("roic_spread",           "REAL"),
            ("peg_ratio_val",         "REAL"),
            ("owner_earnings_yield",  "REAL"),
            ("rule_of_40_score",      "REAL"),
            ("fcf_yield",             "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE company_analyses ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # column already exists
        # Persistent market data cache (survives restarts/deploys)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_data_cache (
                ticker     TEXT PRIMARY KEY,
                data_json  TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        # EDGAR XBRL cache — extracted annual fundamentals per CIK (30-day refresh)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xbrl_cache (
                cik        TEXT PRIMARY KEY,
                data_json  TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        # New columns on company_analyses: quant_score (0-99) and data_source ('ai'/'xbrl')
        for col, coltype in [
            ("quant_score", "INTEGER"),
            ("data_source", "TEXT"),
            ("pinned",      "INTEGER DEFAULT 0"),
            ("market_cap",  "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE company_analyses ADD COLUMN {col} {coltype}")
            except Exception:
                pass
        # Universe table — master list of all screened tickers (NYSE + Nasdaq from SEC EDGAR)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS universe (
                ticker    TEXT PRIMARY KEY,
                cik       TEXT,
                name      TEXT,
                exchange  TEXT,
                source    TEXT DEFAULT 'edgar',
                added_at  TEXT
            )
        """)
        # ── Paper Portfolio ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_positions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker              TEXT    NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'open',
                virtual_dollars     REAL    NOT NULL DEFAULT 100.0,
                entry_price         REAL    NOT NULL,
                entry_date          TEXT    NOT NULL,
                entry_signal        TEXT    NOT NULL,
                entry_quant_score   INTEGER,
                entry_fcf_yield     REAL,
                entry_peg_ratio     REAL,
                entry_upside_pct    REAL,
                entry_piotroski     REAL,
                entry_altman_z      REAL,
                entry_beneish       REAL,
                entry_roic_spread   REAL,
                entry_magic_ey      REAL,
                entry_rdcf_growth   REAL,
                close_price         REAL,
                close_date          TEXT,
                close_signal        TEXT,
                close_reason        TEXT,
                realized_return_pct REAL,
                UNIQUE(ticker, entry_date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS position_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id     INTEGER NOT NULL REFERENCES paper_positions(id) ON DELETE CASCADE,
                snapshot_date   TEXT    NOT NULL,
                current_price   REAL,
                current_signal  TEXT,
                unrealized_pnl  REAL,
                return_pct      REAL,
                UNIQUE(position_id, snapshot_date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_pos ON position_snapshots(position_id, snapshot_date DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at              TEXT    NOT NULL,
                positions_analyzed  INTEGER NOT NULL,
                min_holding_days    INTEGER NOT NULL DEFAULT 30,
                correlation_json    TEXT    NOT NULL,
                signal_accuracy_json TEXT   NOT NULL,
                notes               TEXT
            )
        """)
        conn.commit()


    init_research(DB_PATH)
    init_ownership(DB_PATH)
    init_legacy(DB_PATH)
    init_performance(DB_PATH)
    init_market_history(DB_PATH)
    init_copy(DB_PATH)
    init_workspace(DB_PATH)
    init_forward(DB_PATH)
    init_financial_sources(DB_PATH)
    init_benchmark(DB_PATH)
    init_trials(DB_PATH)
    init_number_evidence(DB_PATH)
    init_cashflow_evidence(DB_PATH)
    init_statements(DB_PATH)
    init_quote_checks(DB_PATH)
    init_broker(DB_PATH)
    init_flex(DB_PATH)
    init_connection(DB_PATH)
    init_campaigns(DB_PATH)


SIGNAL_ORDER = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "AVOID": 3, "STRONG_AVOID": 4}


@app.on_event("startup")
def on_startup():
    init_db()
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.execute("UPDATE research_refresh_jobs SET state='interrupted',updated_at=? WHERE state='running'", (research_now(),))
        db.execute("UPDATE ownership_history_runs SET state='interrupted',finished_at=? WHERE state='running'", (research_now(),))
        db.execute("UPDATE legacy_ownership_runs SET state='interrupted',finished_at=? WHERE state='running'", (research_now(),))
        db.execute("UPDATE market_history_runs SET state='interrupted',finished_at=? WHERE state='running'", (research_now(),))
        db.execute("UPDATE equity_monitor_runs SET state='interrupted',finished_at=? WHERE state='running'", (research_now(),))
        has_version = db.execute('SELECT 1 FROM saved_list_versions LIMIT 1').fetchone()
    if not has_version:
        rebuild_list(DB_PATH, 'Migration audit; old financial caches are not recertified')
    seed_watchlist(DB_PATH)
    # Exchange-local scheduling follows daylight saving rather than fixed UTC.
    # Runs entirely on Fly.io — no manual button clicks needed.
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _daily_auto_update,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone='America/New_York'),
        id="daily_update",
        replace_existing=True,
        misfire_grace_time=3600,  # fire up to 1 hour late if server was down
    )
    _scheduler.add_job(_research_router.daily_refresh, CronTrigger(day_of_week='mon-fri', hour=16, minute=50, timezone='America/New_York'),
                       id='documented_research_refresh', replace_existing=True, misfire_grace_time=3600)
    _scheduler.add_job(_campaign_router.tick, 'interval', minutes=1,
                       id='bounded_source_campaign', replace_existing=True, misfire_grace_time=60)
    _scheduler.add_job(_equity_router.scheduled_monitor,
                       CronTrigger(day_of_week='mon-fri', hour='7-22', minute=10, timezone='America/New_York'),
                       id='focused_sec_monitor', replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_forward_router.scheduled_capture,
                       CronTrigger(day_of_week='mon-fri', hour=17, minute=20, timezone='America/New_York'),
                       id='forward_paper_checkpoint', replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_benchmark_router.scheduled_collect,
                       CronTrigger(day_of_week='mon-fri', hour='9,18', minute=5, timezone='America/New_York'),
                       id='prospective_benchmark_collection', replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_trials_router.scheduled_collect,
                       CronTrigger(day_of_week='mon-fri', hour='9,18', minute=20, timezone='America/New_York'),
                       id='prospective_signal_trials', replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_quote_router.scheduled_collect,
                       CronTrigger(day_of_week='mon-fri', hour='10-15', minute=25, timezone='America/New_York'),
                       id='active_trial_quote_checks', replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_quote_router.scheduled_collect,
                       CronTrigger(day_of_week='mon-fri', hour='13,16', minute=10, timezone='America/New_York'),
                       id='active_trial_closing_quote_checks', replace_existing=True, misfire_grace_time=300)
    _scheduler.start()
    app.state.collector_scheduler = _scheduler


@app.on_event("shutdown")
def stop_collectors():
    scheduler = getattr(app.state, 'collector_scheduler', None)
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def safe(v, default=None):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return default
    return v


def pct(v):
    if v is None:
        return None
    try:
        return round(float(v) * 100, 2)
    except:
        return None


def fmt_b(v):
    if v is None:
        return "N/A"
    try:
        v = float(v)
        if abs(v) >= 1e12:
            return f"${v/1e12:.2f}T"
        if abs(v) >= 1e9:
            return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.1f}M"
        return f"${v:,.0f}"
    except:
        return "N/A"


# ─────────────────────────────────────────────────────────────────
# SCORING MODELS
# ─────────────────────────────────────────────────────────────────

def calc_freshness(filing_date_str: str, form_type: str = "10-Q") -> dict:
    try:
        filing_date = datetime.strptime(filing_date_str[:10], "%Y-%m-%d")
    except:
        return {"score": 0, "label": "Unknown", "color": "gray", "days_old": 999, "warning": "Could not parse filing date."}

    days_old = (datetime.now() - filing_date).days

    if days_old < 0:
        return {"score": 0, "label": "Invalid future date", "color": "gray", "days_old": days_old, "warning": "Financial period is in the future."}
    if form_type in {"10-K", "20-F"}:
        current = days_old <= 400
        return {"score": 75 if current else 20, "label": "Annual period" if current else "Older annual period",
                "color": "yellow" if current else "red", "days_old": days_old,
                "filing_date": filing_date_str[:10], "form_type": form_type, "date_basis": "period_end",
                "warning": "Annual statement period end, not filing or quote time. Check newer quarterly filings before acting."}

    if days_old <= 30:
        score, label, color = 100, "Very Fresh", "green"
    elif days_old <= 60:
        score, label, color = 90, "Fresh", "green"
    elif days_old <= 90:
        score, label, color = 75, "Current Quarter", "yellow"
    elif days_old <= 135:
        score, label, color = 55, "One Quarter Behind", "yellow"
    elif days_old <= 180:
        score, label, color = 35, "Two Quarters Behind", "orange"
    elif days_old <= 365:
        score, label, color = 20, "Very Stale", "red"
    else:
        score, label, color = 5, "Extremely Stale (1yr+)", "red"

    warning = ""
    if days_old > 90:
        warning = (
            f"Data is {days_old} days old. Revenue trajectory, guidance, debt levels, "
            f"and macro conditions may have materially changed. Check recent 8-K filings."
        )

    return {
        "score": score, "label": label, "color": color,
        "days_old": days_old, "filing_date": filing_date_str[:10],
        "form_type": form_type, "warning": warning
    }


def altman_z(wc, re, ebit, mktcap, revenue, total_assets, total_liab):
    try:
        x1 = wc / total_assets
        x2 = re / total_assets
        x3 = ebit / total_assets
        x4 = min(mktcap / total_liab, 30)  # cap: formula breaks when liabilities ≈ 0 (yfinance null → fallback 1)
        x5 = revenue / total_assets
        z = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
        z = round(z, 3)
        if z > 2.99:   zone, color = "Safe Zone", "green"
        elif z > 1.81: zone, color = "Grey Zone", "yellow"
        else:          zone, color = "Distress Zone", "red"
        return {"score": z, "zone": zone, "color": color,
                "components": {"X1_liquidity": round(x1,3), "X2_profitability": round(x2,3),
                               "X3_operating": round(x3,3), "X4_solvency": round(x4,3),
                               "X5_turnover": round(x5,3)}}
    except Exception as e:
        return {"score": None, "zone": "N/A", "color": "gray", "error": str(e)}


def piotroski_f(curr, prev):
    score = 0
    breakdown = {}

    def check(key, val):
        breakdown[key] = 1 if val else 0
        return 1 if val else 0

    try:
        # Piotroski (2000) uses beginning-of-year (= prior-year end) total assets as denominator,
        # not average assets. This is the most commonly mis-implemented detail.
        beg_assets = prev.get("totalAssets") or curr.get("totalAssets") or 1
        roa_c = curr.get("netIncome", 0) / beg_assets
        roa_p = prev.get("netIncome", 0) / beg_assets  # approx: 2-yr-prior TA unavailable
        score += check("F1_positive_roa", roa_c > 0)
        score += check("F2_positive_ocf", curr.get("operatingCashflow", 0) > 0)
        score += check("F3_improving_roa", roa_c > roa_p)
        score += check("F4_cash_quality", curr.get("operatingCashflow", 0) > curr.get("netIncome", 0))

        lev_c = curr.get("longTermDebt", 0) / (curr.get("totalAssets", 1))
        lev_p = prev.get("longTermDebt", 0) / (prev.get("totalAssets", 1))
        cr_c  = curr.get("currentAssets", 0) / (curr.get("currentLiabilities", 1))
        cr_p  = prev.get("currentAssets", 0) / (prev.get("currentLiabilities", 1))
        score += check("F5_decreasing_leverage", lev_c < lev_p)
        score += check("F6_improving_liquidity", cr_c > cr_p)
        score += check("F7_no_dilution", curr.get("sharesOutstanding", 0) <= prev.get("sharesOutstanding", float("inf")))

        rev_c = curr.get("totalRevenue", 1)
        rev_p = prev.get("totalRevenue", 1)
        cogs_c = curr.get("costOfRevenue", 0)
        cogs_p = prev.get("costOfRevenue", 0)
        gm_c = (rev_c - cogs_c) / rev_c if rev_c else 0
        gm_p = (rev_p - cogs_p) / rev_p if rev_p else 0
        # F9: asset turnover uses beginning-of-year assets (Piotroski 2000, Table 1)
        at_c = rev_c / beg_assets
        at_p = rev_p / beg_assets
        score += check("F8_improving_gross_margin", gm_c > gm_p)
        score += check("F9_improving_asset_turnover", at_c > at_p)

        if score >= 7:   verdict, color = "Strong", "green"
        elif score >= 4: verdict, color = "Neutral", "yellow"
        else:            verdict, color = "Weak", "red"

        return {"score": score, "verdict": verdict, "color": color, "breakdown": breakdown}
    except Exception as e:
        return {"score": None, "verdict": "N/A", "color": "gray", "error": str(e)}


def sloan_ratio(net_income, ocf, total_assets, prior_total_assets=None):
    """Sloan (1996): operating accruals = NI - OCF only (not investing CF).
    Denominator is average total assets per the original paper."""
    try:
        # Original paper: accruals = NI - OCF (operating accruals only)
        # Richardson et al. (2005) extended to include investing CF — that is a different metric
        accruals = net_income - ocf
        avg_assets = ((total_assets or 0) + (prior_total_assets or total_assets or 0)) / 2 or total_assets
        if not avg_assets or abs(avg_assets) < 1_000_000:  # require ≥$1M assets — formula invalid for shells
            return {"ratio": None, "quality": "N/A", "color": "gray", "error": "Insufficient assets"}
        ratio = round(max(-500, min(500, (accruals / avg_assets) * 100)), 2)
        if ratio < -10:   quality, color = "Very High Quality", "green"
        elif ratio < 0:   quality, color = "High Quality", "green"
        elif ratio < 10:  quality, color = "Moderate", "yellow"
        else:             quality, color = "Low Quality — Red Flag", "red"
        return {"ratio": ratio, "quality": quality, "color": color}
    except Exception as e:
        return {"ratio": None, "quality": "N/A", "color": "gray", "error": str(e)}


# ─────────────────────────────────────────────────────────────────
# ADVANCED VALUATION FRAMEWORKS
# ─────────────────────────────────────────────────────────────────

def calc_beneish(fins, bal, cf):
    """Beneish M-Score — earnings manipulation detection."""
    try:
        if fins is None or fins.empty or bal is None or bal.empty:
            return None
        if fins.shape[1] < 2 or bal.shape[1] < 2:
            return None

        def g(df, *keys, col=0):
            for k in keys:
                if k in df.index:
                    vals = df.loc[k].dropna()
                    if len(vals) > col:
                        return float(vals.iloc[col])
            return None

        # Current year (col 0) and prior year (col 1)
        sales_t   = g(fins, "Total Revenue", col=0)
        sales_t1  = g(fins, "Total Revenue", col=1)
        cogs_t    = g(fins, "Cost Of Revenue", col=0)
        cogs_t1   = g(fins, "Cost Of Revenue", col=1)
        ni_t      = g(fins, "Net Income", col=0)
        ar_t      = g(bal,  "Accounts Receivable", col=0)
        ar_t1     = g(bal,  "Accounts Receivable", col=1)
        ca_t      = g(bal,  "Current Assets", col=0)
        ca_t1     = g(bal,  "Current Assets", col=1)
        ppe_t     = g(bal,  "Net PPE", "Properties", col=0)
        ppe_t1    = g(bal,  "Net PPE", "Properties", col=1)
        ta_t      = g(bal,  "Total Assets", col=0)
        ta_t1     = g(bal,  "Total Assets", col=1)
        dep_t     = g(cf,   "Depreciation And Amortization", "Depreciation", col=0)
        dep_t1    = g(cf,   "Depreciation And Amortization", "Depreciation", col=1)
        sga_t     = g(fins, "Selling General And Administration", "Selling And Marketing Expense", col=0)
        sga_t1    = g(fins, "Selling General And Administration", "Selling And Marketing Expense", col=1)
        ltd_t     = g(bal,  "Long Term Debt", col=0)
        ltd_t1    = g(bal,  "Long Term Debt", col=1)
        cl_t      = g(bal,  "Current Liabilities", col=0)
        cl_t1     = g(bal,  "Current Liabilities", col=1)
        ocf_t     = g(cf,   "Operating Cash Flow", "Cash Flow From Continuing Operating Activities", col=0)
        gw_t      = g(bal,  "Goodwill", col=0) or 0
        intang_t  = g(bal,  "Intangible Assets", "Other Intangible Assets", col=0) or 0
        gw_t1     = g(bal,  "Goodwill", col=1) or 0
        intang_t1 = g(bal,  "Intangible Assets", "Other Intangible Assets", col=1) or 0

        if not all(v is not None for v in [sales_t, sales_t1, cogs_t, cogs_t1, ar_t, ar_t1, ta_t, ta_t1]):
            return None
        if sales_t == 0 or sales_t1 == 0 or ta_t == 0 or ta_t1 == 0:
            return None

        DSRI = (ar_t / sales_t) / (ar_t1 / sales_t1) if ar_t1 and sales_t1 else 1.0
        gm_t  = (sales_t  - cogs_t)  / sales_t  if cogs_t  else 1.0
        gm_t1 = (sales_t1 - cogs_t1) / sales_t1 if cogs_t1 else 1.0
        GMI = gm_t1 / gm_t if gm_t else 1.0

        non_op_t  = max(0, ta_t  - (ca_t or 0)  - (ppe_t or 0)  - (gw_t)  - (intang_t))
        non_op_t1 = max(0, ta_t1 - (ca_t1 or 0) - (ppe_t1 or 0) - (gw_t1) - (intang_t1))
        AQI = (1 - ((ca_t or 0) + (ppe_t or 0)) / ta_t) / (1 - ((ca_t1 or 0) + (ppe_t1 or 0)) / ta_t1) if ta_t1 else 1.0

        SGI = sales_t / sales_t1

        if dep_t is not None and ppe_t is not None and dep_t1 is not None and ppe_t1 is not None:
            denom_t  = (ppe_t  + dep_t)  if (ppe_t  + dep_t)  != 0 else 1
            denom_t1 = (ppe_t1 + dep_t1) if (ppe_t1 + dep_t1) != 0 else 1
            DEPI = (dep_t1 / denom_t1) / (dep_t / denom_t)
        else:
            DEPI = 1.0

        SGAI = (sga_t / sales_t) / (sga_t1 / sales_t1) if sga_t and sga_t1 and sales_t1 else 1.0

        if ltd_t is not None and cl_t is not None and ltd_t1 is not None and cl_t1 is not None:
            LVGI = ((ltd_t + cl_t) / ta_t) / ((ltd_t1 + cl_t1) / ta_t1)
        else:
            LVGI = 1.0

        TATA = (ni_t - (ocf_t or 0)) / ta_t if ni_t is not None else 0.0

        score = (-4.84
                 + 0.920 * DSRI
                 + 0.528 * GMI
                 + 0.404 * AQI
                 + 0.892 * SGI
                 + 0.115 * DEPI
                 - 0.172 * SGAI
                 + 4.679 * TATA
                 - 0.327 * LVGI)
        score = round(score, 3)

        if score > -1.78:
            verdict, color = "Manipulator", "red"
        elif score > -2.22:
            verdict, color = "Gray Zone", "yellow"
        else:
            verdict, color = "Non-Manipulator", "green"

        return {
            "score": score,
            "verdict": verdict,
            "color": color,
            "components": {
                "DSRI": round(DSRI, 3), "GMI": round(GMI, 3),
                "AQI": round(AQI, 3),  "SGI": round(SGI, 3),
                "DEPI": round(DEPI, 3),"SGAI": round(SGAI, 3),
                "LVGI": round(LVGI, 3),"TATA": round(TATA, 4),
            }
        }
    except Exception:
        return None


def calc_reverse_dcf(*args, **kwargs):
    """Retired: ambiguous FCFE/WACC contract; use valuation_scenario."""
    return None


def calc_magic_formula(ebit, enterprise_value, total_assets, current_assets, current_liabilities, intangibles=0, cash=0):
    """Greenblatt Magic Formula — earnings yield + ROIC.
    Per 'The Little Book That Beats the Market': working capital excludes excess cash
    (cash is a financial asset, not operating capital) and short-term interest-bearing debt."""
    try:
        if not ebit or not enterprise_value or enterprise_value < 1_000_000:  # require ≥$1M EV
            return None
        earnings_yield = round(max(-1.0, min(1.0, ebit / enterprise_value)), 4)  # cap ±100%

        net_fixed_assets = (total_assets or 0) - (current_assets or 0) - (intangibles or 0)
        # Greenblatt explicitly excludes excess cash from NWC — cash is not operating capital
        working_capital  = ((current_assets or 0) - (cash or 0)) - (current_liabilities or 0)
        invested_capital = net_fixed_assets + working_capital
        if invested_capital <= 0:
            return None
        roic = round(ebit / invested_capital, 4)

        ey_pct = earnings_yield * 100
        roic_pct = roic * 100
        combined = ey_pct + roic_pct

        if ey_pct > 10 and roic_pct > 25:
            verdict, color = "Excellent — High yield & ROIC", "green"
        elif ey_pct > 7 and roic_pct > 15:
            verdict, color = "Good — Solid earnings power", "green"
        elif ey_pct > 5:
            verdict, color = "Average", "yellow"
        else:
            verdict, color = "Weak — Low earnings yield", "red"

        return {
            "earnings_yield": round(ey_pct, 2),
            "roic": round(roic_pct, 2),
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def calc_epv(ebit_vals, tax_rate, da, capex, wacc=0.10, cash=0, total_debt=0, shares=1, price=0):
    """Earnings Power Value (Greenwald) — no-growth intrinsic value."""
    try:
        if not ebit_vals or cash is None or total_debt is None or not shares or shares <= 0:
            return None
        valid = [e for e in ebit_vals if e is not None]
        if not valid:
            return None
        tax_rate = max(0.10, min(0.40, tax_rate or 0.21))
        normalized_ebit = sum(valid) / len(valid)
        # Maintenance capex ≈ D&A (conservative)
        adj_ebit = normalized_ebit + (da or 0) - (da or 0)  # simplified: NOPAT from EBIT
        nopat = normalized_ebit * (1 - tax_rate)
        if wacc <= 0:
            return None
        epv_total = nopat / wacc
        epv_equity = epv_total + (cash or 0) - (total_debt or 0)
        epv_per_share = epv_equity / shares if shares and shares > 0 else None
        growth_premium = round((price - epv_per_share) / epv_per_share * 100, 1) if epv_per_share and price and epv_per_share > 0 else None

        if epv_per_share and price:
            if price < epv_per_share * 0.8:
                verdict, color = "Trading below EPV — potential deep value", "green"
            elif price < epv_per_share:
                verdict, color = "Below EPV — reasonable value", "green"
            elif price < epv_per_share * 1.3:
                verdict, color = "Near EPV — fair value", "yellow"
            else:
                verdict, color = "Above EPV — paying for growth", "orange"
        else:
            verdict, color = "N/A", "gray"

        return {
            "epv_total": round(epv_total, 0),
            "epv_per_share": round(epv_per_share, 2) if epv_per_share else None,
            "growth_premium_pct": growth_premium,
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def calc_roic_wacc(ebit, tax_rate, total_assets, current_liabilities, cash, total_debt, equity, beta=1.0, mktcap=None):
    """ROIC vs WACC — value creation spread.
    Invested capital: financing approach (debt + equity - excess cash).
    WACC: uses market-cap equity weight per textbook WACC formula (Brealey/Myers)."""
    try:
        if not ebit or not total_assets or any(v is None for v in (cash, total_debt, equity)):
            return None
        tax_rate = max(0.10, min(0.40, tax_rate or 0.21))
        nopat = ebit * (1 - tax_rate)
        # Financing approach: Invested Capital = Debt + Equity - Excess Cash
        # Excess cash = all cash (conservative: assume 100% is non-operational for OTAs/tech)
        excess_cash = (cash or 0)
        invested_capital = (total_debt or 0) + (equity or 0) - excess_cash
        # Fallback to asset-based approach if financing approach yields <= 0
        if invested_capital <= 0:
            invested_capital = (total_assets or 0) - (current_liabilities or 0) - excess_cash
        if invested_capital <= 0:
            return None
        roic = nopat / invested_capital

        # WACC: use market cap for equity weight (not book equity — book can be negative/tiny)
        risk_free = 0.043
        erp = 0.055
        beta_c = max(0.5, min(2.5, beta or 1.0))
        cost_of_equity = risk_free + beta_c * erp

        mc = mktcap if mktcap and mktcap > 0 else max((equity or 0), 1)
        total_capital_mkt = mc + (total_debt or 0)
        equity_weight = max(0.1, min(0.95, mc / total_capital_mkt)) if total_capital_mkt > 0 else 0.8
        debt_weight = 1 - equity_weight
        cost_of_debt = 0.05  # approximate
        wacc = cost_of_equity * equity_weight + cost_of_debt * (1 - tax_rate) * debt_weight
        spread = roic - wacc
        # Cap spread used for scoring at 100% — above this is almost always a capital
        # structure artifact (near-zero invested capital from negative working capital).
        # spread_scored is used by the conviction engine; spread is shown in the UI.
        spread_scored = min(spread, 1.0)

        if spread > 0.05:
            verdict, color = "Value Creator", "green"
        elif spread > -0.02:
            verdict, color = "Breakeven", "yellow"
        else:
            verdict, color = "Value Destroyer", "red"

        return {
            "roic": round(roic * 100, 2),
            "wacc": round(wacc * 100, 2),
            "spread": round(spread * 100, 2),          # real value for display
            "spread_scored": round(spread_scored * 100, 2),  # capped for scoring
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def calc_peg(pe_ttm, eps_growth_rate):
    """PEG Ratio — Lynch growth-adjusted valuation."""
    try:
        if pe_ttm is None or eps_growth_rate is None or eps_growth_rate <= 0 or pe_ttm <= 0:
            return None
        # eps_growth_rate should be a percentage (e.g. 20, not 0.20)
        growth_pct = eps_growth_rate if eps_growth_rate > 1 else eps_growth_rate * 100
        if growth_pct <= 0:
            return None
        peg = round(pe_ttm / growth_pct, 2)

        if peg < 1.0:
            verdict, color = "Undervalued", "green"
        elif peg < 2.0:
            verdict, color = "Fairly Valued", "yellow"
        elif peg < 3.0:
            verdict, color = "Expensive", "orange"
        else:
            verdict, color = "Highly Expensive", "red"

        return {"peg": peg, "verdict": verdict, "color": color}
    except Exception:
        return None


def calc_net_net(current_assets, total_liabilities, shares, price=0):
    """Graham Net-Net Working Capital — deep value screen."""
    try:
        if current_assets is None or total_liabilities is None or not shares or shares <= 0:
            return None
        nnwc = (current_assets or 0) - (total_liabilities or 0)
        nnwc_per_share = round(nnwc / shares, 2)
        vs_price_pct = round((nnwc_per_share - price) / price * 100, 1) if price and price > 0 else None

        if nnwc_per_share > price and price > 0:
            verdict, color = "Deep Value — trading below net liquidation value", "green"
        elif nnwc_per_share > 0:
            verdict, color = "Positive NNWC — some asset support", "yellow"
        else:
            verdict, color = "Negative NNWC — liabilities exceed current assets", "red"

        return {
            "nnwc": round(nnwc, 0),
            "nnwc_per_share": nnwc_per_share,
            "vs_price_pct": vs_price_pct,
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def calc_owner_earnings(net_income, da, capex, shares=1, mktcap=0, ocf=None):
    """Buffett Owner Earnings (1986 Berkshire letter): NI + D&A - maintenance capex.
    Maintenance capex proxy = min(D&A, |total capex|): the lower bound of asset replacement cost.
    If OCF is available, use OCF - maintenance_capex (more precise, captures WC changes)."""
    try:
        if net_income is None:
            return None
        # Buffett: subtract only maintenance capex, not growth capex.
        # Proxy: maintenance ≈ min(D&A, |capex|) — conservative lower bound.
        # If D&A > |capex|: company under-investing → use actual capex as maintenance
        # If |capex| > D&A: company over-investing (growth) → use D&A as maintenance proxy
        maintenance_capex = min(abs(da or 0), abs(capex or 0)) if da else abs(capex or 0)
        if ocf is not None:
            owner_earnings = ocf - maintenance_capex
        else:
            owner_earnings = (net_income or 0) + (da or 0) - maintenance_capex
        oe_per_share = round(owner_earnings / shares, 2) if shares and shares > 0 else None
        oe_yield = round(max(-100, min(100, owner_earnings / mktcap * 100)), 2) if mktcap and mktcap > 10_000_000 else None  # require ≥$10M mktcap
        fcf = (net_income or 0) + (da or 0) - abs(capex or 0)  # same as owner_earnings here
        vs_fcf_pct = 0.0  # identical in this simplified form

        if oe_yield is not None:
            if oe_yield > 8:
                verdict, color = "Attractive owner earnings yield", "green"
            elif oe_yield > 4:
                verdict, color = "Moderate owner earnings yield", "yellow"
            elif oe_yield > 0:
                verdict, color = "Low owner earnings yield", "orange"
            else:
                verdict, color = "Negative owner earnings", "red"
        else:
            verdict, color = "N/A", "gray"

        return {
            "owner_earnings": round(owner_earnings, 0),
            "owner_earnings_per_share": oe_per_share,
            "owner_earnings_yield": oe_yield,
            "vs_reported_fcf_pct": vs_fcf_pct,
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def calc_dupont(net_income, revenue, total_assets, equity):
    """DuPont Analysis — ROE decomposition."""
    try:
        net_income, revenue, total_assets, equity = [finite(v) for v in (net_income, revenue, total_assets, equity)]
        if net_income is None or any(v is None or v <= 0 for v in (revenue, total_assets, equity)):
            return None
        net_margin      = net_income / revenue
        asset_turnover  = revenue / total_assets
        equity_mult     = total_assets / equity
        roe             = net_margin * asset_turnover * equity_mult

        # Determine dominant driver
        margins_score   = abs(net_margin)
        efficiency_score = asset_turnover / 2  # normalize: typical AT ~0.5–2.0
        leverage_score  = max(0, equity_mult - 1) / 3  # normalize: EM > 1 adds leverage

        if equity_mult > 3 and leverage_score > margins_score:
            dominant, quality, color = "Leverage", "Leverage-Driven", "orange"
        elif net_margin > 0.15:
            dominant, quality, color = "Margins", "High Quality", "green"
        elif asset_turnover > 1.5 and asset_turnover > margins_score * 10:
            dominant, quality, color = "Efficiency", "High Quality", "green"
        else:
            dominant, quality, color = "Balanced", "Mixed", "yellow"

        return {
            "roe": round(roe * 100, 2),
            "net_margin": round(net_margin * 100, 2),
            "asset_turnover": round(asset_turnover, 3),
            "equity_multiplier": round(equity_mult, 3),
            "dominant_driver": dominant,
            "quality": quality,
            "color": color,
        }
    except Exception:
        return None


def calc_rule_of_40(revenue_growth_pct, op_margin_pct):
    """Rule of 40 — SaaS/growth company health metric."""
    try:
        if revenue_growth_pct is None or op_margin_pct is None:
            return None
        # Cap revenue growth at 500% — above this is a data quality artifact (near-zero base)
        capped_growth = max(-200, min(500, revenue_growth_pct))
        score = round(capped_growth + op_margin_pct, 1)

        if score >= 60:
            verdict, color = "Elite", "green"
        elif score >= 40:
            verdict, color = "Healthy", "yellow"
        elif score >= 20:
            verdict, color = "Below Par", "orange"
        else:
            verdict, color = "Underperforming", "red"

        return {
            "score": score,
            "revenue_growth_pct": round(revenue_growth_pct, 1),
            "op_margin_pct": round(op_margin_pct, 1),
            "verdict": verdict,
            "color": color,
        }
    except Exception:
        return None


def red_flags(curr, prev):
    flags = []

    def add(severity, code, detail):
        flags.append({"severity": severity, "code": code, "detail": detail})

    try:
        ni_c  = curr.get("netIncome", 0) or 0
        ni_p  = prev.get("netIncome", 0) or 0
        ocf_c = curr.get("operatingCashflow", 0) or 0
        ocf_p = prev.get("operatingCashflow", 0) or 0
        rev_c = curr.get("totalRevenue", 1) or 1
        rev_p = prev.get("totalRevenue", 1) or 1
        ar_c  = curr.get("accountsReceivable", 0) or 0
        ar_p  = prev.get("accountsReceivable", 0) or 0

        ni_growth  = (ni_c  - ni_p)  / abs(ni_p  or 1)
        ocf_growth = (ocf_c - ocf_p) / abs(ocf_p or 1)
        rev_growth = (rev_c - rev_p) / abs(rev_p or 1)
        ar_growth  = (ar_c  - ar_p)  / abs(ar_p  or 1)

        # Earnings / cash divergence
        if ni_growth > 0.10 and ocf_growth < -0.05:
            add("HIGH", "EARNINGS_CASH_DIVERGENCE",
                f"Net income grew {ni_growth*100:.0f}% but operating cash flow fell {abs(ocf_growth)*100:.0f}%. "
                f"Earnings may not be backed by real cash.")

        # Receivables vs revenue
        if ar_growth > rev_growth * 1.5 and ar_growth > 0.15:
            add("HIGH", "RECEIVABLES_INFLATION",
                f"Accounts receivable grew {ar_growth*100:.0f}% vs revenue {rev_growth*100:.0f}%. "
                f"Possible channel stuffing or aggressive revenue recognition.")

        # DSO spike
        dso_c = (ar_c / rev_c) * 365 if rev_c else 0
        dso_p = (ar_p / rev_p) * 365 if rev_p else 0
        if dso_c > dso_p * 1.25 and dso_c > 60:
            add("HIGH", "DSO_SPIKE",
                f"Days Sales Outstanding jumped from {dso_p:.0f} to {dso_c:.0f} days. "
                f"Customers taking much longer to pay.")

        # Liquidity
        cr = (curr.get("currentAssets", 0) or 0) / (curr.get("currentLiabilities", 1) or 1)
        if cr < 1.0:
            add("HIGH", "LIQUIDITY_CRISIS",
                f"Current ratio = {cr:.2f}. Company may struggle to meet short-term obligations.")

        # Leverage
        equity = curr.get("totalEquity", 1) or 1
        debt   = curr.get("totalDebt", 0) or 0
        de = debt / equity
        if de > 3.0:
            add("HIGH", "HIGH_LEVERAGE",
                f"Debt/Equity = {de:.1f}x. Highly leveraged — vulnerable to rising rates.")

        # Goodwill
        goodwill = curr.get("goodwill", 0) or 0
        total_assets = curr.get("totalAssets", 1) or 1
        gw_pct = goodwill / total_assets
        if gw_pct > 0.30:
            add("MEDIUM", "GOODWILL_HEAVY",
                f"Goodwill = {gw_pct*100:.0f}% of total assets. "
                f"Heavy acquisition reliance. Risk of impairment write-downs.")

        # Dilution
        shares_c = curr.get("sharesOutstanding", 0) or 0
        shares_p = prev.get("sharesOutstanding", 0) or shares_c
        if shares_p > 0:
            dilution = (shares_c - shares_p) / shares_p
            if dilution > 0.03:
                add("MEDIUM", "SHARE_DILUTION",
                    f"Shares outstanding increased {dilution*100:.1f}% YoY. "
                    f"Earnings per share are being diluted.")

    except Exception as e:
        add("INFO", "SCAN_ERROR", f"Red flag scan encountered an error: {e}")

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    flags.sort(key=lambda x: order.get(x["severity"], 99))

    return {"total": len(flags), "flags": flags,
            "high": sum(1 for f in flags if f["severity"] == "HIGH"),
            "medium": sum(1 for f in flags if f["severity"] == "MEDIUM")}


# ─────────────────────────────────────────────────────────────────
# EDGAR XBRL — direct SEC filing data (authoritative financial statements)
# ─────────────────────────────────────────────────────────────────

XBRL_FACTS_URL  = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
XBRL_CACHE_DAYS = 30  # monthly refresh cycle matches user's use pattern

# Concepts are selected by matching annual period, filing date, then priority.
XBRL_CONCEPT_MAP: dict[str, tuple] = {
    "revenue":         ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "SalesRevenueNet", "SalesRevenueGoodsNet",
                        "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "net_income":      ("NetIncomeLoss", "ProfitLoss"),
    "operating_cf":    ("NetCashProvidedByUsedInOperatingActivities",),
    "capex":           ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "da":              ("DepreciationDepletionAndAmortization",
                        "DepreciationAndAmortization", "Depreciation"),
    "total_assets":    ("Assets",),
    "current_assets":  ("AssetsCurrent",),
    "total_liabilities":("Liabilities",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt":  ("LongTermDebt", "LongTermDebtNoncurrent"),
    "debt_noncurrent": ("LongTermDebtNoncurrent",),
    "debt_current_portion": ("LongTermDebtCurrent",),
    "short_term_borrowings": ("ShortTermBorrowings", "CommercialPaper"),
    "retained_earnings":("RetainedEarningsAccumulatedDeficit",),
    "equity":          ("StockholdersEquity",
                        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash":            ("CashAndCashEquivalentsAtCarryingValue",
                        "CashCashEquivalentsAndShortTermInvestments"),
    "goodwill":        ("Goodwill",),
    "ar":              ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "ebit":            ("OperatingIncomeLoss",),
    "gross_profit":    ("GrossProfit",),
    "cogs":            ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "investing_cf":    ("NetCashProvidedByUsedInInvestingActivities",),
    "intangibles":     ("FiniteLivedIntangibleAssetsNet",
                        "IntangibleAssetsNetExcludingGoodwill"),
}
XBRL_SHARES_CONCEPTS = (
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
)


def _pick_annual_value(entries: list, n: int = 0) -> tuple:
    facts = annual_facts(entries)
    return (float(facts[n]["val"]), facts[n]["end"]) if len(facts) > n else (None, "")


def fetch_xbrl_fundamentals(cik: str) -> dict:
    """
    Pull EDGAR XBRL company facts for one CIK and extract key annual financial metrics.
    Returns a flat dict of { field: value, field_prior: prior_year_value, field_date: end_date }.
    Results cached in SQLite for XBRL_CACHE_DAYS (30 days).
    """
    # 1. Check SQLite cache
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) as conn:
            row = conn.execute(
                "SELECT data_json, fetched_at FROM xbrl_cache WHERE cik = ?", (cik,)
            ).fetchone()
            if row:
                age_days = (datetime.now() - datetime.fromisoformat(row[1])).days
                if 0 <= age_days < XBRL_CACHE_DAYS:
                    cached = json.loads(row[0])
                    if cached.get("_schema_version") == SCHEMA_VERSION and cached.get('_source_receipt'):
                        return cached
    except Exception:
        pass

    # 2. Fetch from SEC EDGAR (rate-limited: 10 req/s max)
    # A source denial pauses company-facts requests across issuers for 15 minutes.
    # Cached, dated evidence remains available; the Yahoo fallback is unverified.
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        denied = conn.execute('SELECT observed_at FROM financial_source_documents WHERE status_code IN (403,429) AND source_url LIKE ? ORDER BY observed_at DESC LIMIT 1',
                              ('https://data.sec.gov/api/xbrl/companyfacts/%',)).fetchone()
    if denied and (datetime.now(timezone.utc)-datetime.fromisoformat(denied[0])).total_seconds() < 900:
        return {}
    url = XBRL_FACTS_URL.format(cik=cik)
    try:
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
        receipt = save_financial_source(DB_PATH, cik, resp)
        if resp.status_code in (404, 429):
            return {}
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {}

    result = extract_annual_financials(payload, XBRL_CONCEPT_MAP, XBRL_SHARES_CONCEPTS)
    result['_source_receipt'] = receipt
    result['_issuer_cik'] = str(payload.get('cik','')).zfill(10)

    # 5. Cache result (even empty, so we don't hammer SEC for dead tickers)
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO xbrl_cache (cik, data_json, fetched_at) VALUES (?,?,?)",
                (cik, json.dumps(result), datetime.now().isoformat())
            )
            conn.commit()
    except Exception:
        pass

    return result


def _beneish_from_dicts(curr: dict, prev: dict) -> dict | None:
    """
    Simplified Beneish M-Score from plain financial dicts (for XBRL screener path).
    Computes DSRI, GMI, SGI, TATA exactly; uses neutral 1.0 for components
    that require D&A / PPE / SG&A breakdown (DEPI, SGAI, LVGI, AQI).
    """
    try:
        st  = curr.get("totalRevenue");    st1 = prev.get("totalRevenue")
        cg  = curr.get("costOfRevenue");   cg1 = prev.get("costOfRevenue")
        ni  = curr.get("netIncome")
        ar  = curr.get("accountsReceivable"); ar1 = prev.get("accountsReceivable")
        ta  = curr.get("totalAssets");     ta1 = prev.get("totalAssets")
        ocf = curr.get("operatingCashflow")
        if not all(v is not None for v in [st, st1, ta, ta1]):
            return None
        if st == 0 or st1 == 0 or ta == 0 or ta1 == 0:
            return None
        DSRI  = (ar / st) / (ar1 / st1) if (ar and ar1 and st1) else 1.0
        gm_t  = (st  - (cg  or 0)) / st
        gm_t1 = (st1 - (cg1 or 0)) / st1
        GMI   = gm_t1 / gm_t if gm_t else 1.0
        SGI   = st / st1
        TATA  = (ni - (ocf or 0)) / ta if ni is not None else 0.0
        score = round(
            -4.84
            + 0.920 * DSRI + 0.528 * GMI + 0.404 * 1.0
            + 0.892 * SGI  + 0.115 * 1.0  - 0.172 * 1.0
            + 4.679 * TATA - 0.327 * 1.0,
            3
        )
        if   score > -1.78: verdict, color = "Manipulator",     "red"
        elif score > -2.22: verdict, color = "Gray Zone",       "yellow"
        else:               verdict, color = "Non-Manipulator", "green"
        return {"score": score, "verdict": verdict, "color": color}
    except Exception:
        return None


def apply_model_eligibility(result, scenario):
    with sqlite3.connect(DB_PATH, timeout=15) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='instrument_revisions'").fetchone():
            record = db.execute('SELECT evidence_json,retrieved_at,id FROM instrument_revisions WHERE ticker=? ORDER BY id DESC LIMIT 1', (result.get('ticker'),)).fetchone()
            if record:
                ref = {**json.loads(record[0]), 'retrieved_at': record[1], 'revision_id': record[2]}
                scenario['instrument'] = identity_eligibility(ref, result, research_now())
                if not scenario['instrument']['model_applicable']:
                    scenario.update(status='unavailable', price=None, reverse=None, sensitivity=[], reason='Authoritative security identity excludes this equity scenario.')
    result['valuation_scenario'] = scenario
    result['instrument_eligibility'] = scenario['instrument']
    result['valuation_status'] = 'Conditional scenarios, not verified forecasts.'
    if not scenario['instrument']['model_applicable']:
        for key in ('dcf_price', 'avg_fair_value', 'margin_of_safety', 'reverse_dcf', 'epv', 'magic_formula',
                    'roic_wacc', 'owner_earnings', 'fcf_yield', 'graham_number', 'net_net'):
            result[key] = None


def score_from_xbrl_and_market(ticker: str, xbrl: dict, yf_info: dict) -> dict:
    """
    Build a fully-scored data dict from EDGAR XBRL fundamentals + yfinance market data.
    XBRL = authoritative financial statements (balance sheet, income, cash flow).
    yfinance = market data only (price, PE, beta, growth rates).
    """
    valuation_shares = current_share_basis(yf_info)
    def _x(f): return xbrl.get(f)
    def sp(v):
        if v is None: return None
        try: return round(float(v), 4)
        except: return None

    # XBRL — current year
    rev_c    = _x("revenue")
    ni_c     = _x("net_income")
    ocf_c    = _x("operating_cf")
    capex_raw= _x("capex")
    capex_c  = -abs(capex_raw) if capex_raw is not None else None  # XBRL positive → make negative
    da_c     = _x("da")
    tot_a_c  = _x("total_assets")
    cur_a_c  = _x("current_assets")
    tot_l_c  = _x("total_liabilities")
    cur_l_c  = _x("current_liabilities")
    lt_debt_c= _x("long_term_debt")
    re_c     = _x("retained_earnings")
    equity_c = _x("equity")
    cash_c   = _x("cash")
    gw_c     = _x("goodwill") or 0
    ar_c     = _x("ar")
    ebit_c   = _x("ebit")
    gp_c     = _x("gross_profit")
    cogs_c   = _x("cogs")
    shares_c = _x("shares")
    inv_cf_c = _x("investing_cf")
    intang_c = _x("intangibles") or 0

    # XBRL — prior year
    rev_p    = _x("revenue_prior")
    ni_p     = _x("net_income_prior")
    ocf_p    = _x("operating_cf_prior")
    cur_a_p  = _x("current_assets_prior")
    cur_l_p  = _x("current_liabilities_prior")
    lt_debt_p= _x("long_term_debt_prior")
    tot_a_p  = _x("total_assets_prior")
    ar_p     = _x("ar_prior")
    shares_p = _x("shares_prior")
    cogs_p   = _x("cogs_prior")
    ebit_p   = _x("ebit_prior")
    # Two-year-prior (for 2-yr CAGR — more stable than single-year growth)
    rev_p2   = _x("revenue_prior2")
    ni_p2    = _x("net_income_prior2")
    ocf_p2   = _x("operating_cf_prior2")

    # yfinance — market data only
    price      = safe(yf_info.get("currentPrice") or yf_info.get("regularMarketPrice"), 0)
    mktcap     = safe(yf_info.get("marketCap"), 0)
    ev_val     = safe(yf_info.get("enterpriseValue"))
    beta_val   = safe(yf_info.get("beta"), 1.0)
    pe_ttm     = safe(yf_info.get("trailingPE"))
    bvps       = safe(yf_info.get("bookValue"))
    eps        = safe(yf_info.get("trailingEps"))
    rev_growth = safe(yf_info.get("revenueGrowth"))
    eps_growth = safe(yf_info.get("earningsGrowth"))
    op_margin  = safe(yf_info.get("operatingMargins"))

    # ── Industry classification — drives metric exclusions below ────────────
    _sector   = safe(yf_info.get("sector"),   "") or ""
    _industry = safe(yf_info.get("industry"), "") or ""
    _is_financial = (
        _sector in ("Financial Services",) or
        any(kw in _industry for kw in ("Bank", "Insurance", "Credit", "Mortgage", "Thrift", "Broker"))
    )
    _is_reit = "REIT" in _industry or _sector in ("Real Estate",)

    # Derived
    tot_debt_c = xbrl_total_debt(xbrl)
    net_debt   = net_debt_amount(tot_debt_c, cash_c)
    fcf_c      = free_cash_flow(ocf_c, capex_c)

    # Financial companies: FCF is not a meaningful metric (capital is the product,
    # deposits/float inflate OCF). Nulling prevents wrong DCF and rDCF signals.
    if _is_financial or _is_reit:
        fcf_c = None
    if not mktcap and price and shares_c:
        mktcap = price * shares_c
    if not ev_val and mktcap and net_debt is not None:
        ev_val = mktcap + net_debt

    curr_dict = {
        "netIncome": ni_c, "operatingCashflow": ocf_c, "totalRevenue": rev_c,
        "currentAssets": cur_a_c, "currentLiabilities": cur_l_c, "longTermDebt": lt_debt_c,
        "totalAssets": tot_a_c, "sharesOutstanding": shares_c, "costOfRevenue": cogs_c,
        "accountsReceivable": ar_c, "totalEquity": equity_c, "totalDebt": tot_debt_c,
        "goodwill": gw_c,
    }
    prev_dict = {
        "netIncome": ni_p, "operatingCashflow": ocf_p, "totalRevenue": rev_p,
        "currentAssets": cur_a_p, "currentLiabilities": cur_l_p, "longTermDebt": lt_debt_p,
        "totalAssets": tot_a_p, "sharesOutstanding": shares_p, "costOfRevenue": cogs_p,
        "accountsReceivable": ar_p,
    }

    # ── Scoring models ───────────────────────────────────────────────────────
    az = {"score": None, "zone": "N/A", "color": "gray"}
    if all(v is not None for v in [cur_a_c, cur_l_c, re_c, ebit_c, tot_a_c, tot_l_c]) and mktcap:
        wc = (cur_a_c or 0) - (cur_l_c or 0)
        az = altman_z(wc, re_c or 0, ebit_c or 0, mktcap, rev_c or 0, tot_a_c, tot_l_c or 1)

    pf = piotroski_f(curr_dict, prev_dict)

    sl = {"ratio": None, "quality": "N/A", "color": "gray"}
    if all(v is not None for v in [ocf_c, ni_c, tot_a_c]):
        sl = sloan_ratio(ni_c, ocf_c, tot_a_c, tot_a_p)

    rf = red_flags(curr_dict, prev_dict)
    bm = _beneish_from_dicts(curr_dict, prev_dict)

    mf = None
    # Magic Formula uses invested capital — not meaningful for financial/REIT companies
    if ebit_c and ev_val and ev_val > 0 and not _is_financial and not _is_reit:
        mf = calc_magic_formula(ebit_c, ev_val, tot_a_c, cur_a_c, cur_l_c, intang_c + gw_c, cash=cash_c)

    try:
        tax_rate = 1 - (ni_c / ebit_c) if (ebit_c and ni_c and ebit_c != 0) else 0.21
        tax_rate = max(0.10, min(0.40, tax_rate))
    except Exception:
        tax_rate = 0.21

    # ROIC/invested-capital metrics are structurally wrong for financial companies
    rw = None if (_is_financial or _is_reit) else calc_roic_wacc(
        ebit_c, tax_rate, tot_a_c, cur_l_c, cash_c, tot_debt_c, equity_c, beta_val, mktcap=mktcap
    )

    eps_g_pct = pct(eps_growth)
    peg_calc  = calc_peg(pe_ttm, eps_g_pct)
    oe        = calc_owner_earnings(ni_c, da_c, capex_c, valuation_shares, mktcap, ocf=ocf_c)
    r40       = calc_rule_of_40(pct(rev_growth), pct(op_margin))

    # Valuation estimates
    graham = None
    if eps and eps > 0 and bvps and bvps > 0:
        graham = round((22.5 * eps * bvps) ** 0.5, 2)

    scenario = valuation_scenario(yf_info, fcf_c, [(rev_c, rev_p), (ni_c, ni_p), (ocf_c, ocf_p)])
    dcf_price = scenario['price']
    rdcf = scenario['reverse']

    # EPV (Greenwald) — third independent valuation anchor
    epv_r = calc_epv(
        ebit_vals=[v for v in [ebit_c, ebit_p] if v is not None],
        tax_rate=tax_rate, da=da_c, capex=capex_c,
        wacc=0.10, cash=cash_c, total_debt=tot_debt_c,
        shares=valuation_shares, price=price or 0,
    )
    epv_val = epv_r["epv_per_share"] if epv_r else None
    epv_ok  = epv_val and price and 0 < epv_val < price * 3

    dcf_ok  = dcf_price and price and 0 < dcf_price < price * 2.5
    fvs     = [v for v in [dcf_price if dcf_ok else None, graham, epv_val if epv_ok else None] if v and v > 0]
    avg_fv  = round(sum(fvs) / len(fvs), 2) if fvs else None
    # Single-model penalty: only when truly 1 anchor (2+ models = no penalty needed)
    if avg_fv and len(fvs) == 1:
        avg_fv = round(avg_fv * 0.80, 2)
    mos     = round((avg_fv - price) / price * 100, 1) if avg_fv and price else None

    result = {
        "ticker":           ticker,
        "company_name":     safe(yf_info.get("longName"), ticker),
        "sector":           safe(yf_info.get("sector"),   "N/A"),
        "industry":         safe(yf_info.get("industry"), "N/A"),
        "price":            sp(price),
        "market_cap":       sp(mktcap),
        "pe_ttm":           sp(pe_ttm),
        "bvps":             sp(bvps),
        "eps":              sp(eps),
        "rev_growth":       pct(rev_growth),
        "eps_growth":       pct(eps_growth),
        "op_margin":        pct(op_margin),
        "revenue":          sp(rev_c),
        "net_income":       sp(ni_c),
        "operating_cf":     sp(ocf_c),
        "fcf":              sp(fcf_c),
        "total_debt":       sp(tot_debt_c),
        "cash":             sp(cash_c),
        "total_assets":     sp(tot_a_c),
        "total_equity":     sp(equity_c),
        "dcf_price":        sp(dcf_price),
        "graham_number":    sp(graham),
        "avg_fair_value":   sp(avg_fv),
        "margin_of_safety": sp(mos),
        "altman_z":         az,
        "piotroski_f":      pf,
        "sloan":            sl,
        "red_flags":        rf,
        "beneish_m":        bm,
        "fcf_yield":        sp(fcf_c / mktcap if fcf_c is not None and mktcap and mktcap > 10_000_000 else None),  # require ≥$10M market capitalization
        "reverse_dcf":      rdcf,
        "magic_formula":    mf,
        "epv":              epv_r,
        "roic_wacc":        rw,
        "peg_ratio":        peg_calc,
        "owner_earnings":   oe,
        "rule_of_40":       r40,
        "data_source":      "xbrl",
        "xbrl_filing_date": xbrl.get("total_assets_date", "Unknown"),
    }

    apply_model_eligibility(result, scenario)
    return result


def calc_quant_signal(data: dict) -> tuple:
    """
    Deterministic conviction score 1-99 — mirrors frontend convictionBreakdown() exactly.
    Returns (signal, price_target_base, upside_pct, score).
    No AI required. Neutral defaults used when data fields are missing.
    """
    if data.get('instrument_eligibility', {}).get('status') in ('excluded', 'unverified') or all(finite(data.get(k)) is None for k in ('revenue','net_income','operating_cf','capex')):
        return 'UNAVAILABLE', None, None, 0
    price  = data.get("price") or 0
    avg_fv = data.get("avg_fair_value")
    upside = ((avg_fv - price) / price * 100) if avg_fv and price and price > 0 else None

    pf_score  = (data.get("piotroski_f")    or {}).get("score")
    sloan_r   = (data.get("sloan")          or {}).get("ratio")
    oe_yield  = (data.get("owner_earnings") or {}).get("owner_earnings_yield")
    rw        = data.get("roic_wacc") or {}
    spread    = rw.get("spread_scored") if rw.get("spread_scored") is not None else rw.get("spread")
    magic_ey  = (data.get("magic_formula")  or {}).get("earnings_yield")
    az_score  = (data.get("altman_z")       or {}).get("score")
    bm_score  = (data.get("beneish_m")      or {}).get("score")
    r40_score = (data.get("rule_of_40")     or {}).get("score")
    peg_val   = (data.get("peg_ratio")      or {}).get("peg")
    rdcf_g    = (data.get("reverse_dcf")    or {}).get("implied_growth_rate")
    rf_high   = (data.get("red_flags")      or {}).get("high", 0) or 0
    fcf_yield = data.get("fcf_yield")  # decimal (0.05 = 5%)

    # 1. Valuation (0-30)
    # a) FCF Yield — independent market-based signal (uses actual price in denominator)
    #    Truly independent: the market sets the price, we just observe yield.
    if   fcf_yield is None:      fcf_y_pts = 3   # neutral
    elif fcf_yield < 0:          fcf_y_pts = 0   # negative FCF
    elif fcf_yield < 0.02:       fcf_y_pts = 1   # < 2%: very expensive
    elif fcf_yield < 0.04:       fcf_y_pts = 3   # 2–4%: below average
    elif fcf_yield < 0.06:       fcf_y_pts = 5   # 4–6%: decent
    elif fcf_yield < 0.08:       fcf_y_pts = 7   # 6–8%: strong
    else:                        fcf_y_pts = 8   # ≥ 8%: exceptional

    # b) Upside to avg_fair_value — model-derived, reduced weight (max 7 vs old 15)
    if   upside is None:    up_pts = 0
    elif upside >= 100:     up_pts = 4    # extreme → skepticism cap
    elif upside >= 60:      up_pts = 7   # sweet spot
    elif upside >= 35:      up_pts = 5
    elif upside >= 20:      up_pts = 4
    elif upside >= 10:      up_pts = 3
    elif upside >= 0:       up_pts = 2
    elif upside >= -15:     up_pts = 1
    else:                   up_pts = 0

    # c) PEG (0-8) — independent (earnings growth vs market PE)
    if   peg_val is None:            peg_pts = 4
    elif 0 < peg_val < 0.75:         peg_pts = 8
    elif peg_val < 1.0:              peg_pts = 6
    elif peg_val < 1.5:              peg_pts = 4
    elif peg_val < 2.5:              peg_pts = 2
    else:                            peg_pts = 0

    # d) rDCF (0-7) — independent (market-implied growth rate, not our model)
    if   rdcf_g is None:    rdcf_pts = 3
    elif rdcf_g < -15:      rdcf_pts = 3   # extreme negative — suspicious + penalty below
    elif rdcf_g < 0:        rdcf_pts = 6   # priced for mild decline — cheap but not max
    elif rdcf_g < 5:        rdcf_pts = 7   # very low growth priced in — best signal
    elif rdcf_g < 10:       rdcf_pts = 5
    elif rdcf_g < 20:       rdcf_pts = 3
    elif rdcf_g < 35:       rdcf_pts = 1
    else:                   rdcf_pts = 0

    val_pts = fcf_y_pts + up_pts + peg_pts + rdcf_pts

    # 2. Financial Quality (0-25)
    # Piotroski null reduced: missing data should not imply above-average quality
    if   pf_score is None:  fio_pts = 5   # was 7
    elif pf_score >= 8:     fio_pts = 13
    elif pf_score >= 6:     fio_pts = 10
    elif pf_score >= 4:     fio_pts = 6
    elif pf_score >= 2:     fio_pts = 2
    else:                   fio_pts = 0

    if   sloan_r is None:   sloan_pts = 4
    elif sloan_r < -10:     sloan_pts = 8
    elif sloan_r < 0:       sloan_pts = 6
    elif sloan_r < 5:       sloan_pts = 3
    elif sloan_r < 10:      sloan_pts = 1
    else:                   sloan_pts = 0

    if   oe_yield is None:  oe_pts = 2
    elif oe_yield > 8:      oe_pts = 4
    elif oe_yield > 5:      oe_pts = 3
    elif oe_yield > 2:      oe_pts = 2
    elif oe_yield > 0:      oe_pts = 1
    else:                   oe_pts = 0

    qual_pts = fio_pts + sloan_pts + oe_pts

    # 3. Value Creation (0-20)
    # Spread: raised bar for max — >15% was too easy for capital-light businesses;
    # null reduced — missing data should not assume strong value creation
    if   spread is None:    spread_pts = 4   # was 6
    elif spread > 30:       spread_pts = 12  # new top tier
    elif spread > 15:       spread_pts = 9   # was 12
    elif spread > 10:       spread_pts = 7   # was 10
    elif spread > 5:        spread_pts = 5   # was 7
    elif spread > 0:        spread_pts = 3   # was 4
    elif spread > -5:       spread_pts = 1
    else:                   spread_pts = 0

    if   magic_ey is None:  magic_pts = 4
    elif magic_ey > 10:     magic_pts = 8
    elif magic_ey > 7:      magic_pts = 6
    elif magic_ey > 4:      magic_pts = 4
    elif magic_ey > 2:      magic_pts = 2
    else:                   magic_pts = 0

    vc_pts = spread_pts + magic_pts

    # 4. Risk & Integrity (0-15)
    if   az_score is None:   az_pts = 5
    elif az_score > 3.0:     az_pts = 9
    elif az_score > 2.5:     az_pts = 7
    elif az_score > 1.81:    az_pts = 4
    elif az_score > 1.5:     az_pts = 1
    else:                    az_pts = 0

    if   bm_score is None:   ben_pts = 3
    elif bm_score < -2.22:   ben_pts = 6
    elif bm_score < -1.78:   ben_pts = 3
    else:                    ben_pts = 0

    risk_pts = az_pts + ben_pts

    # 5. Growth Efficiency (0-10)
    # R40 null reduced: missing growth data should not assume average efficiency
    if   r40_score is None:  r40_pts = 3   # was 5
    elif r40_score >= 60:    r40_pts = 10
    elif r40_score >= 40:    r40_pts = 8
    elif r40_score >= 20:    r40_pts = 4
    else:                    r40_pts = 0

    # Penalties
    # Overvaluation penalty — price significantly above model fair value
    if   upside is not None and upside < -50: overval_pen = 15
    elif upside is not None and upside < -35: overval_pen = 8
    elif upside is not None and upside < -20: overval_pen = 3
    else:                                     overval_pen = 0

    penalties = (
        min(15, rf_high * 4)
        + (5 if bm_score is not None and bm_score > -1.78 else 0)
        + (3 if az_score is not None and az_score < 1.81  else 0)
        + (3 if rdcf_g   is not None and rdcf_g   < -15   else 0)  # extreme negative rDCF
        + overval_pen
    )

    total = min(99, max(1, val_pts + qual_pts + vc_pts + risk_pts + r40_pts - penalties))

    if   total >= 70: signal = "STRONG_BUY"
    elif total >= 53: signal = "BUY"
    elif total >= 38: signal = "HOLD"
    elif total >= 22: signal = "AVOID"
    else:             signal = "STRONG_AVOID"

    upside_out = round(upside, 1) if upside is not None else None
    return signal, avg_fv, upside_out, total


# ─────────────────────────────────────────────────────────────────
# MAIN DATA FETCHER
# ─────────────────────────────────────────────────────────────────

MARKET_CACHE_TTL   = 4 * 3600    # 4 hours — "fresh" threshold
MARKET_CACHE_STALE = 7 * 86400   # 7 days — serve stale while refreshing in bg

# In-process dict cache — avoids SQLite reads for recently fetched tickers.
# Keyed by ticker, value is (data_dict, fetched_at_timestamp).
_mem_cache: dict[str, tuple[dict, float]] = {}
_mem_cache_lock = threading.Lock()

# Tracks tickers currently being refreshed in the background so we don't double-dispatch
_bg_refresh_in_flight: set[str] = set()
_bg_refresh_lock = threading.Lock()

def _get_cached_market_data(ticker: str, allow_stale: bool = False) -> dict | None:
    """Return cached market data. Checks in-process dict first, then SQLite.

    If allow_stale=True, returns data up to MARKET_CACHE_STALE seconds old.
    Otherwise only returns data fresher than MARKET_CACHE_TTL.
    """
    import time as _time
    now = _time.time()

    # 1. In-process dict cache (no I/O, no lock contention)
    with _mem_cache_lock:
        entry = _mem_cache.get(ticker)
    if entry:
        data, fetched_ts = entry
        age = now - fetched_ts
        if data.get("_fw_version") == SCHEMA_VERSION and 0 <= age < (MARKET_CACHE_STALE if allow_stale else MARKET_CACHE_TTL):
            return {**data, "cache_age_hours": round(age / 3600, 2)}

    # 2. SQLite (WAL mode ensures readers never block on writers)
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) as conn:
            row = conn.execute(
                "SELECT data_json, fetched_at FROM market_data_cache WHERE ticker = ?",
                (ticker,)
            ).fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row[1])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if 0 <= age < (MARKET_CACHE_STALE if allow_stale else MARKET_CACHE_TTL):
            data = json.loads(row[0])
            if data.get("_fw_version") != SCHEMA_VERSION:
                return None
            # Populate in-process cache so next read skips SQLite
            import time as _time2
            with _mem_cache_lock:
                _mem_cache[ticker] = (data, _time2.time() - age)
            return {**data, "cache_age_hours": round(age / 3600, 2)}
    except Exception:
        pass
    return None

def _save_market_data_cache(ticker: str, data: dict, sync_sqlite: bool = False) -> None:
    """Persist market data to SQLite cache and in-process dict.

    sync_sqlite=True: write SQLite synchronously (used from background refresh
    threads so the write completes before the thread exits, surviving machine restarts).
    """
    import time as _time
    # Tag data with current framework version so stale entries can be detected
    data["_fw_version"] = SCHEMA_VERSION
    data["market_data_fetched_at"] = datetime.utcnow().isoformat() + "Z"
    # Update in-process dict immediately (no I/O)
    with _mem_cache_lock:
        _mem_cache[ticker] = (data, _time.time())
    # Persist to SQLite
    def _write():
        try:
            with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO market_data_cache (ticker, data_json, fetched_at) VALUES (?,?,?)",
                    (ticker, json.dumps(data, default=str), datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
        except Exception:
            pass  # cache write failure is non-fatal
    if sync_sqlite:
        _write()
    else:
        threading.Thread(target=_write, daemon=True).start()

def _refresh_market_data_bg(ticker: str) -> None:
    """Background thread: fetch fresh data from yfinance and overwrite the cache entry.
    Does NOT delete the old entry first — concurrent user requests continue to get cache hits.
    Uses sync_sqlite=True so the write completes before the thread exits."""
    try:
        gather_stock_data(ticker, force_refresh=True, sync_sqlite=True)
    except Exception:
        pass
    finally:
        with _bg_refresh_lock:
            _bg_refresh_in_flight.discard(ticker)

def gather_stock_data(ticker: str, force_refresh: bool = False, sync_sqlite: bool = False) -> dict:
    ticker = ticker.upper().strip()
    if not force_refresh:
        # 1. Fresh cache hit → instant return
        cached = _get_cached_market_data(ticker, allow_stale=False)
        if cached:
            return cached
        # 2. Stale cache hit → serve immediately (never block the user), kick off background refresh.
        #    Always refresh in the background so the next request has fresh data with framework values.
        stale = _get_cached_market_data(ticker, allow_stale=True)
        if stale:
            with _bg_refresh_lock:
                if ticker not in _bg_refresh_in_flight:
                    _bg_refresh_in_flight.add(ticker)
                    threading.Thread(target=_refresh_market_data_bg, args=(ticker,), daemon=True).start()
            return stale
    # 3. No cache (or force_refresh) → synchronous yfinance fetch → saved to cache after
    yf_ticker = yf.Ticker(ticker)
    try:
        info = yf_ticker.info
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Market data fetch failed for {ticker}: {e}")

    if not info or not info.get("regularMarketPrice") and not info.get("currentPrice"):
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found or no data available.")

    valuation_shares = current_share_basis(info)
    # Polygon is authoritative for price; yfinance .info is kept for ratios/analyst data
    _poly_px = _polygon_price(ticker)
    price = float(_poly_px) if _poly_px else safe(info.get("currentPrice") or info.get("regularMarketPrice"), 0)
    quote_time = None
    if not _poly_px and finite(info.get('regularMarketTime')) is not None:
        try:
            quote_time = datetime.fromtimestamp(float(info['regularMarketTime']), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass

    # ── Annual financials (2 years for scoring models) ───────────────
    fins    = yf_ticker.financials          # income annual
    bal     = yf_ticker.balance_sheet       # balance annual
    cf      = yf_ticker.cashflow            # cash flow annual

    fins_q  = yf_ticker.quarterly_financials
    bal_q   = yf_ticker.quarterly_balance_sheet

    _xbrl = {}
    try:
        # Use the current SEC issuer mapping even for saved screen-only tickers.
        # A stale company row must not attach a former issuer's facts to a reused symbol.
        _issuer = get_cik_map().get(ticker)
        if _issuer:
            _xbrl = fetch_xbrl_fundamentals(_issuer['cik'])
    except Exception:
        pass
    annual_period = _xbrl.get("_period_end")
    if not annual_period:
        for frame in (fins, bal, cf):
            if frame is not None and not frame.empty:
                annual_period = str(max(frame.columns).date())
                break

    def row(df, *keys):
        return statement_value(df, keys, annual_period)

    def row_n(df, n, *keys):
        return statement_value(df, keys, annual_period, n)

    # Current year values
    rev_c    = safe(row(fins, "Total Revenue"))
    ni_c     = safe(row(fins, "Net Income"))
    gp_c     = safe(row(fins, "Gross Profit"))
    ebit_c   = safe(row(fins, "EBIT", "Operating Income"))
    da_c     = safe(row(cf,   "Depreciation And Amortization", "Depreciation"))
    ocf_c    = safe(row(cf,   "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    capex_c  = safe(row(cf,   "Capital Expenditure"))
    inv_cf_c = safe(row(cf,   "Investing Cash Flow", "Cash Flow From Continuing Investing Activities"))

    # Balance sheet current
    cash_c   = safe(row(bal,  "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    ar_c     = safe(row(bal,  "Accounts Receivable"))
    inv_c    = safe(row(bal,  "Inventory"))
    cur_a_c  = safe(row(bal,  "Current Assets"))
    tot_a_c  = safe(row(bal,  "Total Assets"))
    cur_l_c  = safe(row(bal,  "Current Liabilities"))
    lt_debt_c= safe(row(bal,  "Long Term Debt"))
    tot_debt_c= safe(row(bal, "Total Debt"))
    tot_l_c  = safe(row(bal,  "Total Liabilities Net Minority Interest", "Total Liabilities"))
    equity_c = safe(row(bal,  "Stockholders Equity", "Common Stock Equity"))
    re_c     = safe(row(bal,  "Retained Earnings"))
    gw_c     = safe(row(bal,  "Goodwill"))
    shares_c = safe(row(bal,  "Ordinary Shares Number", "Share Issued"))

    # Prior year values
    rev_p    = safe(row_n(fins, 1, "Total Revenue"))
    ni_p     = safe(row_n(fins, 1, "Net Income"))
    ocf_p    = safe(row_n(cf,   1, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    cur_a_p  = safe(row_n(bal,  1, "Current Assets"))
    cur_l_p  = safe(row_n(bal,  1, "Current Liabilities"))
    lt_debt_p= safe(row_n(bal,  1, "Long Term Debt"))
    tot_a_p  = safe(row_n(bal,  1, "Total Assets"))
    ar_p     = safe(row_n(bal,  1, "Accounts Receivable"))
    shares_p = safe(row_n(bal,  1, "Ordinary Shares Number", "Share Issued"))
    cogs_p   = safe(row_n(fins, 1, "Cost Of Revenue"))
    cogs_c   = safe(row(fins, "Cost Of Revenue"))

    # ── XBRL override: replace yfinance financial statements with authoritative EDGAR data ──
    # Keep yfinance for market data (price, mktcap, beta, PE, bvps, eps, growth rates).
    try:
        if _xbrl:
            def _xo(field, current):
                v = _xbrl.get(field)
                return v if v is not None else current
            rev_c     = _xo("revenue",             rev_c)
            ni_c      = _xo("net_income",           ni_c)
            ocf_c     = _xo("operating_cf",         ocf_c)
            _xcapex   = _xbrl.get("capex")
            if _xcapex is not None:
                capex_c = -abs(_xcapex)          # XBRL stores positive → make negative
            da_c      = _xo("da",                  da_c)
            tot_a_c   = _xo("total_assets",         tot_a_c)
            cur_a_c   = _xo("current_assets",       cur_a_c)
            cur_l_c   = _xo("current_liabilities",  cur_l_c)
            lt_debt_c = _xo("long_term_debt",       lt_debt_c)
            equity_c  = _xo("equity",              equity_c)
            cash_c    = _xo("cash",                cash_c)
            re_c      = _xo("retained_earnings",    re_c)
            ebit_c    = _xo("ebit",                ebit_c)
            tot_l_c   = _xo("total_liabilities",    tot_l_c)
            shares_c  = _xo("shares",              shares_c)
            gp_c      = _xo("gross_profit",         gp_c)
            cogs_c    = _xo("cogs",                cogs_c)
            gw_c      = _xo("goodwill",            gw_c)
            ar_c      = _xo("ar",                  ar_c)
            # Prior year
            tot_a_p   = _xo("total_assets_prior",  tot_a_p)
            ni_p      = _xo("net_income_prior",     ni_p)
            ocf_p     = _xo("operating_cf_prior",   ocf_p)
            rev_p     = _xo("revenue_prior",        rev_p)
            cur_a_p   = _xo("current_assets_prior", cur_a_p)
            cur_l_p   = _xo("current_liabilities_prior", cur_l_p)
            lt_debt_p = _xo("long_term_debt_prior", lt_debt_p)
            ar_p      = _xo("ar_prior", ar_p)
            shares_p  = _xo("shares_prior", shares_p)
            cogs_p    = _xo("cogs_prior", cogs_p)
            reported_debt = xbrl_total_debt(_xbrl)
            if reported_debt is not None:
                tot_debt_c = reported_debt
    except Exception:
        pass

    # Derived
    mktcap   = safe(info.get("marketCap"), 0)
    fcf_c    = free_cash_flow(ocf_c, capex_c)
    net_debt = net_debt_amount(tot_debt_c, cash_c)
    bvps     = safe(info.get("bookValue"))
    eps      = safe(info.get("trailingEps"))

    # Freshness — use last balance sheet date
    filing_date = "Unknown"
    try:
        if not bal.empty:
            filing_date = str(bal.columns[0].date())
    except:
        pass

    filing_date = annual_period or filing_date
    freshness = calc_freshness(filing_date, form_type="10-K")

    # Scoring models
    curr_dict = {
        "netIncome": ni_c, "operatingCashflow": ocf_c, "totalRevenue": rev_c,
        "currentAssets": cur_a_c, "currentLiabilities": cur_l_c, "longTermDebt": lt_debt_c,
        "totalAssets": tot_a_c, "sharesOutstanding": shares_c, "costOfRevenue": cogs_c,
        "accountsReceivable": ar_c, "totalEquity": equity_c, "totalDebt": tot_debt_c,
        "goodwill": gw_c,
    }
    prev_dict = {
        "netIncome": ni_p, "operatingCashflow": ocf_p, "totalRevenue": rev_p,
        "currentAssets": cur_a_p, "currentLiabilities": cur_l_p, "longTermDebt": lt_debt_p,
        "totalAssets": tot_a_p, "sharesOutstanding": shares_p, "costOfRevenue": cogs_p,
        "accountsReceivable": ar_p,
    }

    # Altman Z
    az = {"score": None, "zone": "N/A", "color": "gray"}
    if all(v is not None for v in [cur_a_c, cur_l_c, re_c, ebit_c, mktcap, rev_c, tot_a_c, tot_l_c]):
        wc = (cur_a_c or 0) - (cur_l_c or 0)
        az = altman_z(wc, re_c or 0, ebit_c or 0, mktcap, rev_c, tot_a_c, tot_l_c or 1)

    # Piotroski
    pf = piotroski_f(curr_dict, prev_dict)

    # Sloan
    sl = {"ratio": None, "quality": "N/A", "color": "gray"}
    if ocf_c and ni_c and tot_a_c:
        sl = sloan_ratio(ni_c, ocf_c, tot_a_c, tot_a_p)

    # Red flags
    rf = red_flags(curr_dict, prev_dict)

    # Graham number
    graham = None
    if eps and eps > 0 and bvps and bvps > 0:
        graham = round((22.5 * eps * bvps) ** 0.5, 2)

    scenario = valuation_scenario(info, fcf_c, [(rev_c, rev_p), (ni_c, ni_p), (ocf_c, ocf_p)])
    dcf_price = scenario['price']

    # avg_fair_value blend — computed after EPV (see below after Framework 4)

    # Quarterly revenue trend (last 4 quarters)
    rev_trend = []
    try:
        if not fins_q.empty and "Total Revenue" in fins_q.index:
            qrevs = fins_q.loc["Total Revenue"].dropna()
            for i, (date, val) in enumerate(qrevs.items()):
                if i >= 4: break
                rev_trend.append({"quarter": str(date.date()), "revenue": float(val)})
            rev_trend.reverse()
    except:
        pass

    # NI trend quarterly
    ni_trend = []
    try:
        if not fins_q.empty and "Net Income" in fins_q.index:
            qni = fins_q.loc["Net Income"].dropna()
            for i, (date, val) in enumerate(qni.items()):
                if i >= 4: break
                ni_trend.append({"quarter": str(date.date()), "net_income": float(val)})
            ni_trend.reverse()
    except:
        pass

    def sp(v):
        if v is None: return None
        try: return round(float(v), 4)
        except: return None

    # ── Tax rate estimate ────────────────────────────────────────────
    try:
        tax_rate_est = 1 - (ni_c / ebit_c) if (ebit_c and ni_c and ebit_c != 0) else 0.21
        tax_rate_est = max(0.10, min(0.40, tax_rate_est))
    except Exception:
        tax_rate_est = 0.21

    # ── WACC estimate ────────────────────────────────────────────────
    try:
        beta_val_w = max(0.5, min(2.5, safe(info.get("beta"), 1.0) or 1.0))
        risk_free_w = 0.043
        erp_w = 0.055
        cost_of_equity_w = risk_free_w + beta_val_w * erp_w
        mc_w = mktcap if mktcap and mktcap > 0 else max((equity_c or 0), 1)
        tot_cap_w = mc_w + (tot_debt_c or 0)
        eq_wt = max(0.1, min(0.95, mc_w / tot_cap_w)) if tot_cap_w > 0 else 0.8
        dbt_wt = 1 - eq_wt
        wacc_est_adv = cost_of_equity_w * eq_wt + 0.05 * (1 - tax_rate_est) * dbt_wt
    except Exception:
        wacc_est_adv = 0.10

    # ── Framework 1: Beneish M-Score ─────────────────────────────────
    bm = calc_beneish(fins, bal, cf)

    # ── Framework 2: Reverse DCF ─────────────────────────────────────
    rdcf = scenario['reverse']

    # ── Framework 3: Magic Formula ────────────────────────────────────
    ev_val = safe(info.get("enterpriseValue"))
    intang_c = safe(row(bal, "Other Intangible Assets", "Intangible Assets")) or 0
    mf = calc_magic_formula(
        ebit=ebit_c, enterprise_value=ev_val,
        total_assets=tot_a_c, current_assets=cur_a_c,
        current_liabilities=cur_l_c, intangibles=intang_c + (gw_c or 0),
        cash=cash_c
    )

    # ── Framework 4: EPV ─────────────────────────────────────────────
    ebit_vals = []
    for i in range(3):
        v = safe(row_n(fins, i, "EBIT", "Operating Income"))
        if v is not None:
            ebit_vals.append(v)
    epv = calc_epv(
        ebit_vals=ebit_vals, tax_rate=tax_rate_est,
        da=da_c, capex=capex_c, wacc=wacc_est_adv,
        cash=cash_c, total_debt=tot_debt_c, shares=valuation_shares, price=price
    )

    # ── avg_fair_value: blend DCF + Graham + EPV (three independent anchors) ──
    epv_val     = epv["epv_per_share"] if epv else None
    epv_ok      = epv_val and price and 0 < epv_val < price * 3
    dcf_reliable = dcf_price and price and 0 < dcf_price < price * 2.5
    fair_values = [v for v in [dcf_price if dcf_reliable else None, graham, epv_val if epv_ok else None] if v and v > 0]
    avg_fv = round(sum(fair_values) / len(fair_values), 2) if fair_values else None
    # Single-model penalty: only when truly 1 anchor (2+ models = no penalty)
    if avg_fv and len(fair_values) == 1:
        avg_fv = round(avg_fv * 0.80, 2)
    mos = round((avg_fv - price) / price * 100, 1) if avg_fv and price else None

    # ── Framework 5: ROIC vs WACC ────────────────────────────────────
    rw = calc_roic_wacc(
        ebit=ebit_c, tax_rate=tax_rate_est,
        total_assets=tot_a_c, current_liabilities=cur_l_c,
        cash=cash_c, total_debt=tot_debt_c, equity=equity_c,
        beta=safe(info.get("beta"), 1.0), mktcap=mktcap
    )

    # ── Framework 6: PEG Ratio ───────────────────────────────────────
    pe_ttm_val = safe(info.get("trailingPE"))
    eps_growth_pct = safe(info.get("earningsGrowth"))
    if eps_growth_pct is not None:
        eps_growth_pct = eps_growth_pct * 100
    peg_calc = calc_peg(pe_ttm=pe_ttm_val, eps_growth_rate=eps_growth_pct)

    # ── Framework 7: Net-Net ─────────────────────────────────────────
    nn = calc_net_net(
        current_assets=cur_a_c, total_liabilities=tot_l_c,
        shares=valuation_shares, price=price
    )

    # ── Framework 8: Owner Earnings ──────────────────────────────────
    oe = calc_owner_earnings(
        net_income=ni_c, da=da_c, capex=capex_c,
        shares=valuation_shares, mktcap=mktcap, ocf=ocf_c
    )

    # ── Framework 9: DuPont Analysis ─────────────────────────────────
    dp = calc_dupont(
        net_income=ni_c, revenue=rev_c,
        total_assets=tot_a_c, equity=equity_c
    )

    # ── Framework 10: Rule of 40 ─────────────────────────────────────
    rev_growth_pct_r40 = pct(info.get("revenueGrowth"))
    op_margin_pct_r40  = pct(info.get("operatingMargins"))
    r40 = calc_rule_of_40(
        revenue_growth_pct=rev_growth_pct_r40,
        op_margin_pct=op_margin_pct_r40
    )

    result = {
        "ticker": ticker,
        "company_name": safe(info.get("longName"), ticker),
        "sector": safe(info.get("sector"), "N/A"),
        "industry": safe(info.get("industry"), "N/A"),
        "employees": safe(info.get("fullTimeEmployees")),
        "description": (info.get("longBusinessSummary") or "")[:500],
        "exchange": safe(info.get("exchange"), "N/A"),

        # Market data
        "price": sp(price),
        "quote_as_of": quote_time,
        "quote_currency": info.get('currency'),
        "quote_source": "Yahoo Finance quote" if not _poly_px else "Polygon quote; execution timestamp unavailable",
        "market_cap": sp(mktcap),
        "enterprise_value": sp(info.get("enterpriseValue")),
        "beta": sp(info.get("beta")),
        "high_52w": sp(info.get("fiftyTwoWeekHigh")),
        "low_52w": sp(info.get("fiftyTwoWeekLow")),
        "avg_volume": safe(info.get("averageVolume")),
        "pct_52w": pct(info.get("52WeekChange")),
        "short_pct_float": pct(info.get("shortPercentOfFloat")),
        "shares_outstanding": sp(valuation_shares),

        # Valuation ratios
        "pe_ttm": sp(info.get("trailingPE")),
        "pe_fwd": sp(info.get("forwardPE")),
        "pb": sp(info.get("priceToBook")),
        "ps": sp(info.get("priceToSalesTrailing12Months")),
        "peg": sp(info.get("pegRatio")),
        "ev_ebitda": sp(info.get("enterpriseToEbitda")),
        "ev_revenue": sp(info.get("enterpriseToRevenue")),
        "fcf_yield": sp(fcf_c / mktcap if fcf_c is not None and mktcap and mktcap > 10_000_000 else None),
        "dividend_yield": dividend_yield_pct(info, price),
        "payout_ratio": pct(info.get("payoutRatio")),

        # Profitability
        "gross_margin": pct(info.get("grossMargins")),
        "op_margin": pct(info.get("operatingMargins")),
        "net_margin": pct(info.get("profitMargins")),
        "roe": pct(info.get("returnOnEquity")),
        "roa": pct(info.get("returnOnAssets")),
        "rev_growth": pct(info.get("revenueGrowth")),
        "eps_growth": pct(info.get("earningsGrowth")),
        "eps": sp(info.get("trailingEps")),

        # Financials
        "revenue": sp(rev_c),
        "gross_profit": sp(gp_c),
        "ebit": sp(ebit_c),
        "net_income": sp(ni_c),
        "operating_cf": sp(ocf_c),
        "capex": sp(capex_c),
        "fcf": sp(fcf_c),
        "da": sp(da_c),

        # Balance sheet
        "cash": sp(cash_c),
        "total_debt": sp(tot_debt_c),
        "net_debt": sp(net_debt),
        "total_assets": sp(tot_a_c),
        "total_equity": sp(equity_c),
        "retained_earnings": sp(re_c),
        "goodwill": sp(gw_c),
        "current_ratio": sp(info.get("currentRatio")),
        "quick_ratio": sp(info.get("quickRatio")),
        "de_ratio": debt_equity_ratio(tot_debt_c, equity_c),
        "bvps": sp(bvps),

        # Analyst
        "target_price": sp(info.get("targetMeanPrice")),
        "analyst_count": safe(info.get("numberOfAnalystOpinions")),
        "analyst_rec": sp(info.get("recommendationMean")),

        # Valuation estimates
        "dcf_price": sp(dcf_price),
        "graham_number": sp(graham),
        "avg_fair_value": sp(avg_fv),
        "margin_of_safety": sp(mos),

        # Scoring models
        "altman_z": az,
        "piotroski_f": pf,
        "sloan": sl,
        "red_flags": rf,

        # Advanced valuation frameworks
        "beneish_m": bm,
        "reverse_dcf": rdcf,
        "magic_formula": mf,
        "epv": epv,
        "roic_wacc": rw,
        "peg_ratio": peg_calc,
        "net_net": nn,
        "owner_earnings": oe,
        "dupont": dp,
        "rule_of_40": r40,

        # Trends
        "revenue_trend": rev_trend,
        "ni_trend": ni_trend,

        # Freshness
        "freshness": freshness,
        "financial_period_end": annual_period,
        "financial_data_source": "SEC XBRL with period-matched market-provider fallback" if _xbrl else "Yahoo Finance annual statements (provider period labels)",
        "financial_currency": "USD" if all(finite(_xbrl.get(k)) is not None for k in ('revenue','net_income','operating_cf','capex')) else info.get('financialCurrency'),
        "financial_provenance": _xbrl.get("_provenance", {}),
        "financial_source_receipt": _xbrl.get('_source_receipt'),
        "cik": _xbrl.get('_issuer_cik'),
        "sec_issuer_mapping": _issuer if _xbrl else None,
        "valuation_status": "Heuristic estimates; not a validated price forecast.",

        # For AI prompt
        "filing_date": filing_date,
    }
    apply_model_eligibility(result, scenario)
    _save_market_data_cache(ticker, result, sync_sqlite=sync_sqlite)
    return result


# ─────────────────────────────────────────────────────────────────
# AI PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an elite financial analyst and forensic accountant with 25 years of experience.
Your methodology combines Benjamin Graham's value investing, Beneish's forensic accounting, and quantitative models (Altman Z, Piotroski F, Sloan accrual).

Your job: determine if a stock's current price is justified by its fundamentals and produce a structured investment analysis.

RULES:
- Base all analysis STRICTLY on the numbers provided. Never invent or extrapolate beyond given data.
- When data is stale (freshness score < 75), explicitly qualify your confidence.
- Call out all red flags, even uncomfortable ones.
- Note when scoring models don't apply to the sector (e.g., Altman Z for banks).
- Provider ratios, quotes, analyst targets and scenario assumptions are unverified unless original-source matching is supplied. Arithmetic agreement alone is not verification or evidence of expected returns.
- Be concise and actionable — every sentence should add value.
- This is financial analysis, NOT financial advice."""


def _edgar_section(filings_data: dict | None) -> str:
    if not filings_data:
        return ""
    s = filings_data.get("summary") or {}
    last_10k = s.get("last_10k")
    last_10q = s.get("last_10q")
    return f"""
EDGAR FILING ACTIVITY
  Last 10-K filed: {last_10k['filed_date'] if last_10k else 'N/A'} (period: {last_10k.get('period_of_report','N/A') if last_10k else 'N/A'})
  Last 10-Q filed: {last_10q['filed_date'] if last_10q else 'N/A'} (period: {last_10q.get('period_of_report','N/A') if last_10q else 'N/A'})
  Recent 8-K filings (90 days): {s.get('recent_8k_count', 0)}
  Recent insider transactions Form 4 (90 days): {s.get('recent_form4_count', 0)}
  Note: High 8-K count may indicate material events (guidance changes, M&A, leadership changes).
  Note: Filing counts do not establish buying or selling. Do not infer direction,
  discretion, or trader performance without parsed transaction details."""


def _advanced_frameworks_section(d: dict) -> str:
    """Build the ADVANCED VALUATION FRAMEWORKS section for the AI prompt."""
    def fv(v, decimals=2): return f"{v:.{decimals}f}" if v is not None else "N/A"
    def fp2(v): return f"{v:.1f}%" if v is not None else "N/A"
    lines = ["\nADVANCED VALUATION FRAMEWORKS", "  Retained model outputs are conditional heuristics; source completeness, calibrated assumptions, forecasts and expected returns remain unverified."]

    bm = d.get("beneish_m") or {}
    if bm.get("score") is not None:
        c = bm.get("components") or {}
        lines.append(f"  Beneish M-Score: {fv(bm.get('score'))} → {bm.get('verdict','N/A')} (manipulation risk)")
        lines.append(f"    Components: DSRI={fv(c.get('DSRI'))} GMI={fv(c.get('GMI'))} AQI={fv(c.get('AQI'))} SGI={fv(c.get('SGI'))} DEPI={fv(c.get('DEPI'))} SGAI={fv(c.get('SGAI'))} LVGI={fv(c.get('LVGI'))} TATA={fv(c.get('TATA'),4)}")

    rdcf = d.get("reverse_dcf") or {}
    if rdcf.get("implied_growth_rate") is not None:
        lines.append(f"  Reverse DCF Implied Growth: {fp2(rdcf.get('implied_growth_rate'))}/yr → {rdcf.get('assessment','N/A')}")
        lines.append(f"    ({rdcf.get('interpretation','N/A')})")

    mf = d.get("magic_formula") or {}
    if mf.get("earnings_yield") is not None:
        lines.append(f"  Magic Formula: Earnings Yield={fp2(mf.get('earnings_yield'))} | ROIC={fp2(mf.get('roic'))} → {mf.get('verdict','N/A')}")

    epv = d.get("epv") or {}
    if epv.get("epv_per_share") is not None:
        lines.append(f"  EPV (No-Growth Value): ${fv(epv.get('epv_per_share'))}/share | Growth Premium: {fp2(epv.get('growth_premium_pct'))}")
        lines.append(f"    (You are paying {fp2(epv.get('growth_premium_pct'))} above the no-growth intrinsic value)")

    rw = d.get("roic_wacc") or {}
    if rw.get("roic") is not None:
        lines.append(f"  ROIC vs WACC: ROIC={fp2(rw.get('roic'))} vs WACC={fp2(rw.get('wacc'))} → Spread={fp2(rw.get('spread'))} → {rw.get('verdict','N/A')}")

    pg = d.get("peg_ratio") or {}
    if pg.get("peg") is not None:
        lines.append(f"  PEG Ratio: {fv(pg.get('peg'))} → {pg.get('verdict','N/A')}")

    nn = d.get("net_net") or {}
    if nn.get("nnwc_per_share") is not None:
        lines.append(f"  Net-Net (Graham Deep Value): ${fv(nn.get('nnwc_per_share'))}/share vs price ${fv(d.get('price'))} → {nn.get('verdict','N/A')}")

    oe = d.get("owner_earnings") or {}
    if oe.get("owner_earnings_per_share") is not None:
        lines.append(f"  Owner Earnings (Buffett): ${fv(oe.get('owner_earnings_per_share'))}/share | Yield={fp2(oe.get('owner_earnings_yield'))}")

    dp = (d.get("dupont") or {}) if dupont_supported(d) else {}
    if dp.get("roe") is not None:
        lines.append(f"  DuPont ROE Decomposition: {fp2(dp.get('roe'))} = {fp2(dp.get('net_margin'))} margin × {fv(dp.get('asset_turnover'))}x turnover × {fv(dp.get('equity_multiplier'))}x leverage")
        lines.append(f"    Dominant driver: {dp.get('dominant_driver','N/A')} → {dp.get('quality','N/A')}")

    r40 = d.get("rule_of_40") or {}
    if r40.get("score") is not None:
        lines.append(f"  Rule of 40: {fv(r40.get('score'),1)} ({fp2(r40.get('revenue_growth_pct'))} growth + {fp2(r40.get('op_margin_pct'))} margin) → {r40.get('verdict','N/A')}")

    return "\n".join(lines)


def build_prompt(d: dict, filings_data: dict | None = None) -> str:
    def f(v, suffix=""): return f"{v}{suffix}" if v is not None else "N/A"
    def fp(v): return f"{v}%" if v is not None else "N/A"
    def fm(v):
        if v is None: return "N/A"
        try:
            v = float(v)
            if abs(v) >= 1e9: return f"${v/1e9:.2f}B"
            if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
            return f"${v:,.0f}"
        except: return "N/A"

    flags_text = ""
    for fl in (d.get("red_flags") or {}).get("flags", []):
        flags_text += f"  [{fl['severity']}] {fl['code']}: {fl['detail']}\n"
    if not flags_text:
        flags_text = "  None detected."

    az = d.get("altman_z") or {}
    pf = d.get("piotroski_f") or {}
    sl = d.get("sloan") or {}
    fr = d.get("freshness") or {}
    pf_bd = pf.get("breakdown") or {}

    return f"""Analyze this stock and produce your response in EXACTLY these 6 sections:

## 1. EXECUTIVE VERDICT
(3-4 sentences: what is this company, is the price justified, the single most important thing to know)

## 2. INVESTMENT SIGNAL
Signal: [STRONG BUY / BUY / HOLD / AVOID / STRONG AVOID]
Confidence: [HIGH / MEDIUM / LOW] — reason
Price Assessment: [UNDERVALUED / FAIRLY VALUED / OVERVALUED] by approximately X%
If any CRITICAL red flags exist → signal cannot be higher than AVOID.

## 3. VALUATION ANALYSIS
Interpret each ratio. State which models apply/don't apply to this sector.
Fair value range: Low / Base / High estimate.

## 4. FINANCIAL HEALTH & EARNINGS QUALITY
Health score 1-10. Interpret Altman Z, Piotroski F, Sloan ratio.
Are reported earnings trustworthy? Why or why not?

## 5. RED FLAGS & RISKS
Interpret all flags. Rate overall credibility: HIGH / ADEQUATE / QUESTIONABLE / LOW.
Bull case vs Bear case (2 points each, with price targets if possible).

## 6. FINAL SCORECARD
| Dimension | Score (1-10) | Comment |
|---|---|---|
| Valuation | X | one line |
| Financial Health | X | one line |
| Earnings Quality | X | one line |
| Growth Trajectory | X | one line |
| Risk Profile | X | one line |
| OVERALL | X | summary |

FINAL: [SIGNAL] at ${f(d.get('price'))} — one sentence summary.

─────────────────────────────────────────────────────────────
COMPANY DATA
─────────────────────────────────────────────────────────────
Ticker: {d['ticker']} | {d['company_name']}
Sector: {d['sector']} | Industry: {d['industry']}

MARKET DATA
  Price: ${f(d.get('price'))}  |  Mkt Cap: {fm(d.get('market_cap'))}  |  EV: {fm(d.get('enterprise_value'))}
  52W Range: ${f(d.get('low_52w'))} – ${f(d.get('high_52w'))}  |  52W Change: {fp(d.get('pct_52w'))}
  Beta: {f(d.get('beta'))}  |  Short %: {fp(d.get('short_pct_float'))}

DATA FRESHNESS: Score {fr.get('score', 0)}/100 — {fr.get('label', 'N/A')} ({fr.get('days_old', '?')} days old)
{fr.get('warning', '')}

VALUATION RATIOS
  P/E (TTM): {f(d.get('pe_ttm'))}x  |  Forward P/E: {f(d.get('pe_fwd'))}x  |  P/B: {f(d.get('pb'))}x
  P/S: {f(d.get('ps'))}x  |  EV/EBITDA: {f(d.get('ev_ebitda'))}x  |  PEG: {f(d.get('peg'))}
  FCF Yield: {fp(d.get('fcf_yield') * 100 if d.get('fcf_yield') else None)}  |  Div Yield: {fp(d.get('dividend_yield'))}

PROFITABILITY
  Gross Margin: {fp(d.get('gross_margin'))}  |  Op Margin: {fp(d.get('op_margin'))}  |  Net Margin: {fp(d.get('net_margin'))}
  ROE: {fp(d.get('roe'))}  |  ROA: {fp(d.get('roa'))}
  Revenue Growth (YoY): {fp(d.get('rev_growth'))}  |  EPS Growth: {fp(d.get('eps_growth'))}

FINANCIALS (Annual)
  Revenue: {fm(d.get('revenue'))}  |  Gross Profit: {fm(d.get('gross_profit'))}  |  EBIT: {fm(d.get('ebit'))}
  Net Income: {fm(d.get('net_income'))}  |  OCF: {fm(d.get('operating_cf'))}  |  FCF: {fm(d.get('fcf'))}

BALANCE SHEET
  Cash: {fm(d.get('cash'))}  |  Total Debt: {fm(d.get('total_debt'))}  |  Net Debt: {fm(d.get('net_debt'))}
  Total Assets: {fm(d.get('total_assets'))}  |  Equity: {fm(d.get('total_equity'))}  |  Goodwill: {fm(d.get('goodwill'))}
  Current Ratio: {f(d.get('current_ratio'))}  |  Quick Ratio: {f(d.get('quick_ratio'))}  |  D/E: {f(d.get('de_ratio'))}

VALUATION ESTIMATES (quantitative models only — no analyst consensus)
  DCF Estimate: ${f(d.get('dcf_price'))}  |  Graham Number: ${f(d.get('graham_number'))}
  Avg Fair Value (DCF + Graham): ${f(d.get('avg_fair_value'))}  |  Margin of Safety: {fp(d.get('margin_of_safety'))}

FORENSIC SCORES
  Altman Z-Score: {f(az.get('score'))} → {az.get('zone', 'N/A')}
  Piotroski F-Score: {f(pf.get('score'))}/9 → {pf.get('verdict', 'N/A')}
    F1(ROA+): {pf_bd.get('F1_positive_roa','?')}  F2(OCF+): {pf_bd.get('F2_positive_ocf','?')}  F3(ROA↑): {pf_bd.get('F3_improving_roa','?')}  F4(OCF>NI): {pf_bd.get('F4_cash_quality','?')}
    F5(Debt↓): {pf_bd.get('F5_decreasing_leverage','?')}  F6(Liq↑): {pf_bd.get('F6_improving_liquidity','?')}  F7(NoDilution): {pf_bd.get('F7_no_dilution','?')}
    F8(GM↑): {pf_bd.get('F8_improving_gross_margin','?')}  F9(AssetTurn↑): {pf_bd.get('F9_improving_asset_turnover','?')}
  Sloan Accrual Ratio: {f(sl.get('ratio'))}% → {sl.get('quality', 'N/A')}

RED FLAGS DETECTED ({d.get('red_flags', {}).get('total', 0)} total):
{flags_text}
{_advanced_frameworks_section(d)}
{_edgar_section(filings_data)}"""


# ─────────────────────────────────────────────────────────────────
# EDGAR HELPERS
# ─────────────────────────────────────────────────────────────────

def get_cik_map() -> dict:
    global _cik_map, _cik_map_fetched_at
    age = (datetime.now() - _cik_map_fetched_at).total_seconds() if _cik_map_fetched_at else 999999
    if _cik_map is None or age > 86400:
        try:
            resp = requests.get(EDGAR_TICKERS_URL, headers=EDGAR_HEADERS, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            mapping_receipt = save_financial_source(DB_PATH, '0', resp, source_url=EDGAR_TICKERS_URL)
            _cik_map = {
                v["ticker"].upper(): {
                    "cik": str(v["cik_str"]).zfill(10),
                    "name": v["title"],
                    "source_receipt": mapping_receipt,
                }
                for v in raw.values()
            }
            _cik_map_fetched_at = datetime.now()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to fetch EDGAR ticker list: {e}")
    return _cik_map


def _build_filing_summary(filings: list[dict]) -> dict:
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    last_10k = next((f for f in filings if f["form_type"] == "10-K"), None)
    last_10q = next((f for f in filings if f["form_type"] == "10-Q"), None)
    recent_8k = sum(1 for f in filings if f["form_type"] == "8-K" and f["filed_date"] >= cutoff)
    recent_f4 = sum(1 for f in filings if f["form_type"] == "4"   and f["filed_date"] >= cutoff)
    return {
        "last_10k":            {"filed_date": last_10k["filed_date"], "period_of_report": last_10k["period_of_report"], "url": last_10k["url"]} if last_10k else None,
        "last_10q":            {"filed_date": last_10q["filed_date"], "period_of_report": last_10q["period_of_report"], "url": last_10q["url"]} if last_10q else None,
        "recent_8k_count":     recent_8k,
        "recent_form4_count":  recent_f4,
    }


def fetch_edgar_filings(ticker: str) -> tuple[bool, list[dict]]:
    """Fetch filing metadata from EDGAR for a ticker, caching in SQLite.
    Returns (cached: bool, filings: list).
    Note: only reads filings.recent — older filings (filings.files pagination) are not fetched.
    """
    ticker = ticker.upper().strip()
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            "SELECT id, last_updated FROM companies WHERE ticker = ?", (ticker,)
        ).fetchone()

        should_fetch = True
        company_id = None
        if row:
            company_id = row["id"]
            age_days = (datetime.now() - datetime.fromisoformat(row["last_updated"])).days
            if age_days < EDGAR_CACHE_DAYS:
                should_fetch = False

        if should_fetch:
            cik_map = get_cik_map()
            cik_info = cik_map.get(ticker)
            if not cik_info:
                raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found in EDGAR database.")

            cik = cik_info["cik"]  # zero-padded 10-digit string
            try:
                resp = requests.get(
                    EDGAR_SUBMISSIONS_URL.format(cik=cik),
                    headers=EDGAR_HEADERS,
                    timeout=20,
                )
                if resp.status_code == 429:
                    raise HTTPException(status_code=429, detail="EDGAR rate limit hit. Try again shortly.")
                resp.raise_for_status()
                data = resp.json()
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"EDGAR fetch failed: {e}")

            recent = data.get("filings", {}).get("recent", {})
            accessions   = recent.get("accessionNumber", [])
            filing_dates = recent.get("filingDate", [])
            report_dates = recent.get("reportDate", [])
            forms        = recent.get("form", [])
            primary_docs = recent.get("primaryDocument", [])
            descriptions = recent.get("primaryDocDescription", [])

            parsed = []
            for i, form in enumerate(forms):
                if form not in TRACKED_FORMS:
                    continue
                acc_no      = accessions[i]
                acc_nodash  = acc_no.replace("-", "")
                doc         = primary_docs[i] if i < len(primary_docs) else ""
                filing_url  = EDGAR_FILING_BASE.format(
                    cik=int(cik), accession=acc_nodash, doc=doc
                )
                parsed.append({
                    "form_type":        form,
                    "filed_date":       filing_dates[i] if i < len(filing_dates) else "",
                    "period_of_report": report_dates[i] if i < len(report_dates) and report_dates[i] else None,
                    "accession_number": acc_no,
                    "description":      descriptions[i] if i < len(descriptions) and descriptions[i] else form,
                    "url":              filing_url,
                })

            exchanges = data.get("exchanges", [])
            conn.execute("""
                INSERT INTO companies (ticker, cik, name, exchange, sic, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    cik=excluded.cik, name=excluded.name,
                    exchange=excluded.exchange, sic=excluded.sic,
                    last_updated=excluded.last_updated
            """, (
                ticker, cik,
                data.get("name", cik_info["name"]),
                exchanges[0] if exchanges else "",
                data.get("sic", ""),
                datetime.now().isoformat(),
            ))
            conn.commit()

            company_id = conn.execute(
                "SELECT id FROM companies WHERE ticker = ?", (ticker,)
            ).fetchone()["id"]

            conn.executemany("""
                INSERT OR IGNORE INTO filings
                  (company_id, form_type, filed_date, period_of_report,
                   accession_number, description, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                (company_id, f["form_type"], f["filed_date"], f["period_of_report"],
                 f["accession_number"], f["description"], f["url"])
                for f in parsed
            ])
            conn.commit()

        rows = conn.execute("""
            SELECT form_type, filed_date, period_of_report,
                   accession_number, description, url
            FROM filings
            WHERE company_id = ?
            ORDER BY filed_date DESC
            LIMIT 100
        """, (company_id,)).fetchall()

        return (not should_fetch), [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────
# FILING CONTENT FETCHER & EXTRACTOR
# ─────────────────────────────────────────────────────────────────

FILING_MAX_CHARS = 10_000

_SECTION_RE = [
    ("mda",          re.compile(r"management.{0,5}s\s+discussion\s+and\s+analysis", re.I)),
    ("risk_factors",  re.compile(r"risk\s+factors", re.I)),
    ("results",       re.compile(r"results\s+of\s+operations", re.I)),
    ("liquidity",     re.compile(r"liquidity\s+and\s+capital\s+resources", re.I)),
    ("outlook",       re.compile(r"(?:business\s+outlook|guidance)\b", re.I)),
]


def _strip_html_to_text(raw: str) -> str:
    raw = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", raw,
                 flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = _html_lib.unescape(raw)
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _extract_key_sections(text: str, chars_per: int = 2000) -> dict:
    sections: dict = {}
    for name, pattern in _SECTION_RE:
        m = pattern.search(text)
        if m:
            sections[name] = text[m.start(): m.start() + chars_per].strip()
    return sections


def fetch_filing_content(url: str, max_chars: int = FILING_MAX_CHARS) -> tuple:
    """Fetch EDGAR HTML filing → (plain_text[:max_chars], extracted_sections)."""
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=25)
        r.raise_for_status()
        text = _strip_html_to_text(r.text)
        sections = _extract_key_sections(text)
        return text[:max_chars], sections
    except Exception as e:
        return f"[Fetch failed: {e}]", {}


# ─────────────────────────────────────────────────────────────────
# FILING DEEP ANALYSIS — SYSTEM PROMPT & PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────

FILING_SYSTEM_PROMPT = """You are a veteran buy-side equity analyst. Analyze SEC filings and quantitative data to produce an investment signal and price target.

OUTPUT FORMAT — your response MUST begin with this JSON on line 1, nothing before it, no code fences:
{"signal":"HOLD","confidence":"MEDIUM","price_target_low":0,"price_target_base":0,"price_target_high":0,"upside_pct":0.0,"verdict":"one sentence"}

Then on line 2 onwards write your analysis in these sections:
## FILING ANALYSIS VERDICT
## 10-K ANNUAL INSIGHTS
## 10-Q QUARTERLY INSIGHTS
## 8-K MATERIAL EVENTS IMPACT
## PRICE TARGET RATIONALE
## KEY RISKS FROM FILINGS
## FINAL RECOMMENDATION

SIGNAL OPTIONS: STRONG_BUY, BUY, HOLD, AVOID, STRONG_AVOID
PRICE TARGET RULES:
1. price_target_base = weighted blend of DCF Intrinsic Value and Graham Number from the data (DCF weighted higher for asset-light/tech, Graham weighted higher for industrials/financials).
2. price_target_low = price_target_base minus 20% (bear case).
3. price_target_high = price_target_base plus 20% (bull case).
4. upside_pct = (price_target_base - current_price) / current_price * 100.
5. No analyst consensus is provided — derive targets from the quantitative models only.
6. If current price exceeds your price_target_base, signal must be HOLD or AVOID. Price matters."""


def build_filing_analysis_prompt(stock_data: dict, filing_contents: list) -> str:
    p = stock_data.get("price") or 0
    az = stock_data.get("altman_z") or {}
    pf = stock_data.get("piotroski_f") or {}
    rf = stock_data.get("red_flags") or {}

    lines = [
        f"Analyze these EDGAR filings for {stock_data['ticker']} ({stock_data['company_name']}).",
        f"Current price: ${p:.2f}",
        "",
        "QUANTITATIVE SNAPSHOT:",
        f"  P/E TTM: {stock_data.get('pe_ttm') or 'N/A'}x | Fwd P/E: {stock_data.get('pe_fwd') or 'N/A'}x | EV/EBITDA: {stock_data.get('ev_ebitda') or 'N/A'}x",
        f"  Revenue growth: {stock_data.get('rev_growth') or 'N/A'}% | EPS growth: {stock_data.get('eps_growth') or 'N/A'}%",
        f"  Gross margin: {stock_data.get('gross_margin') or 'N/A'}% | Net margin: {stock_data.get('net_margin') or 'N/A'}%",
        f"  FCF: ${stock_data.get('fcf') or 'N/A'} | Total debt: ${stock_data.get('total_debt') or 'N/A'}",
        f"  Altman Z: {az.get('score', 'N/A')} ({az.get('zone', '')}) | Piotroski F: {pf.get('score', 'N/A')}/9 ({pf.get('verdict', '')})",
        f"  DCF Intrinsic Value: ${stock_data.get('dcf_price') or 'N/A'} | Graham Number: ${stock_data.get('graham_number') or 'N/A'} | Avg Quant Fair Value: ${stock_data.get('avg_fair_value') or 'N/A'}",
        f"  Red flags: {rf.get('high', 0)} HIGH, {rf.get('medium', 0)} MEDIUM",
        "",
        "FILING CONTENT FROM EDGAR:",
    ]

    for fc in filing_contents:
        lines += [
            "",
            "=" * 60,
            f"FILING: {fc['type']}  |  Filed: {fc['filed_date']}  |  Period: {fc.get('period') or 'N/A'}",
            "=" * 60,
        ]
        secs = fc.get("sections", {})
        if secs.get("mda"):
            lines += ["\n[MANAGEMENT'S DISCUSSION & ANALYSIS]", secs["mda"][:2000]]
        if secs.get("results"):
            lines += ["\n[RESULTS OF OPERATIONS]", secs["results"][:1500]]
        if secs.get("risk_factors"):
            lines += ["\n[RISK FACTORS]", secs["risk_factors"][:1500]]
        if secs.get("liquidity"):
            lines += ["\n[LIQUIDITY]", secs["liquidity"][:800]]
        if secs.get("outlook"):
            lines += ["\n[OUTLOOK / GUIDANCE]", secs["outlook"][:800]]
        if not secs:
            lines += ["\n[FILING CONTENT]", fc.get("content", "No content available")[:3000]]

    dcf = stock_data.get('dcf_price')
    graham = stock_data.get('graham_number')
    avg_fv = stock_data.get('avg_fair_value')
    price = stock_data.get('price') or 0
    # Warn the model when DCF is unreliable (>2.5× price) so it doesn't anchor to garbage
    dcf_reliable = dcf and price and 0 < dcf < price * 2.5
    dcf_note = "" if dcf_reliable else " ⚠ UNRELIABLE — DCF inflated by non-operating cash flows; ignore for target calculation"
    lines += [
        "",
        "PRICE TARGET ANCHOR (use these to compute your JSON price targets):",
        f"  DCF Intrinsic Value:  ${dcf or 'N/A'}{dcf_note}",
        f"  Graham Number:        ${graham or 'N/A'}",
        f"  Avg Quant Fair Value: ${avg_fv or 'N/A'}  (blend of reliable models only)",
        f"  Current Price:        ${price:.2f}",
        f"  Implied premium/discount vs avg fair value: {round((price - avg_fv) / avg_fv * 100, 1) if avg_fv and price else 'N/A'}%",
        "",
        "Compute price_target_base as a weighted blend of reliable models appropriate for this sector.",
        "If DCF is marked UNRELIABLE, anchor to Graham Number and sector P/B / P/E multiples instead.",
        "Then output the JSON on line 1 and ## sections from line 2.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    ticker: str
    stream: bool = True


@app.get("/api/health")
def health():
    return {"status": "ok", "model": OLLAMA_MODEL}


@app.post("/api/admin/cap-outlier-metrics")
def cap_outlier_metrics():
    """One-time cleanup: null out or cap stored metric values that are clearly data artifacts.
    Fixes blowups from near-zero mktcap / assets / EV across FCF yield, owner earnings,
    rule of 40, sloan accrual, and magic formula EY."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        # FCF yield stored as decimal — cap ±1.0 (±100%), null if beyond
        r1 = conn.execute("UPDATE company_analyses SET fcf_yield = NULL WHERE fcf_yield > 1.0 OR fcf_yield < -1.0").rowcount
        # Owner earnings yield stored as pct — cap ±100
        r2 = conn.execute("UPDATE company_analyses SET owner_earnings_yield = NULL WHERE owner_earnings_yield > 100 OR owner_earnings_yield < -100").rowcount
        # Rule of 40 score — cap at 600 (500% growth + 100% margin), null beyond
        r3 = conn.execute("UPDATE company_analyses SET rule_of_40_score = NULL WHERE rule_of_40_score > 600 OR rule_of_40_score < -300").rowcount
        # Sloan accrual stored as pct — cap ±500
        r4 = conn.execute("UPDATE company_analyses SET sloan_accrual = NULL WHERE sloan_accrual > 500 OR sloan_accrual < -500").rowcount
        # Magic formula EY stored as pct — cap ±100
        r5 = conn.execute("UPDATE company_analyses SET magic_ey = NULL WHERE magic_ey > 100 OR magic_ey < -100").rowcount
        conn.commit()
    return {"fcf_yield_nulled": r1, "oe_yield_nulled": r2, "r40_nulled": r3, "sloan_nulled": r4, "magic_ey_nulled": r5}


@app.post("/api/admin/cap-altman-z")
def cap_altman_z():
    """One-time fix: cap absurd Altman Z scores caused by near-zero liabilities (yfinance null fallback).
    Forward: formula already caps x4 at 30. This cleans up existing stored bad values."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        capped = conn.execute(
            "UPDATE company_analyses SET altman_z_score = 30.0 WHERE altman_z_score > 30"
        ).rowcount
        conn.commit()
    return {"capped": capped}


@app.post("/api/admin/enforce-cap-floor")
def enforce_cap_floor():
    """Remove sub-$50M tickers from company_analyses (preferred shares, shells, micro-caps).
    Only removes tickers where market_cap is known and below threshold.
    Tickers with market_cap IS NULL are left in place (will be filtered in leaderboard query)."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        removed_rows = conn.execute(
            "SELECT ticker FROM company_analyses WHERE market_cap IS NOT NULL AND market_cap < 50000000"
        ).fetchall()
        tickers_removed = [r[0] for r in removed_rows]
        if tickers_removed:
            ph = ",".join("?" * len(tickers_removed))
            conn.execute(f"DELETE FROM company_analyses WHERE ticker IN ({ph})", tickers_removed)
            conn.commit()
    return {"removed": len(tickers_removed), "tickers": tickers_removed[:50]}


@app.post("/api/admin/reopen-premature-closes")
def reopen_premature_closes():
    """Reopen positions that were auto-closed before the 14-day minimum hold.
    These are noise — signal flipped on day 0/1 before the thesis had time to play out."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT id, ticker, entry_date, close_date
            FROM paper_positions
            WHERE status = 'closed'
              AND close_reason = 'signal_flip'
              AND CAST(julianday(close_date) - julianday(entry_date) AS INTEGER) < 14
        """).fetchall()
        ids = [r[0] for r in rows]
        tickers = [r[1] for r in rows]
        if ids:
            ph = ",".join("?" * len(ids))
            conn.execute(f"""
                UPDATE paper_positions
                SET status='open', close_price=NULL, close_date=NULL,
                    close_signal=NULL, close_reason=NULL, realized_return_pct=NULL
                WHERE id IN ({ph})
            """, ids)
            conn.commit()
    return {"reopened": len(ids), "tickers": tickers}


@app.delete("/api/admin/orphaned-positions")
def remove_orphaned_positions():
    """Delete open paper_positions with no matching company_analyses entry (truly orphaned)."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT id, ticker FROM paper_positions
            WHERE status = 'open'
              AND ticker NOT IN (SELECT ticker FROM company_analyses)
        """).fetchall()
        if rows:
            ids = [r[0] for r in rows]
            ph  = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM paper_positions WHERE id IN ({ph})", ids)
            conn.commit()
    return {"removed": len(rows), "tickers": [r[1] for r in rows]}


@app.post("/api/admin/backfill-entry-scores")
def backfill_entry_scores():
    """Backfill null entry_quant_score in paper_positions from current company_analyses scores."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        updated = conn.execute("""
            UPDATE paper_positions
            SET entry_quant_score = (
                SELECT quant_score FROM company_analyses WHERE ticker = paper_positions.ticker
            )
            WHERE entry_quant_score IS NULL
              AND EXISTS (
                SELECT 1 FROM company_analyses
                WHERE ticker = paper_positions.ticker AND quant_score IS NOT NULL
              )
        """).rowcount
        conn.commit()
    return {"updated": updated}


@app.post("/api/admin/clear-xbrl-cache")
def clear_xbrl_cache():
    """Clear the 30-day XBRL cache so all companies re-fetch fresh data from SEC EDGAR on next screen run."""
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
            count = conn.execute("SELECT COUNT(*) FROM xbrl_cache").fetchone()[0]
            conn.execute("DELETE FROM xbrl_cache")
            conn.commit()
        return {"cleared": count, "status": "ok"}
    except Exception as e:
        return {"error": str(e), "status": "error"}




def saved_filings_response(ticker: str):
    """Keep stored metadata usable when an external refresh fails, without refreshing its date."""
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        company = conn.execute("SELECT id,last_updated FROM companies WHERE ticker=?", (ticker,)).fetchone()
        if not company:
            return None
        rows = conn.execute("""SELECT form_type,filed_date,period_of_report,accession_number,description,url
            FROM filings WHERE company_id=? ORDER BY filed_date DESC LIMIT 100""", (company['id'],)).fetchall()
    if not rows:
        return None
    filings_list = [dict(row) for row in rows]
    return {'ticker': ticker, 'cached': True, 'stale': True, 'cache_as_of': company['last_updated'],
            'total': len(filings_list), 'filings': filings_list, 'summary': _build_filing_summary(filings_list),
            'notice': 'SEC refresh failed. Showing saved filing metadata; newer filings may be missing.'}


@app.get("/api/filings/{ticker}")
def get_filings(ticker: str):
    """Fetch and cache SEC EDGAR filing metadata for a ticker."""
    ticker = ticker.upper().strip()
    try:
        cached, filings_list = fetch_edgar_filings(ticker)
    except HTTPException as e:
        stored = saved_filings_response(ticker)
        if stored:
            return stored
        # Return a graceful 200 with error field so the frontend degrades nicely
        if e.status_code == 404:
            return {"ticker": ticker.upper(), "cached": False, "total": 0, "filings": [], "summary": None, "error": e.detail}
        raise
    except Exception as e:
        stored = saved_filings_response(ticker)
        if stored:
            return stored
        return {"ticker": ticker.upper(), "cached": False, "total": 0, "filings": [], "summary": None, "error": str(e)}

    summary = _build_filing_summary(filings_list)
    return {
        "ticker":  ticker.upper(),
        "cached":  cached,
        "total":   len(filings_list),
        "filings": filings_list,
        "summary": summary,
    }


@app.get("/api/companies")
def list_companies():
    """List all companies tracked in the local database."""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT c.ticker, c.name, c.cik, c.exchange, c.last_updated,
                   COUNT(f.id) AS filing_count
            FROM companies c
            LEFT JOIN filings f ON f.company_id = c.id
            GROUP BY c.id
            ORDER BY c.last_updated DESC
        """).fetchall()
    companies = [dict(r) for r in rows]
    return {"companies": companies, "total": len(companies)}


@app.get("/api/data/{ticker}")
def get_stock_data(ticker: str):
    """Fetch and score stock data without AI analysis."""
    try:
        data = gather_stock_data(ticker)
        with sqlite3.connect(DB_PATH, timeout=30) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            report=current_report(db,data)
        # Preserve the original stored cache. Unsupported cached models must not
        # continue presenting a fabricated zero in the current dashboard/API.
        return {**public_values(data), **{s['field']:None for s in report['display_suppressions']}, 'number_evidence':report}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analyze/{ticker}")
async def analyze_stream(ticker: str):
    """Stream AI analysis for a ticker."""
    try:
        data = gather_stock_data(ticker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Fetch EDGAR filing data — silently skip if unavailable
    filings_data = None
    try:
        _, filings_list = fetch_edgar_filings(ticker)
        filings_data = {"summary": _build_filing_summary(filings_list), "filings": filings_list}
    except Exception:
        pass

    prompt = build_prompt(data, filings_data=filings_data)

    async def stream_ollama() -> AsyncGenerator[str, None]:
        try:
            payload = {
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "stream": True,
                "options": {"temperature": 0.1, "num_ctx": 4096}
            }
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                stream=True,
                timeout=180
            )
            resp.raise_for_status()

            for line in resp.iter_lines():
                if line:
                    try:
                        obj = json.loads(line)
                        chunk = obj.get("message", {}).get("content", "")
                        if chunk:
                            yield f"data: {json.dumps({'content': chunk})}\n\n"
                        if obj.get("done"):
                            yield "data: [DONE]\n\n"
                            break
                    except json.JSONDecodeError:
                        continue

        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': 'Ollama not running. Start it with: ollama serve'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        stream_ollama(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/api/ollama/status")
def ollama_status():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", headers=OLLAMA_HEADERS, timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        return {"running": True, "models": models, "target_model": OLLAMA_MODEL,
                "model_available": any(OLLAMA_MODEL in m for m in models)}
    except:
        return {"running": False, "models": [], "model_available": False}


@app.get("/api/filing-deep-analysis/{ticker}")
async def filing_deep_analysis(ticker: str):
    """Fetch actual EDGAR filing content and AI-analyze it for price targets + buy/sell signal."""
    ticker = ticker.upper().strip()

    try:
        stock_data = gather_stock_data(ticker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        _, filings_list = fetch_edgar_filings(ticker)
    except Exception:
        filings_list = []

    if not filings_list:
        raise HTTPException(status_code=404, detail=f"No EDGAR filings found for {ticker}.")

    last_10k   = next((f for f in filings_list if f["form_type"] == "10-K"), None)
    last_10q   = next((f for f in filings_list if f["form_type"] == "10-Q"), None)
    recent_8ks = [f for f in filings_list if f["form_type"] == "8-K"][:3]

    filing_contents: list = []

    if last_10k:
        content, sections = fetch_filing_content(last_10k["url"], max_chars=5000)
        filing_contents.append({
            "type": "10-K (Annual Report)", "filed_date": last_10k["filed_date"],
            "period": last_10k.get("period_of_report"), "content": content,
            "sections": sections, "url": last_10k["url"],
        })

    if last_10q:
        content, sections = fetch_filing_content(last_10q["url"], max_chars=4000)
        filing_contents.append({
            "type": "10-Q (Quarterly Report)", "filed_date": last_10q["filed_date"],
            "period": last_10q.get("period_of_report"), "content": content,
            "sections": sections, "url": last_10q["url"],
        })

    for ek in recent_8ks[:2]:
        content, sections = fetch_filing_content(ek["url"], max_chars=2000)
        filing_contents.append({
            "type": "8-K (Material Event)", "filed_date": ek["filed_date"],
            "period": ek.get("period_of_report"), "content": content,
            "sections": sections, "url": ek["url"],
        })

    if not filing_contents:
        raise HTTPException(status_code=404, detail=f"Could not fetch any filing documents for {ticker}.")

    prompt = build_filing_analysis_prompt(stock_data, filing_contents)

    meta_event = {
        "type": "meta",
        "ticker": ticker,
        "company_name": stock_data.get("company_name"),
        "current_price": stock_data.get("price"),
        "filings_analyzed": [
            {"type": fc["type"], "filed_date": fc["filed_date"],
             "period": fc.get("period"), "url": fc["url"]}
            for fc in filing_contents
        ],
    }

    async def _stream():
        yield f"data: {json.dumps(meta_event)}\n\n"
        try:
            payload = {
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": FILING_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "stream": True,
                "options": {"temperature": 0.1, "num_ctx": 8192},
            }
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, headers=OLLAMA_HEADERS, stream=True, timeout=360)
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    try:
                        obj = json.loads(line)
                        chunk = obj.get("message", {}).get("content", "")
                        if chunk:
                            yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
                        if obj.get("done"):
                            yield "data: [DONE]\n\n"
                            break
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'type': 'error', 'error': 'Ollama not running. Start with: ollama serve'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────
# DASHBOARD — persist + serve analysis results
# ─────────────────────────────────────────────────────────────────

class AnalysisResultPayload(BaseModel):
    ticker: str
    company_name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    current_price: Optional[float] = None
    piotroski_score: Optional[float] = None
    signal: str
    confidence: Optional[float] = None
    price_target_low: Optional[float] = None
    price_target_base: Optional[float] = None
    price_target_high: Optional[float] = None
    upside_pct: Optional[float] = None
    verdict_text: Optional[str] = None
    filings_count: int = 0
    filings_detail: list = []
    altman_z_score: Optional[float] = None
    sloan_accrual: Optional[float] = None
    red_flags_count: Optional[int] = None
    red_flags_high: Optional[int] = None


@app.post("/api/save-analysis")
def save_analysis(payload: AnalysisResultPayload):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO company_analyses
            (ticker, company_name, sector, industry, current_price, piotroski_score,
             signal, confidence, price_target_low, price_target_base, price_target_high,
             upside_pct, verdict_text, filings_count, filings_detail, analyzed_at,
             altman_z_score, sloan_accrual, red_flags_count, red_flags_high)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            payload.ticker.upper(), payload.company_name, payload.sector, payload.industry,
            payload.current_price, payload.piotroski_score,
            payload.signal, payload.confidence,
            payload.price_target_low, payload.price_target_base, payload.price_target_high,
            payload.upside_pct, payload.verdict_text, payload.filings_count,
            json.dumps(payload.filings_detail), datetime.utcnow().isoformat(),
            payload.altman_z_score, payload.sloan_accrual,
            payload.red_flags_count, payload.red_flags_high,
        ))
        conn.commit()
    return {"ok": True}


@app.get("/api/dashboard")
def get_dashboard():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM company_analyses").fetchall()
        refs = identities(conn)

    companies = []
    for r in rows:
        price = r["current_price"]
        target = r["price_target_base"]
        # Always recompute upside from stored targets (ensures consistency)
        if price and target:
            upside = round((target - price) / price * 100, 1)
        else:
            upside = r["upside_pct"]
        # Override signal if it contradicts the computed upside
        signal = r["signal"]
        if upside is not None:
            if upside >= 20 and signal in ("HOLD", "SELL", "AVOID"):
                signal = "BUY"
            elif upside >= 5 and signal in ("SELL", "AVOID"):
                signal = "HOLD"
            elif upside <= -15 and signal == "BUY":
                signal = "HOLD"
            elif upside <= -30 and signal in ("BUY", "HOLD"):
                signal = "SELL"
        try:
            altman = r["altman_z_score"]
        except IndexError:
            altman = None
        try:
            sloan = r["sloan_accrual"]
        except IndexError:
            sloan = None
        try:
            rf_count = r["red_flags_count"]
            rf_high  = r["red_flags_high"]
        except IndexError:
            rf_count = None
            rf_high  = None

        def _col(name):
            try: return r[name]
            except IndexError: return None

        eligibility = identity_eligibility(refs.get(r['ticker']), dict(r), research_now())
        companies.append({
            "instrument_eligibility": eligibility,
            "model_version": None,
            "validation_status": "Legacy analysis; open versioned research list for input validation",
            "ticker": r["ticker"],
            "company_name": r["company_name"],
            "sector": r["sector"],
            "industry": r["industry"],
            "current_price": price,
            "piotroski_score": r["piotroski_score"],
            "signal": signal,
            "confidence": r["confidence"],
            "price_target_low": r["price_target_low"],
            "price_target_base": target,
            "price_target_high": r["price_target_high"],
            "upside_pct": upside,
            "verdict_text": r["verdict_text"],
            "filings_count": r["filings_count"],
            "filings_detail": json.loads(r["filings_detail"] or "[]"),
            "analyzed_at": r["analyzed_at"],
            "altman_z_score": altman,
            "sloan_accrual": sloan,
            "red_flags_count": rf_count,
            "red_flags_high": rf_high,
            "beneish_score":        _col("beneish_score"),
            "reverse_dcf_growth":   _col("reverse_dcf_growth"),
            "magic_ey":             _col("magic_ey"),
            "roic_spread":          _col("roic_spread"),
            "peg_ratio_val":        _col("peg_ratio_val"),
            "owner_earnings_yield": _col("owner_earnings_yield"),
            "rule_of_40_score":     _col("rule_of_40_score"),
            "quant_score":          _col("quant_score"),
            "data_source":          _col("data_source"),
            "fcf_yield":            _col("fcf_yield"),
            "pinned":               _col("pinned") or 0,
        })

    # Sort: quant_score (deterministic) first, then upside as tiebreaker
    companies.sort(key=lambda x: (-(x["quant_score"] or 0), -(x["upside_pct"] or 0)))

    industries: dict[str, int] = {}
    signals: dict[str, int] = {}
    for c in companies:
        ind = c["industry"] or "Unknown"
        industries[ind] = industries.get(ind, 0) + 1
        sig = c["signal"] or "Unknown"
        signals[sig] = signals.get(sig, 0) + 1

    return {"companies": companies, "total": len(companies), "industries": industries, "signals": signals,
            "preview_label": os.environ.get("PREVIEW_LABEL")}


@app.delete("/api/dashboard/{ticker}")
def delete_analysis(ticker: str, force: bool = False):
    t = ticker.upper()
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        if not force:
            row = conn.execute("SELECT pinned FROM company_analyses WHERE ticker = ?", (t,)).fetchone()
            if row and row[0]:
                raise HTTPException(
                    status_code=403,
                    detail=f"{t} is pinned (part of universe screen). It will refresh on next Run Screen."
                )
        conn.execute("DELETE FROM company_analyses WHERE ticker = ?", (t,))
        conn.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────
# BULK ANALYSIS — background job to analyze many tickers at once
# ─────────────────────────────────────────────────────────────────

_bulk_lock = threading.Lock()
_bulk_state: dict = {
    "running": False,
    "tickers": [],
    "total": 0,
    "current_idx": 0,
    "current_ticker": None,
    "done": [],
    "errors": [],
    "cancelled": False,
    "started_at": None,
    "finished_at": None,
}


def _analyze_ticker_and_save(ticker: str) -> dict:
    """Synchronously run filing deep analysis for one ticker and persist to DB."""
    ticker = ticker.upper().strip()

    stock_data = gather_stock_data(ticker)

    _, filings_list = fetch_edgar_filings(ticker)
    if not filings_list:
        raise ValueError(f"No EDGAR filings found for {ticker}")

    last_10k   = next((f for f in filings_list if f["form_type"] == "10-K"), None)
    last_10q   = next((f for f in filings_list if f["form_type"] == "10-Q"), None)
    recent_8ks = [f for f in filings_list if f["form_type"] == "8-K"][:2]

    filing_contents: list = []
    if last_10k:
        content, sections = fetch_filing_content(last_10k["url"], max_chars=5000)
        filing_contents.append({
            "type": "10-K (Annual Report)", "filed_date": last_10k["filed_date"],
            "period": last_10k.get("period_of_report"), "content": content,
            "sections": sections, "url": last_10k["url"],
        })
    if last_10q:
        content, sections = fetch_filing_content(last_10q["url"], max_chars=4000)
        filing_contents.append({
            "type": "10-Q (Quarterly Report)", "filed_date": last_10q["filed_date"],
            "period": last_10q.get("period_of_report"), "content": content,
            "sections": sections, "url": last_10q["url"],
        })
    for ek in recent_8ks:
        content, sections = fetch_filing_content(ek["url"], max_chars=2000)
        filing_contents.append({
            "type": "8-K (Material Event)", "filed_date": ek["filed_date"],
            "period": ek.get("period_of_report"), "content": content,
            "sections": sections, "url": ek["url"],
        })

    if not filing_contents:
        raise ValueError(f"Could not fetch any filing documents for {ticker}")

    prompt = build_filing_analysis_prompt(stock_data, filing_contents)
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": FILING_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, headers=OLLAMA_HEADERS, timeout=360)
    r.raise_for_status()
    full_text = r.json()["message"]["content"]

    first_line = full_text.split("\n")[0].strip()
    verdict = json.loads(first_line)
    if not verdict.get("signal") or verdict.get("price_target_base") is None:
        raise ValueError(f"Invalid verdict JSON for {ticker}: {first_line[:100]}")

    # Always recompute upside_pct from the actual price targets — the model miscalculates this
    current_price = stock_data.get("price") or 0
    if current_price and verdict.get("price_target_base"):
        verdict["upside_pct"] = round(
            (verdict["price_target_base"] - current_price) / current_price * 100, 1
        )

    # Override signal if it contradicts the computed upside strongly
    # (AI sometimes says HOLD for a 100%+ upside stock — trust the math)
    upside = verdict.get("upside_pct") or 0
    ai_signal = verdict.get("signal", "HOLD")
    if upside >= 20 and ai_signal in ("HOLD", "SELL", "AVOID"):
        verdict["signal"] = "BUY"
    elif upside >= 5 and ai_signal in ("SELL", "AVOID"):
        verdict["signal"] = "HOLD"
    elif upside <= -15 and ai_signal == "BUY":
        verdict["signal"] = "HOLD"
    elif upside <= -30 and ai_signal in ("BUY", "HOLD"):
        verdict["signal"] = "SELL"

    narrative = full_text[full_text.index("\n") + 1:].strip()[:500] if "\n" in full_text else ""
    filings_analyzed = [
        {"type": fc["type"], "filed_date": fc["filed_date"], "period": fc.get("period"), "url": fc["url"]}
        for fc in filing_contents
    ]
    conf_raw = verdict.get("confidence")
    confidence = None
    if isinstance(conf_raw, (int, float)):
        confidence = float(conf_raw)
    elif isinstance(conf_raw, str):
        confidence = {"HIGH": 0.85, "MEDIUM": 0.60, "LOW": 0.30}.get(conf_raw.upper())

    rf = stock_data.get("red_flags") or {}
    # Extract advanced framework values
    bm_data  = stock_data.get("beneish_m") or {}
    rdcf     = stock_data.get("reverse_dcf") or {}
    mf_data  = stock_data.get("magic_formula") or {}
    rw_data  = stock_data.get("roic_wacc") or {}
    peg_data = stock_data.get("peg_ratio") or {}
    oe_data  = stock_data.get("owner_earnings") or {}
    r40_data = stock_data.get("rule_of_40") or {}
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO company_analyses
            (ticker, company_name, sector, industry, current_price, piotroski_score,
             signal, confidence, price_target_low, price_target_base, price_target_high,
             upside_pct, verdict_text, filings_count, filings_detail, analyzed_at,
             altman_z_score, sloan_accrual, red_flags_count, red_flags_high,
             beneish_score, reverse_dcf_growth, magic_ey, roic_spread,
             peg_ratio_val, owner_earnings_yield, rule_of_40_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ticker,
            stock_data.get("company_name"),
            stock_data.get("sector"),
            stock_data.get("industry"),
            stock_data.get("price"),
            (stock_data.get("piotroski_f") or {}).get("score"),
            verdict["signal"],
            confidence,
            verdict.get("price_target_low"),
            verdict.get("price_target_base"),
            verdict.get("price_target_high"),
            verdict.get("upside_pct"),
            verdict.get("verdict") or narrative[:500],
            len(filings_analyzed),
            json.dumps(filings_analyzed),
            datetime.utcnow().isoformat(),
            (stock_data.get("altman_z") or {}).get("score"),
            (stock_data.get("sloan") or {}).get("ratio"),
            rf.get("total"),
            rf.get("high"),
            bm_data.get("score"),
            rdcf.get("implied_growth_rate"),
            mf_data.get("earnings_yield"),
            rw_data.get("spread_scored") if rw_data.get("spread_scored") is not None else rw_data.get("spread"),
            peg_data.get("peg"),
            oe_data.get("owner_earnings_yield"),
            r40_data.get("score"),
        ))
        conn.commit()

    return {
        "ticker": ticker,
        "company_name": stock_data.get("company_name"),
        "signal": verdict["signal"],
        "upside_pct": verdict.get("upside_pct"),
    }


def _run_bulk_analysis():
    try:
        tickers = list(_bulk_state["tickers"])

        # Skip tickers already analyzed in the last 7 days WITH framework data
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ticker FROM company_analyses "
                "WHERE analyzed_at >= date('now', '-7 days') AND beneish_score IS NOT NULL"
            ).fetchall()
            conn.close()
            analyzed = {r["ticker"].upper() for r in rows}
        except Exception:
            analyzed = set()  # if DB query fails, just analyze everything

        skipped = [t for t in tickers if t.upper() in analyzed]
        pending = [t for t in tickers if t.upper() not in analyzed]

        with _bulk_lock:
            _bulk_state["done"].extend([
                {"ticker": t, "company_name": t, "signal": "cached", "upside_pct": None}
                for t in skipped
            ])
            _bulk_state["total"] = len(pending) + len(skipped)

        for idx, ticker in enumerate(pending):
            with _bulk_lock:
                if _bulk_state["cancelled"]:
                    break
                _bulk_state["current_idx"] = idx
                _bulk_state["current_ticker"] = ticker
            try:
                result = _analyze_ticker_and_save(ticker)
                with _bulk_lock:
                    _bulk_state["done"].append(result)
            except Exception as e:
                with _bulk_lock:
                    _bulk_state["errors"].append({"ticker": ticker, "error": str(e)})

    except Exception as e:
        with _bulk_lock:
            _bulk_state["errors"].append({"ticker": "__bulk__", "error": str(e)})
    finally:
        with _bulk_lock:
            _bulk_state["running"] = False
            _bulk_state["current_ticker"] = None
            _bulk_state["finished_at"] = datetime.utcnow().isoformat()


class BulkAnalyzeRequest(BaseModel):
    tickers: list[str]


@app.post("/api/bulk-analyze")
def start_bulk_analyze(req: BulkAnalyzeRequest):
    with _bulk_lock:
        if _bulk_state["running"]:
            raise HTTPException(status_code=409, detail="Bulk analysis already running.")
        tickers = [t.upper().strip() for t in req.tickers if t.strip()]
        _bulk_state.update({
            "running": True,
            "tickers": tickers,
            "total": len(tickers),
            "current_idx": 0,
            "current_ticker": None,
            "done": [],
            "errors": [],
            "cancelled": False,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        })
    threading.Thread(target=_run_bulk_analysis, daemon=True).start()
    return {"ok": True, "total": len(tickers)}


@app.get("/api/bulk-status")
def get_bulk_status():
    with _bulk_lock:
        return dict(_bulk_state)


@app.delete("/api/bulk-analyze")
def cancel_bulk_analyze():
    with _bulk_lock:
        _bulk_state["cancelled"] = True
    return {"ok": True}


_prewarm_state: dict = {"running": False, "done": 0, "total": 0, "failed": 0}
_prewarm_lock = threading.Lock()

class PrewarmRequest(BaseModel):
    tickers: list[str]

@app.post("/api/prewarm-cache")
def prewarm_cache(req: PrewarmRequest):
    """Fast cache pre-warmer: runs yfinance-only fetches (no AI) in parallel threads.
    Skips tickers already in cache with _fw_version. Use to rebuild cache after purge."""
    with _prewarm_lock:
        if _prewarm_state["running"]:
            raise HTTPException(status_code=409, detail="Pre-warm already running.")
        tickers = [t.upper().strip() for t in req.tickers if t.strip()]
        _prewarm_state.update({"running": True, "done": 0, "total": len(tickers), "failed": 0})

    def _run():
        import time as _time2

        for t in tickers:
            with _prewarm_lock:
                if not _prewarm_state["running"]:
                    break  # cancelled
            try:
                # Skip if already cached with current fw_version
                existing = _get_cached_market_data(t, allow_stale=False)
                if existing and existing.get("_fw_version") == SCHEMA_VERSION:
                    with _prewarm_lock:
                        _prewarm_state["done"] += 1
                    continue
                gather_stock_data(t, force_refresh=True, sync_sqlite=True)
                with _prewarm_lock:
                    _prewarm_state["done"] += 1
                _time2.sleep(0.5)  # 500ms gap to respect Yahoo Finance rate limits
            except Exception:
                with _prewarm_lock:
                    _prewarm_state["failed"] += 1

        with _prewarm_lock:
            _prewarm_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "total": len(tickers)}

@app.get("/api/prewarm-status")
def prewarm_status():
    with _prewarm_lock:
        return dict(_prewarm_state)


_patch_state: dict = {"running": False, "done": 0, "total": 0, "failed": 0, "skipped": 0}
_patch_lock = threading.Lock()


@app.post("/api/patch-frameworks")
def patch_frameworks():
    """Backfill framework columns for all company_analyses rows that have null beneish_score.
    Uses cached yfinance data — no Ollama needed. Fast (~0.1s/ticker from cache)."""
    with _patch_lock:
        if _patch_state["running"]:
            raise HTTPException(status_code=409, detail="Patch already running.")
        _patch_state.update({"running": True, "done": 0, "total": 0, "failed": 0, "skipped": 0})

    def _run():
        try:
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                rows = conn.execute(
                    "SELECT ticker FROM company_analyses WHERE beneish_score IS NULL"
                ).fetchall()
            tickers = [r[0] for r in rows]

            with _patch_lock:
                _patch_state["total"] = len(tickers)

            for ticker in tickers:
                with _patch_lock:
                    if not _patch_state["running"]:
                        break
                try:
                    stock_data = gather_stock_data(ticker)
                    if not stock_data:
                        with _patch_lock:
                            _patch_state["skipped"] += 1
                        continue

                    bm_data  = stock_data.get("beneish_m") or {}
                    rdcf     = stock_data.get("reverse_dcf") or {}
                    mf_data  = stock_data.get("magic_formula") or {}
                    rw_data  = stock_data.get("roic_wacc") or {}
                    peg_data = stock_data.get("peg_ratio") or {}
                    oe_data  = stock_data.get("owner_earnings") or {}
                    r40_data = stock_data.get("rule_of_40") or {}

                    beneish  = bm_data.get("score")
                    rdcf_g   = rdcf.get("implied_growth_rate")
                    magic_ey = mf_data.get("earnings_yield")
                    roic_sp  = rw_data.get("spread_scored") if rw_data.get("spread_scored") is not None else rw_data.get("spread")
                    peg      = peg_data.get("peg")
                    oe_yield = oe_data.get("owner_earnings_yield")
                    r40      = r40_data.get("score")

                    if all(v is None for v in (beneish, rdcf_g, magic_ey, roic_sp, peg, oe_yield, r40)):
                        with _patch_lock:
                            _patch_state["skipped"] += 1
                        continue

                    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                        conn.execute("""
                            UPDATE company_analyses
                            SET beneish_score=?, reverse_dcf_growth=?, magic_ey=?,
                                roic_spread=?, peg_ratio_val=?, owner_earnings_yield=?,
                                rule_of_40_score=?
                            WHERE ticker=?
                        """, (beneish, rdcf_g, magic_ey, roic_sp, peg, oe_yield, r40, ticker))

                    with _patch_lock:
                        _patch_state["done"] += 1
                except Exception:
                    with _patch_lock:
                        _patch_state["failed"] += 1
        finally:
            with _patch_lock:
                _patch_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


@app.get("/api/patch-status")
def patch_status():
    with _patch_lock:
        return dict(_patch_state)


# ─────────────────────────────────────────────────────────────────
# FAST SCREENER — EDGAR XBRL + yfinance market data (no AI / no Ollama)
# Run once a month to rank 1,000+ companies from their actual SEC filings.
# ─────────────────────────────────────────────────────────────────

_screen_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "failed": 0,
    "current_ticker": None,
    "started_at": None,
    "finished_at": None,
    "cancelled": False,
    "last_error": None,
}
_screen_lock = threading.Lock()


def _screen_one(ticker: str) -> dict:
    """Screen one ticker: fetch EDGAR XBRL + yfinance market data, score, save to DB."""
    ticker = ticker.upper().strip()

    # 1. Look up CIK
    cik_map  = get_cik_map()
    cik_info = cik_map.get(ticker)
    if not cik_info:
        raise ValueError(f"{ticker}: not found in EDGAR")
    cik = cik_info["cik"]

    # 2. EDGAR XBRL fundamentals (authoritative filed numbers)
    xbrl = fetch_xbrl_fundamentals(cik)
    if not xbrl:
        raise ValueError(f"{ticker}: no XBRL data (foreign/pre-XBRL filer)")

    # 3. yfinance market data (price + ratios — not financials)
    yf_info: dict = {}
    try:
        yf_info = yf.Ticker(ticker).info or {}
    except Exception:
        pass

    # 3b. Exclude entity types where our scoring models are structurally invalid.
    #     Financial companies: Altman Z and Piotroski assume industrial capital structure;
    #     bank "liabilities" are customer deposits, REIT "debt" is property financing —
    #     the coefficients/criteria don't hold, and including them would corrupt the
    #     regression training data with systematically wrong factor signals.
    _yf_sector   = (yf_info.get("sector")   or "").strip()
    _yf_industry = (yf_info.get("industry") or "").strip()
    _yf_name     = (yf_info.get("longName")  or "").strip()
    _FINANCIAL_SECTORS    = {"Financial Services"}
    _FINANCIAL_INDUSTRIES = {"Bank", "Banks", "Insurance", "Mortgage", "Credit Services",
                              "Capital Markets", "Asset Management", "Financial Data",
                              "Insurance—Life", "Insurance—Property", "Thrift & Mortgage",
                              "Savings & Cooperative Banks"}
    _EXCLUDE_NAME_TOKENS  = {" ETF", " Fund", " Trust", " BDC", " LP", " L.P."}

    if _yf_sector in _FINANCIAL_SECTORS:
        raise ValueError(f"{ticker}: excluded — financial sector (models not valid)")
    if any(kw in _yf_industry for kw in _FINANCIAL_INDUSTRIES):
        raise ValueError(f"{ticker}: excluded — financial industry '{_yf_industry}'")
    if "REIT" in _yf_industry or _yf_sector == "Real Estate":
        raise ValueError(f"{ticker}: excluded — REIT (different capital structure)")
    if any(tok in _yf_name for tok in _EXCLUDE_NAME_TOKENS):
        raise ValueError(f"{ticker}: excluded — investment vehicle name '{_yf_name}'")

    # Market cap floor — preferred shares, shells, and micro-caps blow up our scoring models
    _mktcap_check = yf_info.get("marketCap") or 0
    if _mktcap_check < 50_000_000:
        raise ValueError(f"{ticker}: excluded — market cap ${_mktcap_check:,.0f} below $50M floor")

    # 4. Score
    data   = score_from_xbrl_and_market(ticker, xbrl, yf_info)
    signal, price_target, upside, quant_score = calc_quant_signal(data)

    rf       = data.get("red_flags")      or {}
    bm_data  = data.get("beneish_m")      or {}
    rdcf     = data.get("reverse_dcf")    or {}
    mf_data  = data.get("magic_formula")  or {}
    rw_data  = data.get("roic_wacc")      or {}
    peg_data = data.get("peg_ratio")      or {}
    oe_data  = data.get("owner_earnings") or {}
    r40_data = data.get("rule_of_40")     or {}

    pt_low  = round(price_target * 0.80, 2) if price_target else None
    pt_high = round(price_target * 1.20, 2) if price_target else None

    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO company_analyses
            (ticker, company_name, sector, industry, current_price, piotroski_score,
             signal, confidence, price_target_low, price_target_base, price_target_high,
             upside_pct, verdict_text, filings_count, filings_detail, analyzed_at,
             altman_z_score, sloan_accrual, red_flags_count, red_flags_high,
             beneish_score, reverse_dcf_growth, magic_ey, roic_spread,
             peg_ratio_val, owner_earnings_yield, rule_of_40_score,
             quant_score, data_source, pinned, fcf_yield, market_cap)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ticker,
            data.get("company_name"), data.get("sector"), data.get("industry"),
            data.get("price"),
            (data.get("piotroski_f") or {}).get("score"),
            signal, None,
            pt_low, price_target, pt_high,
            upside, None, 0, "[]",
            datetime.utcnow().isoformat(),
            (data.get("altman_z") or {}).get("score"),
            (data.get("sloan")    or {}).get("ratio"),
            rf.get("total"), rf.get("high"),
            bm_data.get("score"),
            rdcf.get("implied_growth_rate"),
            mf_data.get("earnings_yield"),
            rw_data.get("spread_scored") if rw_data.get("spread_scored") is not None else rw_data.get("spread"),
            peg_data.get("peg"),
            oe_data.get("owner_earnings_yield"),
            r40_data.get("score"),
            quant_score, "xbrl", 1,  # pinned=1: universe-screened, never auto-deleted
            data.get("fcf_yield"),
            data.get("market_cap"),
        ))
        conn.commit()

    return {"ticker": ticker, "signal": signal, "quant_score": quant_score, "upside_pct": upside}


def _run_screen_job(tickers: list[str]):
    import time as _t
    try:
        # Pre-warm CIK map so it doesn't count toward per-ticker time
        try:
            get_cik_map()
        except Exception:
            pass

        for i, ticker in enumerate(tickers):
            with _screen_lock:
                if _screen_state["cancelled"]:
                    break
                _screen_state["current_ticker"] = ticker

            try:
                _screen_one(ticker)
                with _screen_lock:
                    _screen_state["done"] += 1
            except Exception as e:
                with _screen_lock:
                    _screen_state["failed"] += 1
                    _screen_state["last_error"] = f"{ticker}: {str(e)[:300]}"

            _t.sleep(0.12)  # ~8 req/s — safely under SEC's 10 req/s limit

    except Exception:
        pass
    finally:
        with _screen_lock:
            _screen_state["running"]        = False
            _screen_state["current_ticker"] = None
            _screen_state["finished_at"]    = datetime.utcnow().isoformat()
        # Open paper positions for any new STRONG_BUY/BUY tickers
        try:
            opened = _open_portfolio_positions()
            with _screen_lock:
                _screen_state["portfolio_opened"] = opened
        except Exception:
            pass


class ScreenRequest(BaseModel):
    tickers: list[str] = []  # empty = use stored universe


# ─────────────────────────────────────────────────────────────────
# UNIVERSE — master ticker list seeded from SEC EDGAR
# ─────────────────────────────────────────────────────────────────

@app.post("/api/universe/seed")
def seed_universe():
    """
    Fetches SEC EDGAR company_tickers_exchange.json and populates the universe table
    with all NYSE, Nasdaq, NYSE MKT (AMEX), NYSE Arca, and Cboe BZX listed companies.
    Uses the same EDGAR infrastructure already powering XBRL data pulls.
    Free, authoritative, includes CIKs for direct filing access.
    """
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch SEC ticker list: {e}")

    fields = data.get("fields", [])
    rows   = data.get("data",   [])
    try:
        cik_i      = fields.index("cik")
        name_i     = fields.index("name")
        ticker_i   = fields.index("ticker")
        exchange_i = fields.index("exchange")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Unexpected SEC response format: {e}")

    # Expanded exchange list: NYSE, Nasdaq, NYSE MKT (AMEX), NYSE Arca, Cboe BZX
    ALLOWED_EXCHANGES = {"NYSE", "Nasdaq", "NYSE MKT", "NYSE Arca", "Cboe BZX"}

    now   = datetime.utcnow().isoformat()
    added = 0
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        for row in rows:
            exchange = row[exchange_i]
            if exchange not in ALLOWED_EXCHANGES:
                continue
            ticker = str(row[ticker_i]).upper().strip()
            # Skip blank, tickers with dots (BRK.B → handled separately), or >5 chars
            if not ticker or "." in ticker or len(ticker) > 5:
                continue
            cik  = str(row[cik_i]).zfill(10)
            name = row[name_i]
            conn.execute(
                "INSERT OR REPLACE INTO universe (ticker, cik, name, exchange, source, added_at) "
                "VALUES (?,?,?,?,?,?)",
                (ticker, cik, name, exchange, "edgar", now),
            )
            added += 1
        conn.commit()

    return {"ok": True, "added": added}


@app.post("/api/universe/purge-financials")
def purge_financial_companies():
    """
    Remove financial companies, REITs, investment vehicles, and shells from company_analyses,
    paper_positions, and universe. These entities have structurally different capital
    structures that make Altman Z and Piotroski invalid — keeping them corrupts the
    regression training data. Call once after initial seed to clean up any that slipped through.
    """
    EXCLUDE_SECTORS    = ("Financial Services", "Real Estate")
    EXCLUDE_INDUSTRIES = ("Bank", "Banks", "Insurance", "Mortgage", "Credit Services",
                          "Capital Markets", "Asset Management", "REIT", "Thrift")
    EXCLUDE_NAME_TOKENS = (" ETF", " Fund", " Trust", " BDC", " LP", " L.P.")

    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        # Find contaminated tickers
        all_ca = conn.execute(
            "SELECT ticker, sector, industry, company_name FROM company_analyses"
        ).fetchall()

        to_purge = []
        for ticker, sector, industry, name in all_ca:
            sector   = sector   or ""
            industry = industry or ""
            name     = name     or ""
            if sector in EXCLUDE_SECTORS:
                to_purge.append(ticker); continue
            if any(kw in industry for kw in EXCLUDE_INDUSTRIES):
                to_purge.append(ticker); continue
            if any(tok in name for tok in EXCLUDE_NAME_TOKENS):
                to_purge.append(ticker); continue

        if not to_purge:
            return {"purged_analyses": 0, "purged_positions": 0, "purged_universe": 0, "tickers": []}

        ph = ",".join("?" * len(to_purge))
        conn.execute(f"DELETE FROM company_analyses WHERE ticker IN ({ph})", to_purge)
        pos_res = conn.execute(
            f"DELETE FROM paper_positions WHERE ticker IN ({ph}) AND status = 'open'", to_purge
        )
        uni_res = conn.execute(
            f"DELETE FROM universe WHERE ticker IN ({ph})", to_purge
        )
        conn.commit()

    return {
        "purged_analyses":  len(to_purge),
        "purged_positions": pos_res.rowcount,
        "purged_universe":  uni_res.rowcount,
        "tickers":          to_purge,
    }


@app.delete("/api/universe/purge-otc-foreign")
def purge_otc_foreign():
    """
    Remove OTC tickers that are almost certainly foreign or ADRs:
    - 5-letter tickers ending in F  = foreign ordinary shares (e.g. AGMRF)
    - Tickers ending in Y           = ADRs (e.g. BDUUY)
    These have no US XBRL filings and fail immediately during screening,
    wasting time without contributing any data.
    """
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        rows = conn.execute("SELECT ticker FROM universe").fetchall()
        # Suffix conventions for non-common-stock securities across all exchanges:
        # 5-letter tickers ending in W = warrants (ADSEW, ACAQW...)
        # 5-letter tickers ending in R = rights  (ACAHR...)
        # 5-letter tickers ending in U = units    (ACAHU...)
        # Any length ending in F       = foreign ordinary shares (OTC)
        # Any length ending in Y       = ADRs (OTC)
        to_remove = [
            r[0] for r in rows
            if (len(r[0]) == 5 and r[0].endswith(("W", "R", "U")))
            or r[0].endswith(("F", "Y"))
        ]
        if not to_remove:
            return {"removed": 0}
        ph = ",".join("?" * len(to_remove))
        conn.execute(f"DELETE FROM universe WHERE ticker IN ({ph})", to_remove)
        conn.commit()
    return {"removed": len(to_remove)}


@app.delete("/api/universe/cleanup")
def cleanup_universe():
    """
    Removes failed analyses from company_analyses (quant_score IS NULL = screener couldn't
    fetch EDGAR data). Also removes those tickers from the universe so they won't be
    re-screened unless they reappear from SEC. Returns counts of what was removed.
    """
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        # Find tickers that failed to produce a quant_score
        failed = [r[0] for r in conn.execute(
            "SELECT ticker FROM company_analyses WHERE quant_score IS NULL"
        ).fetchall()]
        if not failed:
            return {"removed_analyses": 0, "removed_universe": 0, "tickers": []}
        placeholders = ",".join("?" * len(failed))
        conn.execute(f"DELETE FROM company_analyses WHERE ticker IN ({placeholders})", failed)
        result_universe = conn.execute(
            f"DELETE FROM universe WHERE ticker IN ({placeholders})", failed
        )
        conn.commit()
    return {
        "removed_analyses": len(failed),
        "removed_universe": result_universe.rowcount,
        "tickers": failed,
    }


@app.post("/api/universe/seed-expanded")
def seed_universe_expanded():
    """
    Extends the universe using the full SEC EDGAR company_tickers.json (all ~15k SEC filers).
    Adds tickers not already in the universe table — NYSE/Nasdaq already seeded are skipped.
    Captures OTCQX, OTCQB, NYSE American, NYSE Arca and any other SEC-reporting companies.
    The screener's XBRL validation naturally filters out funds, SPACs, and inactive filers.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch SEC full ticker list: {e}")

    now = datetime.utcnow().isoformat()
    added = 0
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        existing = {r[0] for r in conn.execute("SELECT ticker FROM universe").fetchall()}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper().strip()
            # Same quality filters as primary seed
            if not ticker or "." in ticker or len(ticker) > 5:
                continue
            if ticker in existing:
                continue
            cik  = str(entry.get("cik_str", "")).zfill(10)
            name = entry.get("title", "")
            conn.execute(
                "INSERT OR IGNORE INTO universe (ticker, cik, name, exchange, source, added_at) "
                "VALUES (?,?,?,?,?,?)",
                (ticker, cik, name, "OTC", "edgar_full", now),
            )
            added += 1
        conn.commit()

    return {"ok": True, "added": added, "total_in_universe": len(existing) + added}


@app.get("/api/universe")
def get_universe_stats():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        total = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
        exchanges = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT exchange, COUNT(*) FROM universe GROUP BY exchange"
            ).fetchall()
        }
    return {"total": total, "exchanges": exchanges}


@app.post("/api/screen/run")
def run_screen(req: ScreenRequest):
    """
    Start the fast batch screener (no AI/Ollama required).
    If tickers list is empty, uses the stored universe table (seeded from SEC EDGAR).
    Fetches EDGAR XBRL data for each ticker, runs all quantitative scoring models,
    and saves ranked results to company_analyses (data_source='xbrl', pinned=1).
    Designed to be run once a month.
    """
    with _screen_lock:
        if _screen_state["running"]:
            raise HTTPException(status_code=409, detail="Screen already running.")

        tickers = [t.upper().strip() for t in req.tickers if t.strip()]

        # If no tickers provided, pull from stored universe table
        if not tickers:
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                rows = conn.execute("SELECT ticker FROM universe ORDER BY ticker").fetchall()
                tickers = [r[0] for r in rows]
            if not tickers:
                raise HTTPException(
                    status_code=400,
                    detail="No tickers provided and universe is empty. Call POST /api/universe/seed first."
                )

        _screen_state.update({
            "running": True,
            "total": len(tickers),
            "done": 0,
            "failed": 0,
            "current_ticker": None,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "cancelled": False,
            "last_error": None,
        })
    threading.Thread(target=_run_screen_job, args=(tickers,), daemon=True).start()
    return {"ok": True, "total": len(tickers)}


@app.get("/api/screen/status")
def get_screen_status():
    with _screen_lock:
        return dict(_screen_state)


@app.delete("/api/screen/run")
def cancel_screen():
    with _screen_lock:
        _screen_state["cancelled"] = True
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────
# PAPER PORTFOLIO
# ─────────────────────────────────────────────────────────────────

def _pearson(xs: list, ys: list) -> float | None:
    """Pure-Python Pearson correlation for two same-length lists."""
    import math
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num   = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def _open_portfolio_positions():
    """
    Called after each screener run. Opens $100 paper positions for any
    STRONG_BUY or BUY ticker that doesn't already have an open position.
    Uses current_price from company_analyses as entry price.
    """
    if os.environ.get('ENABLE_LEGACY_PAPER_ALLOCATION') != '1':
        return 0
    today = datetime.utcnow().date().isoformat()
    opened = 0
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
            rows = conn.execute("""
                SELECT ca.ticker, ca.company_name, ca.signal, ca.quant_score,
                       ca.current_price, ca.upside_pct,
                       ca.piotroski_score, ca.altman_z_score, ca.beneish_score,
                       ca.roic_spread, ca.magic_ey, ca.peg_ratio_val,
                       ca.reverse_dcf_growth, ca.fcf_yield
                FROM company_analyses ca
                WHERE ca.signal IN ('BUY', 'STRONG_BUY')
                  AND ca.current_price IS NOT NULL
                  AND ca.current_price > 0
                  AND ca.analyzed_at >= date('now', '-2 days')
            """).fetchall()

            existing = {r[0] for r in conn.execute(
                "SELECT ticker FROM paper_positions WHERE status = 'open'"
            ).fetchall()}

            for (ticker, name, signal, score, price, upside,
                 piotroski, altman_z, beneish, roic_spread,
                 magic_ey, peg, rdcf, fcf_yield) in rows:
                if ticker in existing or instrument_eligibility(name)["status"] == "excluded":
                    continue
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO paper_positions
                        (ticker, status, virtual_dollars, entry_price, entry_date,
                         entry_signal, entry_quant_score, entry_fcf_yield,
                         entry_peg_ratio, entry_upside_pct, entry_piotroski,
                         entry_altman_z, entry_beneish, entry_roic_spread,
                         entry_magic_ey, entry_rdcf_growth)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (ticker, 'open', 100.0, price, today,
                          signal, score, fcf_yield,
                          peg, upside, piotroski,
                          altman_z, beneish, roic_spread,
                          magic_ey, rdcf))
                    opened += 1
                except Exception:
                    pass
            conn.commit()
    except Exception:
        pass
    return opened


@app.post("/api/portfolio/allocate")
def allocate_portfolio():
    """Open $100 positions for all currently analyzed stocks that don't have an open position."""
    opened = _open_portfolio_positions()
    return {"opened": opened}


@app.post("/api/portfolio/reset")
def reset_portfolio():
    """
    Full data-quality reset + rescore:
    1. Fetch fresh Polygon prices for every open-position ticker.
    2. Rescore all positions using cached XBRL + Polygon prices (no EDGAR calls).
    3. Delete all open positions and their snapshots.
    4. Re-open positions using rescored data and corrected prices (entry_date = today).
    """
    today = datetime.utcnow().date().isoformat()

    # ── Step 1: collect all open-position tickers ─────────────────────────────
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM paper_positions WHERE status = 'open'"
        ).fetchall()]

    if not tickers:
        return {"error": "No open positions found."}

    # ── Step 2: fetch fresh Polygon prices ────────────────────────────────────
    prices = _polygon_bulk_prices(tickers)
    if not prices:
        raise HTTPException(status_code=503, detail="Polygon returned no prices — aborting reset.")

    # ── Step 3: rescore using cached XBRL + Polygon prices ────────────────────
    #    Loads EDGAR fundamentals from the 30-day SQLite cache (no live EDGAR calls).
    #    Passes Polygon price as the only market-data input; score_from_xbrl_and_market
    #    derives mktcap/EV from price × shares internally.
    cik_map_data = get_cik_map()
    rescored      = 0
    rescore_errors = 0

    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=120) as conn:
        for ticker in tickers:
            price = prices.get(ticker)
            if not price or price <= 0:
                continue
            try:
                cik_info = cik_map_data.get(ticker.upper())
                if not cik_info:
                    conn.execute(
                        "UPDATE company_analyses SET current_price = ? WHERE ticker = ?",
                        (round(price, 4), ticker),
                    )
                    continue
                cik = cik_info["cik"]

                xbrl_row = conn.execute(
                    "SELECT data_json FROM xbrl_cache WHERE cik = ?", (cik,)
                ).fetchone()
                if not xbrl_row:
                    # No cached XBRL — just update price
                    conn.execute(
                        "UPDATE company_analyses SET current_price = ? WHERE ticker = ?",
                        (round(price, 4), ticker),
                    )
                    continue

                xbrl = json.loads(xbrl_row[0])
                data = score_from_xbrl_and_market(ticker, xbrl, {"currentPrice": price})
                signal, price_target, upside, quant_score = calc_quant_signal(data)

                rf       = data.get("red_flags")      or {}
                bm_data  = data.get("beneish_m")      or {}
                rdcf_d   = data.get("reverse_dcf")    or {}
                mf_data  = data.get("magic_formula")  or {}
                rw_data  = data.get("roic_wacc")      or {}
                peg_data = data.get("peg_ratio")      or {}
                oe_data  = data.get("owner_earnings") or {}
                r40_data = data.get("rule_of_40")     or {}
                pt_low   = round(price_target * 0.80, 2) if price_target else None
                pt_high  = round(price_target * 1.20, 2) if price_target else None

                conn.execute("""
                    UPDATE company_analyses SET
                        current_price        = ?,
                        piotroski_score      = ?,
                        signal               = ?,
                        price_target_low     = ?,
                        price_target_base    = ?,
                        price_target_high    = ?,
                        upside_pct           = ?,
                        analyzed_at          = ?,
                        altman_z_score       = ?,
                        sloan_accrual        = ?,
                        red_flags_count      = ?,
                        red_flags_high       = ?,
                        beneish_score        = ?,
                        reverse_dcf_growth   = ?,
                        magic_ey             = ?,
                        roic_spread          = ?,
                        peg_ratio_val        = ?,
                        owner_earnings_yield = ?,
                        rule_of_40_score     = ?,
                        quant_score          = ?,
                        fcf_yield            = ?,
                        market_cap           = ?
                    WHERE ticker = ?
                """, (
                    round(price, 4),
                    (data.get("piotroski_f") or {}).get("score"),
                    signal,
                    pt_low, price_target, pt_high,
                    upside,
                    datetime.utcnow().isoformat(),
                    (data.get("altman_z") or {}).get("score"),
                    (data.get("sloan")    or {}).get("ratio"),
                    rf.get("total"), rf.get("high"),
                    bm_data.get("score"),
                    rdcf_d.get("implied_growth_rate"),
                    mf_data.get("earnings_yield"),
                    rw_data.get("spread_scored") if rw_data.get("spread_scored") is not None else rw_data.get("spread"),
                    peg_data.get("peg"),
                    oe_data.get("owner_earnings_yield"),
                    r40_data.get("score"),
                    quant_score,
                    data.get("fcf_yield"),
                    data.get("market_cap"),
                    ticker,
                ))
                rescored += 1
            except Exception:
                rescore_errors += 1
        conn.commit()

    # ── Step 4: delete all open positions and their snapshots ──────────────────
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
        conn.execute("""
            DELETE FROM position_snapshots
            WHERE position_id IN (SELECT id FROM paper_positions WHERE status = 'open')
        """)
        conn.execute("DELETE FROM paper_positions WHERE status = 'open'")
        conn.commit()

    # ── Step 5: re-open positions with fresh scores + Polygon prices ──────────
    #    Re-query company_analyses AFTER the rescore so positions carry the new values.
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
        placeholders = ",".join("?" * len(tickers))
        scored_rows = conn.execute(f"""
            SELECT ticker, signal, quant_score, fcf_yield,
                   peg_ratio_val, upside_pct, piotroski_score,
                   altman_z_score, beneish_score, roic_spread,
                   magic_ey, reverse_dcf_growth
            FROM company_analyses
            WHERE ticker IN ({placeholders})
              AND signal IS NOT NULL
        """, tickers).fetchall()

        opened = 0
        for (ticker, signal, score, fcf_yield, peg, upside,
             piotroski, altman_z, beneish, roic_spread, magic_ey, rdcf) in scored_rows:
            price = prices.get(ticker)
            if not price or price <= 0:
                continue
            try:
                conn.execute("""
                    INSERT INTO paper_positions
                    (ticker, status, virtual_dollars, entry_price, entry_date,
                     entry_signal, entry_quant_score, entry_fcf_yield,
                     entry_peg_ratio, entry_upside_pct, entry_piotroski,
                     entry_altman_z, entry_beneish, entry_roic_spread,
                     entry_magic_ey, entry_rdcf_growth)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (ticker, 'open', 100.0, round(price, 4), today,
                      signal, score, fcf_yield, peg, upside,
                      piotroski, altman_z, beneish, roic_spread,
                      magic_ey, rdcf))
                opened += 1
            except Exception:
                pass
        conn.commit()

    return {
        "prices_updated": len(prices),
        "rescored":        rescored,
        "rescore_errors":  rescore_errors,
        "positions_cleared":  len(tickers),
        "positions_reopened": opened,
        "entry_date": today,
    }


@app.post("/api/data/cleanup")
def cleanup_unpriced_tickers():
    """
    Remove every ticker from company_analyses (and its open position, if any)
    that Polygon cannot price today — delisted, OTC-only, symbol changes, etc.
    Also removes their xbrl_cache rows to free storage.
    """
    # 1. All tickers currently in the universe
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
        all_tickers = [r[0] for r in conn.execute(
            "SELECT ticker FROM company_analyses"
        ).fetchall()]

    if not all_tickers:
        return {"error": "No tickers in company_analyses."}

    # 2. Bulk-fetch Polygon prices for every ticker (~18 req/s for 4500 tickers)
    prices = _polygon_bulk_prices(all_tickers)
    priced  = set(prices.keys())
    dropped = [t for t in all_tickers if t not in priced]

    if not dropped:
        return {"total": len(all_tickers), "dropped": 0, "message": "All tickers are priced — nothing to clean."}

    # 3. Delete unpriced tickers from company_analyses, open positions, and xbrl_cache
    cik_map_data = get_cik_map()
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60) as conn:
        # Remove open positions first (FK-safe order)
        conn.execute(f"""
            DELETE FROM position_snapshots
            WHERE position_id IN (
                SELECT id FROM paper_positions
                WHERE status = 'open' AND ticker IN ({','.join('?'*len(dropped))})
            )
        """, dropped)
        conn.execute(f"""
            DELETE FROM paper_positions
            WHERE status = 'open' AND ticker IN ({','.join('?'*len(dropped))})
        """, dropped)
        conn.execute(f"""
            DELETE FROM company_analyses
            WHERE ticker IN ({','.join('?'*len(dropped))})
        """, dropped)

        # Clean xbrl_cache for these CIKs
        ciks_to_drop = [
            cik_map_data[t]["cik"] for t in dropped
            if t in cik_map_data and cik_map_data[t].get("cik")
        ]
        if ciks_to_drop:
            conn.execute(f"""
                DELETE FROM xbrl_cache
                WHERE cik IN ({','.join('?'*len(ciks_to_drop))})
            """, ciks_to_drop)

        conn.commit()

    return {
        "total_before": len(all_tickers),
        "priced":       len(priced),
        "dropped":      len(dropped),
        "remaining":    len(priced),
        "sample_dropped": dropped[:20],
    }


@app.get("/api/portfolio/positions")
def get_positions(status: str = "all"):
    """Return paper positions with live P&L for open ones."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        if status == "open":
            where = "WHERE status = 'open'"
        elif status == "closed":
            where = "WHERE status = 'closed'"
        else:
            where = ""
        rows = conn.execute(f"""
            SELECT id, ticker, status, virtual_dollars,
                   entry_price, entry_date, entry_signal, entry_quant_score,
                   entry_fcf_yield, entry_peg_ratio, entry_upside_pct,
                   entry_piotroski, entry_altman_z, entry_beneish,
                   entry_roic_spread, entry_magic_ey, entry_rdcf_growth,
                   close_price, close_date, close_signal, close_reason,
                   realized_return_pct
            FROM paper_positions {where}
            ORDER BY entry_date DESC
        """).fetchall()

    cols = ["id","ticker","status","virtual_dollars",
            "entry_price","entry_date","entry_signal","entry_quant_score",
            "entry_fcf_yield","entry_peg_ratio","entry_upside_pct",
            "entry_piotroski","entry_altman_z","entry_beneish",
            "entry_roic_spread","entry_magic_ey","entry_rdcf_growth",
            "close_price","close_date","close_signal","close_reason",
            "realized_return_pct"]

    positions = []
    for row in rows:
        p = dict(zip(cols, row))
        # For open positions, get current price from company_analyses
        if p["status"] == "open":
            try:
                with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) as conn2:
                    r = conn2.execute(
                        "SELECT current_price, signal, quant_score FROM company_analyses WHERE ticker = ?",
                        (p["ticker"],)
                    ).fetchone()
                if r and r[0]:
                    cp = r[0]
                    p["current_price"]  = cp
                    p["current_signal"] = r[1]
                    p["current_score"]  = r[2]
                    ep = p["entry_price"]
                    p["return_pct"]     = round((cp - ep) / ep * 100, 2) if ep else None
                    p["unrealized_pnl"] = round((cp - ep) / ep * p["virtual_dollars"], 2) if ep else None
                else:
                    p["current_price"]  = None
                    p["current_signal"] = None
                    p["current_score"]  = None
                    p["return_pct"]     = None
                    p["unrealized_pnl"] = None
            except Exception:
                p["current_price"] = p["current_signal"] = p["current_score"] = None
                p["return_pct"] = p["unrealized_pnl"] = None
        positions.append(p)
    return {"positions": positions, "count": len(positions)}


@app.get("/api/portfolio/sector-breakdown")
def get_sector_breakdown():
    """Sector-level performance stats across all closed positions with sector data."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT p.ticker, p.status, p.entry_price, p.close_price,
                   p.realized_return_pct, ca.sector, ca.current_price
            FROM paper_positions p
            LEFT JOIN company_analyses ca ON p.ticker = ca.ticker
            WHERE ca.sector IS NOT NULL AND ca.sector != 'N/A' AND ca.sector != ''
        """).fetchall()

    from collections import defaultdict
    sectors: dict = defaultdict(list)
    for ticker, status, entry_price, close_price, realized_pct, sector, current_price in rows:
        if status == "closed" and realized_pct is not None:
            ret = realized_pct
        elif status == "open" and entry_price and current_price:
            ret = round((current_price - entry_price) / entry_price * 100, 2)
        else:
            continue
        sectors[sector].append(ret)

    result = []
    for sector, returns in sorted(sectors.items()):
        wins = sum(1 for r in returns if r > 0)
        result.append({
            "sector":     sector,
            "count":      len(returns),
            "win_rate":   round(wins / len(returns) * 100, 1) if returns else 0,
            "avg_return": round(sum(returns) / len(returns), 2) if returns else 0,
            "best":       round(max(returns), 2) if returns else 0,
            "worst":      round(min(returns), 2) if returns else 0,
        })
    result.sort(key=lambda x: x["avg_return"], reverse=True)
    return {"sectors": result}


@app.get("/api/portfolio/summary")
def get_portfolio_summary():
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        open_rows = conn.execute("""
            SELECT p.ticker, p.entry_price, p.virtual_dollars,
                   ca.current_price, ca.signal
            FROM paper_positions p
            LEFT JOIN company_analyses ca ON ca.ticker = p.ticker
            WHERE p.status = 'open'
        """).fetchall()

        closed_rows = conn.execute("""
            SELECT realized_return_pct, entry_signal, virtual_dollars
            FROM paper_positions WHERE status = 'closed'
        """).fetchall()

    total_open = len(open_rows)
    open_deployed = sum(r[2] for r in open_rows)
    open_value = 0.0
    for (_, ep, vd, cp, _) in open_rows:
        if cp and ep and ep > 0:
            open_value += vd * (cp / ep)
        else:
            open_value += vd

    closed_returns  = [r[0] for r in closed_rows if r[0] is not None]
    wins            = [r for r in closed_returns if r > 0]
    losses          = [r for r in closed_returns if r <= 0]
    realized_pnl    = sum(vd * (ret / 100) for ret, _, vd in closed_rows if ret is not None)

    signal_accuracy = {}
    for ret, sig, _ in closed_rows:
        if ret is None or sig is None:
            continue
        if sig not in signal_accuracy:
            signal_accuracy[sig] = {"count": 0, "wins": 0, "avg_return": 0.0, "_sum": 0.0}
        signal_accuracy[sig]["count"] += 1
        signal_accuracy[sig]["_sum"]  += ret
        if ret > 0:
            signal_accuracy[sig]["wins"] += 1
    for sig in signal_accuracy:
        d = signal_accuracy[sig]
        d["avg_return"] = round(d["_sum"] / d["count"], 2)
        d["win_rate"]   = round(d["wins"] / d["count"] * 100, 1)
        del d["_sum"]

    return {
        "integrity": portfolio_integrity(DB_PATH),
        "open_return_pct": round((open_value / open_deployed - 1) * 100, 2) if open_deployed else None,
        "open_positions":    total_open,
        "closed_positions":  len(closed_rows),
        "open_deployed_usd": round(open_deployed, 2),
        "open_value_usd":    round(open_value, 2),
        "open_unrealized_pnl": round(open_value - open_deployed, 2),
        "closed_realized_pnl": round(realized_pnl, 2),
        "win_rate":          round(len(wins) / len(closed_returns) * 100, 1) if closed_returns else None,
        "avg_return_pct":    round(sum(closed_returns) / len(closed_returns), 2) if closed_returns else None,
        "best_return_pct":   round(max(closed_returns), 2) if closed_returns else None,
        "worst_return_pct":  round(min(closed_returns), 2) if closed_returns else None,
        "signal_accuracy":   signal_accuracy,
    }




def _check_edgar_new_filings(log) -> list[str]:
    """
    Step 4a of daily cron: query EDGAR full-text search for 10-K/10-Q filings
    filed in the last 2 days (2-day window catches filings posted late or across
    time zones). Returns tickers in our analyzed universe with new filings.
    """
    start = (datetime.utcnow().date() - timedelta(days=2)).isoformat()
    today = datetime.utcnow().date().isoformat()
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q=%22%22&dateRange=custom&startdt={start}&enddt={today}"
        "&forms=10-K%2C10-Q&hits.hits=500"
    )
    try:
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=20)
        if resp.status_code != 200:
            log.warning(f"EDGAR filing search returned {resp.status_code}")
            return []
        hits = resp.json().get("hits", {}).get("hits", [])
    except Exception as e:
        log.error(f"EDGAR filing search failed: {e}")
        return []

    if not hits:
        return []

    # entity_id in EFTS is the 10-digit zero-padded CIK
    filing_ciks = set()
    for h in hits:
        eid = h.get("_source", {}).get("entity_id", "")
        if eid:
            filing_ciks.add(str(eid).zfill(10))

    if not filing_ciks:
        log.warning("EDGAR hits found but no entity_ids extracted — API format may have changed")
        return []

    ph = ",".join("?" * len(filing_ciks))
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute(
            f"""SELECT c.ticker FROM companies c
                JOIN company_analyses ca ON ca.ticker = c.ticker
                WHERE c.cik IN ({ph})""",
            list(filing_ciks),
        ).fetchall()
    tickers = [r[0] for r in rows]
    log.info(f"EDGAR new filings: {len(hits)} total, {len(tickers)} in our analyzed universe")
    return tickers


def _process_new_filings(tickers: list[str], log):
    """
    Step 4b of daily cron: for tickers with new 10-K/10-Q filings —
    1. Invalidate their XBRL cache so next fetch pulls fresh SEC data
    2. Force-refresh market data + rescore with fresh XBRL
    3. Run AI analysis if Ollama is available (called directly, bypasses bulk skip logic)
    """
    # 1. Invalidate XBRL cache
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        ciks = [r[0] for r in conn.execute(
            f"SELECT cik FROM companies WHERE ticker IN ({','.join('?' * len(tickers))})",
            tickers,
        ).fetchall()]
    if ciks:
        ph = ",".join("?" * len(ciks))
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
            cleared = conn.execute(f"DELETE FROM xbrl_cache WHERE cik IN ({ph})", ciks).rowcount
            conn.commit()
        log.info(f"XBRL cache cleared for {cleared} new-filing tickers")

    # 2. Force re-fetch yfinance + fresh XBRL, then rescore
    rescored = 0
    for ticker in tickers:
        try:
            data = gather_stock_data(ticker, force_refresh=True, sync_sqlite=True)
            if not data:
                continue
            signal, _pt, upside, score = calc_quant_signal(data)
            with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
                conn.execute(
                    "UPDATE company_analyses SET signal=?, quant_score=?, upside_pct=? WHERE ticker=?",
                    (signal, score, round(upside, 1) if upside is not None else None, ticker),
                )
                conn.commit()
            rescored += 1
        except Exception as e:
            log.error(f"New-filing rescore failed for {ticker}: {e}")
    log.info(f"New-filing rescore: {rescored}/{len(tickers)} completed")

    # 3. AI re-analysis if Ollama is up (direct call — bypasses bulk 7-day skip)
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", headers=OLLAMA_HEADERS, timeout=3)
        model_available = any(OLLAMA_MODEL in m.get("name", "") for m in r.json().get("models", []))
    except Exception:
        model_available = False

    if model_available:
        ai_done = 0
        for ticker in tickers:
            try:
                _analyze_ticker_and_save(ticker)
                ai_done += 1
            except Exception as e:
                log.error(f"AI re-analysis failed for {ticker}: {e}")
        log.info(f"AI re-analysis: {ai_done}/{len(tickers)} completed")
    else:
        log.info("Ollama unavailable — skipping AI re-analysis for new filings")


def _daily_auto_update():
    """
    Runs automatically at 4:30 PM ET every weekday (21:30 UTC).
    1. Refreshes current prices for all open positions via yfinance batch download.
    2. Takes daily snapshot — records P&L, auto-closes signal-flipped positions.
    3. Rescores all analyzed stocks using cached market data (background thread).
    4. Checks EDGAR for new 10-K/10-Q filings; processes any found (background thread).
    """
    import logging
    log = logging.getLogger("daily_auto_update")
    log.info("Daily auto-update starting...")

    # Step 1 — refresh prices
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM paper_positions WHERE status = 'open'"
            ).fetchall()
        tickers = [r[0] for r in rows]
        if tickers:
            prices = _polygon_bulk_prices(tickers)

            with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
                for ticker, price in prices.items():
                    if price and price > 0:
                        conn.execute(
                            "UPDATE company_analyses SET current_price = ? WHERE ticker = ?",
                            (round(price, 4), ticker),
                        )
                conn.commit()
            log.info(f"Prices refreshed for {len(prices)} tickers via Polygon")
    except Exception as e:
        log.error(f"Price refresh failed: {e}")

    # Step 2 — daily snapshot
    try:
        today    = datetime.utcnow().date().isoformat()
        snapped  = 0
        auto_closed = 0
        with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
            open_pos = conn.execute("""
                SELECT p.id, p.ticker, p.entry_price, p.entry_date, p.virtual_dollars,
                       ca.current_price, ca.signal, p.entry_signal
                FROM paper_positions p
                LEFT JOIN company_analyses ca ON ca.ticker = p.ticker
                WHERE p.status = 'open'
            """).fetchall()
            for (pos_id, ticker, ep, entry_date, vd, cp, sig, entry_sig) in open_pos:
                if not cp or not ep or ep <= 0:
                    continue
                ret_pct = round((cp - ep) / ep * 100, 2)
                pnl     = round((cp - ep) / ep * vd, 2)
                conn.execute("""
                    INSERT OR IGNORE INTO position_snapshots
                    (position_id, snapshot_date, current_price, current_signal, unrealized_pnl, return_pct)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (pos_id, today, cp, sig, pnl, ret_pct))
                snapped += 1
                age_days = (datetime.utcnow().date() - datetime.fromisoformat(entry_date).date()).days
                # Only close on a genuine signal FLIP (was positive/neutral, now negative).
                # Minimum 14-day hold before signal-flip close — day-1 flips are noise,
                # not a meaningful test of whether the entry score was right.
                signal_flipped = (
                    sig in ("AVOID", "STRONG_AVOID") and
                    entry_sig not in ("AVOID", "STRONG_AVOID")
                )
                if os.environ.get('ENABLE_LEGACY_PAPER_ALLOCATION') == '1' and ((signal_flipped and age_days >= 14) or age_days >= 180):
                    reason = "signal_flip" if signal_flipped else "max_age"
                    conn.execute("""
                        UPDATE paper_positions
                        SET status='closed', close_price=?, close_date=?,
                            close_signal=?, close_reason=?, realized_return_pct=?
                        WHERE id=?
                    """, (cp, today, sig, reason, ret_pct, pos_id))
                    auto_closed += 1
            conn.commit()
        log.info(f"Snapshot complete: {snapped} positions snapped, {auto_closed} auto-closed")
    except Exception as e:
        log.error(f"Snapshot failed: {e}")

    # Step 3 — check EDGAR for new filings and process them (background)
    def _filing_check_and_process():
        try:
            new_tickers = _check_edgar_new_filings(log)
            if new_tickers:
                _process_new_filings(new_tickers, log)
        except Exception as e:
            log.error(f"Filing check/process failed: {e}")

    threading.Thread(target=_filing_check_and_process, daemon=True).start()


@app.post("/api/portfolio/snapshot")
def run_portfolio_snapshot():
    """
    Snapshot current prices + signals for all open positions.
    Auto-closes positions where signal flipped to AVOID/STRONG_AVOID
    or position age exceeds 180 days.
    """
    today = datetime.utcnow().date().isoformat()
    snapped = 0
    auto_closed = 0

    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30) as conn:
        open_pos = conn.execute("""
            SELECT p.id, p.ticker, p.entry_price, p.entry_date, p.virtual_dollars,
                   ca.current_price, ca.signal, p.entry_signal
            FROM paper_positions p
            LEFT JOIN company_analyses ca ON ca.ticker = p.ticker
            WHERE p.status = 'open'
        """).fetchall()

        for (pos_id, ticker, ep, entry_date, vd, cp, sig, entry_sig) in open_pos:
            if not cp or not ep or ep <= 0:
                continue
            ret_pct = round((cp - ep) / ep * 100, 2)
            pnl     = round((cp - ep) / ep * vd, 2)

            conn.execute("""
                INSERT OR IGNORE INTO position_snapshots
                (position_id, snapshot_date, current_price, current_signal, unrealized_pnl, return_pct)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pos_id, today, cp, sig, pnl, ret_pct))
            snapped += 1

            # Only close on a genuine signal FLIP (was positive/neutral, now negative).
            # Minimum 14-day hold before signal-flip close — day-1 flips are noise,
            # not a meaningful test of whether the entry score was right.
            age_days = (datetime.utcnow().date() - datetime.fromisoformat(entry_date).date()).days
            signal_flipped = (
                sig in ("AVOID", "STRONG_AVOID") and
                entry_sig not in ("AVOID", "STRONG_AVOID")
            )
            if os.environ.get('ENABLE_LEGACY_PAPER_ALLOCATION') == '1' and ((signal_flipped and age_days >= 14) or age_days >= 180):
                reason = "signal_flip" if signal_flipped else "max_age"
                conn.execute("""
                    UPDATE paper_positions
                    SET status='closed', close_price=?, close_date=?,
                        close_signal=?, close_reason=?, realized_return_pct=?
                    WHERE id=?
                """, (cp, today, sig, reason, ret_pct, pos_id))
                auto_closed += 1

        conn.commit()

    return {"snapped": snapped, "auto_closed": auto_closed, "date": today}


@app.post("/api/portfolio/refresh-prices")
def refresh_portfolio_prices():
    """Fetch live prices from yfinance for all open-position tickers and update company_analyses."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM paper_positions WHERE status = 'open'"
        ).fetchall()
    tickers = [r[0] for r in rows]
    if not tickers:
        return {"updated": 0}

    try:
        prices = _polygon_bulk_prices(tickers)
        if not prices:
            raise ValueError("Polygon returned no prices")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Price fetch failed: {e}")

    updated = 0
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        for ticker, price in prices.items():
            if price and price > 0:
                conn.execute(
                    "UPDATE company_analyses SET current_price = ? WHERE ticker = ?",
                    (round(price, 4), ticker),
                )
                updated += 1
        conn.commit()

    return {"updated": updated}


@app.get("/api/portfolio/equity-curve")
def get_equity_curve():
    """Recorded position values; variable coverage is not a comparable total return."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT ps.snapshot_date,
                   ROUND(SUM(pp.virtual_dollars + ps.unrealized_pnl), 2) AS total_value,
                   ROUND(SUM(pp.virtual_dollars), 2) AS deployed,
                   COUNT(*) AS positions_count
            FROM position_snapshots ps
            JOIN paper_positions pp ON pp.id = ps.position_id
            GROUP BY ps.snapshot_date
            ORDER BY ps.snapshot_date
        """).fetchall()

    if not rows:
        return {"curve": []}

    curve = [{"date": r[0], "value": r[1], "deployed": r[2], "positions": r[3]} for r in rows]

    # A fixed-initial-dollar SPY line is not comparable to this changing set of
    # position snapshots. Reintroduce a benchmark with cash-flow-aware accounting.
    return {"curve": curve, "performance_status": "unverified",
            "basis": "Sum of recorded position marks; changing coverage, no closed-trade cash ledger.",
            "benchmark_status": "Unavailable until cash flows and corporate actions are reconciled."}


@app.get("/api/portfolio/quintile-analysis")
def get_quintile_analysis():
    """Group all positions (open + closed) by score quintile and compare return distributions."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT p.entry_quant_score, p.entry_price, p.status,
                   p.realized_return_pct, ca.current_price
            FROM paper_positions p
            LEFT JOIN company_analyses ca ON ca.ticker = p.ticker
            WHERE p.entry_quant_score IS NOT NULL
        """).fetchall()

    data = []
    for score, ep, status, realized, cp in rows:
        if status == "closed" and realized is not None:
            ret = realized
        elif status == "open" and cp and ep and ep > 0:
            ret = round((cp - ep) / ep * 100, 2)
        else:
            continue
        data.append({"score": score, "return": ret})

    if len(data) < 5:
        return {"quintiles": [], "total": len(data)}

    data.sort(key=lambda x: x["score"])
    n = len(data)
    quintiles = []
    for i in range(5):
        start = int(i * n / 5)
        end = int((i + 1) * n / 5) if i < 4 else n
        bucket = data[start:end]
        returns = [x["return"] for x in bucket]
        scores  = [x["score"]  for x in bucket]
        quintiles.append({
            "quintile":   i + 1,
            "label":      f"Q{i+1}",
            "score_range": f"{min(scores):.0f}-{max(scores):.0f}",
            "avg_return": round(sum(returns) / len(returns), 2),
            "win_rate":   round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
            "count":      len(bucket),
        })
    return {"quintiles": quintiles, "total": len(data)}


@app.post("/api/portfolio/positions/{pos_id}/close")
def manual_close_position(pos_id: int):
    """Manually close an open position at the current screener price."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        row = conn.execute(
            "SELECT ticker, entry_price, virtual_dollars FROM paper_positions WHERE id=? AND status='open'",
            (pos_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Open position not found")
        ticker, ep, vd = row

        ca = conn.execute(
            "SELECT current_price, signal FROM company_analyses WHERE ticker=?", (ticker,)
        ).fetchone()
        cp  = ca[0] if ca else None
        sig = ca[1] if ca else None
        if not cp:
            raise HTTPException(status_code=422, detail="No current price available")

        ret_pct = round((cp - ep) / ep * 100, 2)
        today   = datetime.utcnow().date().isoformat()
        conn.execute("""
            UPDATE paper_positions
            SET status='closed', close_price=?, close_date=?,
                close_signal=?, close_reason='manual', realized_return_pct=?
            WHERE id=?
        """, (cp, today, sig, ret_pct, pos_id))
        conn.commit()

    return {"closed": True, "ticker": ticker, "return_pct": ret_pct, "close_price": cp}


@app.get("/api/portfolio/history/{ticker}")
def get_position_history(ticker: str):
    """Return all snapshots for all positions in a ticker."""
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        positions = conn.execute(
            "SELECT id, entry_date, entry_signal, entry_price, status, realized_return_pct "
            "FROM paper_positions WHERE ticker=? ORDER BY entry_date DESC",
            (ticker.upper(),)
        ).fetchall()
        result = []
        for pos_id, ed, es, ep, st, rr in positions:
            snaps = conn.execute(
                "SELECT snapshot_date, current_price, current_signal, return_pct "
                "FROM position_snapshots WHERE position_id=? ORDER BY snapshot_date",
                (pos_id,)
            ).fetchall()
            result.append({
                "id": pos_id, "entry_date": ed, "entry_signal": es,
                "entry_price": ep, "status": st, "realized_return_pct": rr,
                "snapshots": [{"date": s[0], "price": s[1], "signal": s[2], "return_pct": s[3]} for s in snaps]
            })
    return {"ticker": ticker.upper(), "positions": result}


MIN_POSITIONS_FOR_LEARNING = 10

@app.post("/api/portfolio/learn")
def run_learning_analysis(min_days: int = 0):
    """
    Factor correlation + OLS regression using ALL positions (open + closed).
    For open positions, current unrealized return is used as the performance signal.
    This means the analysis runs immediately — no waiting for positions to close.
    """
    if min_days < 0 or min_days > 36500:
        raise HTTPException(422, "min_days must be between 0 and 36500.")
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        closed = conn.execute("""
            SELECT realized_return_pct,
                   entry_fcf_yield, entry_peg_ratio, entry_upside_pct,
                   entry_piotroski, entry_altman_z, entry_beneish,
                   entry_roic_spread, entry_magic_ey, entry_rdcf_growth,
                   entry_signal
            FROM paper_positions
            WHERE status = 'closed' AND realized_return_pct IS NOT NULL
              AND julianday(close_date)-julianday(entry_date) >= ?
        """, (min_days,)).fetchall()

        open_ = conn.execute("""
            SELECT p.entry_price, ca.current_price,
                   p.entry_fcf_yield, p.entry_peg_ratio, p.entry_upside_pct,
                   p.entry_piotroski, p.entry_altman_z, p.entry_beneish,
                   p.entry_roic_spread, p.entry_magic_ey, p.entry_rdcf_growth,
                   p.entry_signal
            FROM paper_positions p
            LEFT JOIN company_analyses ca ON ca.ticker = p.ticker
            WHERE p.status = 'open' AND ca.current_price IS NOT NULL AND p.entry_price > 0
              AND julianday('now')-julianday(p.entry_date) >= ?
        """, (min_days,)).fetchall()

    all_rows = []
    for r in closed:
        all_rows.append({"return": r[0], "factors": list(r[1:10]), "signal": r[10]})
    open_count = 0
    for r in open_:
        ep, cp = r[0], r[1]
        if ep and cp and ep > 0:
            all_rows.append({"return": round((cp - ep) / ep * 100, 4), "factors": list(r[2:11]), "signal": r[11]})
            open_count += 1

    if len(all_rows) < MIN_POSITIONS_FOR_LEARNING:
        raise HTTPException(status_code=422,
            detail=f"Need {MIN_POSITIONS_FOR_LEARNING} positions with data. Have {len(all_rows)}.")

    factor_names = ["entry_fcf_yield", "entry_peg_ratio", "entry_upside_pct",
                    "entry_piotroski", "entry_altman_z", "entry_beneish",
                    "entry_roic_spread", "entry_magic_ey", "entry_rdcf_growth"]
    label_map = {
        "entry_fcf_yield":   "FCF Yield",
        "entry_peg_ratio":   "PEG Ratio",
        "entry_upside_pct":  "Upside %",
        "entry_piotroski":   "Piotroski F",
        "entry_altman_z":    "Altman Z",
        "entry_beneish":     "Beneish M",
        "entry_roic_spread": "ROIC Spread",
        "entry_magic_ey":    "Magic EY",
        "entry_rdcf_growth": "Rev-DCF Growth",
    }

    # ── Pearson correlations ─────────────────────────────────────────────────
    correlations = {}
    for i, fname in enumerate(factor_names):
        vals = [(r["return"], r["factors"][i]) for r in all_rows if r["factors"][i] is not None]
        if len(vals) < 5:
            correlations[fname] = {"r": None, "n": len(vals), "label": label_map[fname]}
            continue
        ys, xs = zip(*vals)
        correlations[fname] = {"r": _pearson(list(xs), list(ys)), "n": len(vals), "label": label_map[fname]}

    # ── OLS Regression (standardized so coefficients are comparable weights) ─
    regression: dict = {"_meta": {"r2": None, "n": 0, "intercept": 0}}
    try:
        complete = [r for r in all_rows if all(f is not None for f in r["factors"])]
        if len(complete) >= 20:
            X = np.array([[f for f in r["factors"]] for r in complete], dtype=float)
            y = np.array([r["return"] for r in complete], dtype=float)
            X_mean, X_std = X.mean(axis=0), X.std(axis=0)
            X_std[X_std == 0] = 1.0
            X_norm = (X - X_mean) / X_std
            X_int  = np.column_stack([np.ones(len(y)), X_norm])
            beta, _, _, _ = np.linalg.lstsq(X_int, y, rcond=None)
            y_pred  = X_int @ beta
            ss_res  = float(((y - y_pred) ** 2).sum())
            ss_tot  = float(((y - y.mean()) ** 2).sum())
            r2      = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
            regression["_meta"] = {"r2": round(r2, 4), "n": len(complete), "intercept": round(float(beta[0]), 4)}
            for i, fname in enumerate(factor_names):
                regression[fname] = {"coef": round(float(beta[i + 1]), 4), "label": label_map[fname]}
    except Exception as exc:
        regression["_error"] = str(exc)

    # ── Signal accuracy ──────────────────────────────────────────────────────
    sig_acc: dict = {}
    for row in all_rows:
        sig = row["signal"]
        if not sig:
            continue
        if sig not in sig_acc:
            sig_acc[sig] = {"count": 0, "wins": 0, "_sum": 0.0}
        sig_acc[sig]["count"] += 1
        sig_acc[sig]["_sum"]  += row["return"]
        if row["return"] > 0:
            sig_acc[sig]["wins"] += 1
    for sig in sig_acc:
        d = sig_acc[sig]
        d["avg_return"] = round(d["_sum"] / d["count"], 2)
        d["win_rate"]   = round(d["wins"]  / d["count"] * 100, 1)
        del d["_sum"]

    now = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        conn.execute("""
            INSERT INTO learning_runs
            (run_at, positions_analyzed, min_holding_days, correlation_json, signal_accuracy_json, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (now, len(all_rows), min_days, json.dumps(correlations), json.dumps(sig_acc), json.dumps(regression)))
        conn.commit()
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {
        "run_id":             run_id,
        "run_at":             now,
        "positions_analyzed": len(all_rows),
        "open_included":      open_count,
        "closed_included":    len(closed),
        "correlations":       correlations,
        "regression":         regression,
        "signal_accuracy":    sig_acc,
        "interpretation": (
            "Pearson r: each factor's individual correlation with return. "
            "OLS coefficients describe this sample only; they are not recommended scoring weights. "
            "r² is in-sample fit, not out-of-sample prediction. Returns need corporate-action reconciliation."
        ),
    }


@app.get("/api/portfolio/learn/history")
def get_learning_history():
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15) as conn:
        rows = conn.execute("""
            SELECT id, run_at, positions_analyzed, min_holding_days,
                   correlation_json, signal_accuracy_json, notes
            FROM learning_runs ORDER BY run_at DESC
        """).fetchall()
    return {"runs": [
        {
            "id": r[0], "run_at": r[1], "positions_analyzed": r[2],
            "min_holding_days": r[3],
            "correlations":    json.loads(r[4]),
            "signal_accuracy": json.loads(r[5]),
            "regression":      json.loads(r[6]) if r[6] else None,
        } for r in rows
    ]}


# ─────────────────────────────────────────────────────────────────
# LEADERBOARD / RANKINGS
# ─────────────────────────────────────────────────────────────────

LEADERBOARD_METRICS: dict[str, dict] = {
    "quant_score":      {"label": "Conviction Score",    "col": "quant_score",      "higher_better": True},
    "fcf_yield":        {"label": "FCF Yield",           "col": "fcf_yield",        "higher_better": True},
    "piotroski_score":  {"label": "Piotroski F-Score",   "col": "piotroski_score",  "higher_better": True},
    "altman_z_score":   {"label": "Altman Z-Score",      "col": "altman_z_score",   "higher_better": True},
    "roic_spread":      {"label": "ROIC Spread",         "col": "roic_spread",      "higher_better": True},
    "magic_ey":         {"label": "Magic Formula EY",    "col": "magic_ey",         "higher_better": True},
    "rule_of_40_score": {"label": "Rule of 40",          "col": "rule_of_40_score", "higher_better": True},
    "upside_pct":       {"label": "Upside to Fair Value","col": "upside_pct",       "higher_better": True},
    "owner_earnings_yield": {"label": "Owner Earnings Yield", "col": "owner_earnings_yield", "higher_better": True},
    "peg_ratio_val":    {"label": "PEG Ratio",           "col": "peg_ratio_val",    "higher_better": False},
    "beneish_score":    {"label": "Beneish M-Score",     "col": "beneish_score",    "higher_better": False},
    "sloan_accrual":    {"label": "Sloan Accrual",       "col": "sloan_accrual",    "higher_better": False},
}


def eligible_saved_analyses():
    with sqlite3.connect(DB_PATH, timeout=15) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute('SELECT * FROM company_analyses')]
        refs = identities(db)
    eligible = [row for row in rows if (row.get('market_cap') is None or row['market_cap'] >= 50_000_000)
                and (identity_eligibility(refs[row['ticker']], row, research_now()) if row['ticker'] in refs else
                     instrument_eligibility(row.get('company_name'), sector=row.get('sector'), industry=row.get('industry')))['status'] != 'excluded'
                and (finite(row.get('fcf_yield')) is None or abs(row['fcf_yield']) < 1)]
    return rows, eligible


@app.get('/api/leaderboard')
def get_leaderboard(metric: str = 'quant_score', limit: int = 100):
    if metric not in LEADERBOARD_METRICS or not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail='Unknown metric or limit outside 1–500')
    meta = LEADERBOARD_METRICS[metric]
    all_rows, eligible = eligible_saved_analyses()
    rows = [row for row in eligible if finite(row.get(meta['col'])) is not None]
    rows.sort(key=lambda row: ((-1 if meta['higher_better'] else 1) * row[meta['col']], row['ticker']))
    results = []
    previous_value, rank = None, 0
    for index, row in enumerate(rows[:limit]):
        value = row[meta['col']]
        if value != previous_value:
            rank = index + 1
        previous_value = value
        results.append({**{key: row[key] for key in ('ticker', 'company_name', 'signal', 'quant_score', 'upside_pct', 'current_price')},
                        'rank': rank, 'value': value, 'percentile': round((1 - (rank - 1) / len(rows)) * 100, 1)})
    return {'metric': metric, 'label': meta['label'], 'higher_better': meta['higher_better'],
            'total_universe': len(rows), 'excluded_count': len(all_rows) - len(eligible), 'results': results,
            'eligibility_basis': 'Legacy diagnostic rankings: documented non-common instruments, specialized sectors and FCF yields ≥100% in magnitude excluded. Values remain unversioned; use the versioned research list for source checks.'}


@app.get('/api/rankings/{ticker}')
def get_ticker_rankings(ticker: str):
    ticker = ticker.upper()
    all_rows, eligible = eligible_saved_analyses()
    target = next((row for row in all_rows if row['ticker'] == ticker), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f'{ticker} not found')
    target_eligible = any(row['ticker'] == ticker for row in eligible)
    rankings = {}
    for key, meta in LEADERBOARD_METRICS.items():
        value = finite(target.get(meta['col']))
        values = [finite(row.get(meta['col'])) for row in eligible]
        values = [v for v in values if v is not None]
        rank = None
        if target_eligible and value is not None:
            rank = 1 + sum(v > value if meta['higher_better'] else v < value for v in values)
        rankings[key] = {'value': value, 'rank': rank, 'total': len(values),
                         'percentile': round((1 - (rank - 1) / len(values)) * 100, 1) if rank and values else None,
                         'label': meta['label'], 'higher_better': meta['higher_better']}
    return {'ticker': ticker, 'eligible': target_eligible, 'rankings': rankings}


@app.get("/api/leaderboard/metrics")
def get_leaderboard_metric_list():
    return {"metrics": [
        {"key": k, "label": v["label"], "higher_better": v["higher_better"]}
        for k, v in LEADERBOARD_METRICS.items()
    ]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


@app.get('/api/portfolio/reconciliation')
def get_reconciliation():
    return reconciliation_report(DB_PATH)


@app.get('/api/paper-ledger')
def get_paper_ledger():
    return read_ledger(DB_PATH)


@app.post('/api/paper-ledger/events')
def add_paper_event(event: dict):
    try:
        return append_event(DB_PATH, event)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.post('/api/paper-ledger/import/preview')
def preview_paper_import(body:dict):
    try:return preview_import(DB_PATH,body.get('events'))
    except (ValueError,TypeError,KeyError,OverflowError) as error:
        raise HTTPException(422,str(error)) from None


@app.post('/api/paper-ledger/import/commit')
def commit_paper_import(body:dict):
    try:return commit_import(DB_PATH,body.get('events'),body.get('preview_token'))
    except (ValueError,TypeError,KeyError,OverflowError) as error:
        raise HTTPException(422,str(error)) from None
