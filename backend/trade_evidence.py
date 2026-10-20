"""Public trade evidence. Provider activity is never presented as proven alpha."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from research_integrity import instrument_eligibility

UTC = timezone.utc
PROVIDERS = {
    "congress": {"name": "Quiver · Congress", "env": "QUIVER_API_KEY",
        "url": "https://api.quiverquant.com/beta/live/congresstrading",
        "docs": "https://www.quiverquant.com/api-setup/",
        "coverage": "Most recent reported trades; not a complete historical ledger."},
    "insiders": {"name": "Quiver · Corporate insiders", "env": "QUIVER_API_KEY",
        "url": "https://api.quiverquant.com/beta/live/insiders",
        "docs": "https://www.quiverquant.com/api-setup/",
        "coverage": "First page, up to 100 recent records. May require a higher API tier."},
    "crypto": {"name": "Nansen · DEX wallets", "env": "NANSEN_API_KEY",
        "url": "https://api.nansen.ai/api/v1/smart-money/dex-trades",
        "docs": "https://docs.nansen.ai/api/smart-money/dex-trades",
        "coverage": "First 100 Ethereum trades from a trailing 24-hour feed; incomplete history."},
}


def now_utc():
    return datetime.now(UTC)


def timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def positive(value):
    val = number(value)
    return val if val is not None and val > 0 else None


def source_time_label(value):
    text = str(value)
    if len(text) == 10:
        return text + " (date only)"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return text + (" (timezone unspecified)" if parsed.tzinfo is None else "")


def normalize(kind: str, raw: dict, observed: datetime) -> dict:
    """Keep raw records, unknowns and separate trade/public/observation clocks."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid record")
    notes = []
    token_address = None
    if kind == "congress":
        actor = raw.get("Representative")
        actor_id = raw.get("BioGuideID") or actor
        asset = raw.get("Ticker")
        trade_at, disclosed = raw.get("TransactionDate"), raw.get("ReportDate")
        side_text = str(raw.get("Transaction", "")).lower()
        side = "buy" if side_text == "purchase" else "sell" if side_text.startswith("sale") else "other"
        amount = positive(raw.get("Amount"))
        amount_label = raw.get("Range") or (f"At least ${amount:,.0f}" if amount else "Unknown range")
        instrument = str(raw.get("TickerType") or "unknown")
        notes += ["Reported amount is a range, not an exact position size.",
                  "Ownership and options details need the original filing.",
                  "Provider returns from transaction date are excluded from copy-performance claims."]
        source = "https://www.quiverquant.com/congresstrading/"
        source_label = "Provider dashboard (not original filing)"
        identity = [actor_id, asset, trade_at, disclosed, side, amount_label,
                    instrument, raw.get("Description")]
    elif kind == "insiders":
        actor = actor_id = raw.get("Name")
        asset = raw.get("Ticker")
        trade_at, disclosed = raw.get("Date"), raw.get("fileDate")
        code = str(raw.get("TransactionCode") or "unknown").upper()
        side = {"P": "buy", "S": "sell"}.get(code, "other")
        shares, price = positive(raw.get("Shares")), positive(raw.get("PricePerShare"))
        amount = shares * price if shares and price else None
        if amount is not None and not math.isfinite(amount):
            raise ValueError("Invalid transaction amount")
        amount_label = f"Reported value ${amount:,.0f}" if amount else "Unknown value"
        instrument = "Form 4 · code " + code
        notes += ["P/S includes open-market or private transactions; inspect the filing.",
                  "10b5-1 status and derivative-table context are not supplied by this feed."]
        if side == "other":
            notes.append("Grant, exercise, tax withholding or other code: not a discretionary purchase signal.")
        source = "https://www.quiverquant.com/insiders/"
        source_label = "Provider dashboard (not original filing)"
        identity = [actor, asset, trade_at, disclosed, code, raw.get("Shares"),
                    raw.get("PricePerShare"), raw.get("SharesOwnedFollowing"),
                    raw.get("directOrIndirectOwnership")]
    elif kind == "crypto":
        actor_id = raw.get("trader_address")
        actor = raw.get("trader_address_label") or actor_id
        asset = f"{raw.get('token_sold_symbol') or '?'} → {raw.get('token_bought_symbol') or '?'}"
        trade_at = disclosed = raw.get("block_timestamp")
        side = "swap"
        amount = positive(raw.get("trade_value_usd"))
        amount_label = f"Swap value ${amount:,.0f}" if amount else "Unknown swap value"
        chain, tx = raw.get("chain"), raw.get("transaction_hash")
        if chain != "ethereum" or not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(tx)):
            raise ValueError("Unsupported chain or transaction")
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", str(actor_id)):
            raise ValueError("Invalid wallet")
        token_address = raw.get("token_bought_address")
        instrument = "ethereum · DEX swap"
        source = "https://etherscan.io/tx/" + quote(tx, safe="")
        source_label = "On-chain transaction"
        notes += ["A swap has two legs; receiving a token does not establish a bullish thesis.",
                  "Wallet label is provider attribution, not a verified person or proven copyable edge.",
                  "Liquidity, token safety, gas and slippage are not yet evaluated."]
        identity = [chain, tx, actor_id, token_address, raw.get("token_sold_address"),
                    raw.get("token_bought_amount"), raw.get("token_sold_amount")]
    else:
        raise ValueError("Unknown provider")
    trade_dt, public_dt = timestamp(trade_at), timestamp(disclosed)
    if not actor or not actor_id or not asset or not trade_dt or not public_dt:
        raise ValueError("Missing identity or dates")
    if public_dt < trade_dt or public_dt > observed or trade_dt > observed:
        raise ValueError("Inconsistent dates")
    if kind != "crypto" and not re.fullmatch(r"[A-Za-z0-9.\-^]{1,16}", str(asset)):
        raise ValueError("Non-equity ticker")
    notes.append("Date-only or timezone-less provider fields are not precise execution timestamps.")
    notes.append("Identical rows are collapsed; corrections without stable provider IDs may remain separate.")
    digest = hashlib.sha256(json.dumps([kind, identity], sort_keys=True).encode()).hexdigest()
    return {"id": digest, "kind": kind, "actor": str(actor), "actor_id": str(actor_id),
            "asset": str(asset).upper() if kind != "crypto" else asset,
            "token_address": token_address, "side": side, "instrument": instrument,
            "amount_usd": amount, "amount_label": amount_label,
            "traded_at": trade_dt.isoformat(), "disclosed_at": public_dt.isoformat(),
            "traded_at_display": source_time_label(trade_at), "disclosed_at_display": source_time_label(disclosed),
            "first_seen_at": observed.isoformat(), "available_at": max(public_dt, observed).isoformat(),
            "disclosure_lag_days": round((public_dt-trade_dt).total_seconds()/86400, 2),
            "source_url": source, "source_label": source_label, "notes": notes}


def init_store(db_path):
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS trade_events (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, event_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, watched INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trade_observations (
            event_id TEXT NOT NULL, raw_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
            raw_json TEXT NOT NULL, PRIMARY KEY(event_id, raw_hash));
        CREATE TABLE IF NOT EXISTS trade_syncs (
            kind TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, succeeded_at TEXT,
            state TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_trade_identity BEFORE UPDATE OF id,kind,event_json,first_seen_at ON trade_events
          BEGIN SELECT RAISE(ABORT,'Trade source evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_trade_observation_update BEFORE UPDATE ON trade_observations
          BEGIN SELECT RAISE(ABORT,'Trade source evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_trade_observation_delete BEFORE DELETE ON trade_observations
          BEGIN SELECT RAISE(ABORT,'Trade source evidence is immutable'); END;
        """)


def save_records(db_path, kind, rows, observed):
    inserted = rejected = 0
    with sqlite3.connect(db_path, timeout=15) as db:
        for raw in rows:
            try:
                event = normalize(kind, raw, observed)
                encoded = json.dumps(raw, sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, OverflowError):
                rejected += 1
                continue
            inserted += db.execute(
                "INSERT OR IGNORE INTO trade_events(id,kind,event_json,first_seen_at) VALUES(?,?,?,?)",
                (event["id"], kind, json.dumps(event), event["first_seen_at"])).rowcount
            db.execute("INSERT OR IGNORE INTO trade_observations VALUES(?,?,?,?)",
                       (event["id"], hashlib.sha256(encoded.encode()).hexdigest(), observed.isoformat(), encoded))
    return inserted, rejected


def assess(event, fundamentals, at):
    """Research triage, deliberately not a return forecast or trading instruction."""
    reasons = ["Trader's net, post-disclosure performance is unverified."]
    age = max(0, (at - timestamp(event["disclosed_at"])).total_seconds()/86400)
    result = {**event, "public_age_days": round(age, 2), "fundamentals": fundamentals,
              "performance_status": "unverified", "priority": "needs_evidence", "reasons": reasons}
    if event["kind"] == "crypto":
        reasons.append("Token liquidity and risk checks are required before considering a copy.")
        return result
    if event["side"] != "buy":
        result["priority"] = "context_only"
        reasons.append("This is not a purchase candidate; a sale does not imply a short trade.")
        return result
    if event["kind"] == "congress" and event["instrument"].upper() not in {"CS", "ST", "STOCK", "COMMON STOCK"}:
        reasons.append("Asset type is not confirmed common stock; inspect options or other instrument details.")
        return result
    if age > 7 or event["disclosure_lag_days"] > 14:
        result["priority"] = "delayed"
        reasons.append("Older than 7 days publicly or reported more than 14 days after trading.")
        return result
    if event.get('source_verification') != 'primary_parsed':
        reasons.append('Original filing or transaction-level source evidence is missing or requires review. Provider rows alone cannot earn a research flag.')
        return result
    if not fundamentals:
        reasons.append("No saved fundamental analysis for this stock. Analyze it in Stock Analyzer.")
        return result
    if fundamentals.get('validation_status') != 'documented_inputs' or not fundamentals.get('currently_fresh'):
        reasons.append('Company inputs are not documented and currently fresh in the versioned research list.')
        return result
    if instrument_eligibility(fundamentals.get('company_name'), sector=fundamentals.get('sector'), industry=fundamentals.get('industry'))['status'] == 'excluded':
        reasons.append('Saved security identity or sector is excluded from this equity model.')
        return result
    analyzed = timestamp(fundamentals.get("analyzed_at"))
    if not analyzed or analyzed > at or (at-analyzed).total_seconds() > 7*86400:
        reasons.append("Saved company analysis is missing a valid recent timestamp or is over 7 days old.")
        return result
    if fundamentals.get("red_flags_high") is None:
        reasons.append("High-severity financial risk count is unknown.")
    elif fundamentals["red_flags_high"] > 0:
        result["priority"] = "risk_flag"
        reasons.append("Existing company analysis reports high-severity financial risks.")
    elif positive(fundamentals.get("fcf_yield")) and (number(fundamentals.get("quant_score")) or 0) >= 53:
        result["priority"] = "research"
        reasons.append("Recent purchase plus positive FCF yield and existing quant score ≥53; investigate further.")
    else:
        reasons.append("Existing fundamentals do not meet the initial research filter.")
    return result


class WatchRequest(BaseModel):
    watched: bool


def make_router(db_path: Path):
    router = APIRouter(prefix="/api/trade-evidence", tags=["Trade evidence"])
    sync_lock = threading.Lock()

    @router.get("")
    def dashboard(kind: str = Query("all", pattern="^(all|congress|insiders|crypto)$"),
                  limit: int = Query(200, ge=1, le=500),
                  ticker: str = Query("", max_length=20, pattern=r"^[A-Za-z0-9.^-]*$"),
                  search:str=Query('',max_length=100),watched:bool=False,offset:int=Query(0,ge=0)):
        init_store(db_path)
        with sqlite3.connect(db_path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            syncs = {r["kind"]: dict(r) for r in db.execute("SELECT * FROM trade_syncs")}
            where = "(?='all' OR kind=?) AND (?='' OR (kind!='crypto' AND UPPER(json_extract(event_json,'$.asset'))=?))"
            escaped=search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            where += " AND (?='' OR (json_extract(event_json,'$.actor') || ' ' || json_extract(event_json,'$.actor_id') || ' ' || json_extract(event_json,'$.asset')) LIKE ? ESCAPE '\\') AND (?=0 OR watched=1)"
            parameters = (kind, kind, ticker.upper(), ticker.upper(),search,'%'+escaped+'%',int(watched))
            rows = db.execute(f"SELECT * FROM trade_events WHERE {where} ORDER BY first_seen_at DESC,id LIMIT ? OFFSET ?",
                              (*parameters, limit,offset)).fetchall()
            fundamentals = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_analyses'").fetchone():
                tickers = list({json.loads(r["event_json"])["asset"] for r in rows if r["kind"] != "crypto"})
                if tickers:
                    placeholders = ",".join("?" for _ in tickers)
                    available = {column[1] for column in db.execute('PRAGMA table_info(company_analyses)')}
                    identity = ''.join(',' + column for column in ('company_name','sector','industry') if column in available)
                    fundamentals = {r["ticker"]: dict(r) for r in db.execute(
                        f"SELECT ticker,quant_score,fcf_yield,red_flags_high,analyzed_at{identity} FROM company_analyses WHERE ticker IN ({placeholders})", tickers)}
            total = db.execute(f"SELECT COUNT(*) FROM trade_events WHERE {where}", parameters).fetchone()[0]
            total_saved=db.execute("SELECT COUNT(*) FROM trade_events WHERE (?='' OR (kind!='crypto' AND UPPER(json_extract(event_json,'$.asset'))=?))",(ticker.upper(),ticker.upper())).fetchone()[0]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='saved_list_items'").fetchone():
                # Join one immutable edition, never the old row's timestamp alone.
                from research_store import currently_fresh, now
                for asset, fundamental in fundamentals.items():
                    item = db.execute('SELECT payload_json FROM saved_list_items WHERE ticker=? AND version_id=(SELECT MAX(id) FROM saved_list_versions)', (asset,)).fetchone()
                    rebuilt = json.loads(item[0]) if item else {}
                    fundamental['validation_status'] = rebuilt.get('status')
                    fundamental['currently_fresh'] = currently_fresh(rebuilt, now())
                    # A quant score from legacy models is never silently certified.
                    fundamental['fcf_yield'] = rebuilt.get('fcf_yield') if fundamental['currently_fresh'] else None
            amendments = []
            primary_history = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ownership_documents'").fetchone():
                amendments = [json.loads(r[0]) for r in db.execute("SELECT metadata_json FROM ownership_documents WHERE json_extract(metadata_json,'$.amendment')=1")]
                latest=db.execute('SELECT state,finished_at,message FROM ownership_history_runs ORDER BY id DESC LIMIT 1').fetchone()
                primary_history={'contact_configured':bool(re.search(r'[^\s]+@[^\s]+',os.environ.get('EDGAR_USER_AGENT',''))),
                                 'documents':db.execute('SELECT COUNT(*) FROM ownership_documents').fetchone()[0],
                                 'transaction_lines':db.execute("SELECT COUNT(*) FROM trade_events WHERE json_extract(event_json,'$.accession') IS NOT NULL").fetchone()[0],
                                 'latest_run':dict(latest) if latest else None,'complete_history':False}
        at = now_utc()
        events = []
        for row in rows:
            event = json.loads(row["event_json"])
            if any(a.get('issuer_cik') == event.get('issuer_cik') and a.get('original_submission_date') == event.get('disclosed_at','')[:10]
                   and {owner['cik'] for owner in a.get('owners',[])}.intersection(owner['cik'] for owner in event.get('owners',[])) for a in amendments):
                event['source_verification'] = 'amendment_unresolved'
                event['notes'] = [*event['notes'], 'An amendment references this submission date; line reconciliation is unresolved.']
            events.append({**assess(event, fundamentals.get(event["asset"]) if event["kind"] != "crypto" else None, at),
                           "watched": bool(row["watched"])})
        return {"as_of": at.isoformat(), "events": events, "total": total,'total_saved':total_saved,'offset':offset,'limit':limit,
                'primary_history':primary_history,
                "demo": os.environ.get("TRADE_EVIDENCE_DEMO") == "1",
                "providers": [{"id": key, "name": p["name"], "configured": bool(os.environ.get(p["env"])),
                    "required_env": p["env"], "docs": p["docs"], "coverage": p["coverage"],
                    "sync": syncs.get(key)} for key, p in PROVIDERS.items()],
                "methodology": "Research triage only. No verified trader rankings or expected-return estimates. Current fundamentals are not historical snapshots. Available-at starts no earlier than first observation."}

    @router.post("/sync/{kind}")
    def sync(kind: str):
        if kind not in PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        provider = PROVIDERS[kind]
        key = os.environ.get(provider["env"])
        if not key:
            raise HTTPException(409, f"Set {provider['env']} on the backend to connect this feed.")
        if not sync_lock.acquire(blocking=False):
            raise HTTPException(409, "A feed sync is already running.")
        try:
            init_store(db_path)
            started = now_utc()
            with sqlite3.connect(db_path, timeout=15) as db:
                prior = db.execute("SELECT attempted_at FROM trade_syncs WHERE kind=?", (kind,)).fetchone()
                if prior and (started-timestamp(prior[0])).total_seconds() < 300:
                    raise HTTPException(429, "Wait five minutes between sync attempts for this feed.")
                db.execute("INSERT INTO trade_syncs(kind,attempted_at,state,message) VALUES(?,?,?,?) ON CONFLICT(kind) DO UPDATE SET attempted_at=excluded.attempted_at,state=excluded.state,message=excluded.message",
                           (kind, started.isoformat(), "syncing", "Fetching bounded recent sample"))
            try:
                if kind == "crypto":
                    response = requests.post(provider["url"], headers={"apikey": key}, timeout=(5, 30),
                        json={"chains": ["ethereum"], "pagination": {"page": 1, "per_page": 100},
                              "order_by": [{"field": "block_timestamp", "direction": "DESC"}]})
                else:
                    params = {"normalized": "true"} if kind == "congress" else {"page": 1, "page_size": 100}
                    response = requests.get(provider["url"], headers={"Authorization": f"Bearer {key}"},
                                            params=params, timeout=(5, 30))
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data") if kind == "crypto" and isinstance(payload, dict) else payload
                if not isinstance(rows, list):
                    raise ValueError("Provider response format changed")
                observed = now_utc()
                inserted, rejected = save_records(db_path, kind, rows[:1000], observed)
                state = "partial" if rejected or len(rows) > 1000 else "ok"
                message = f"{inserted} new records; {rejected} rejected; {min(len(rows),1000)} sampled. {provider['coverage']}"
                with sqlite3.connect(db_path, timeout=15) as db:
                    db.execute("UPDATE trade_syncs SET succeeded_at=?,state=?,message=? WHERE kind=?",
                               (observed.isoformat(), state, message, kind))
                return {"inserted": inserted, "rejected": rejected, "message": message}
            except (requests.RequestException, ValueError, TypeError):
                # Do not expose provider response bodies or credentials to the client.
                message = "Feed sync failed. Check API credentials, plan entitlement, provider availability and schema. Existing records retained."
                with sqlite3.connect(db_path, timeout=15) as db:
                    db.execute("UPDATE trade_syncs SET state='error',message=? WHERE kind=?", (message, kind))
                raise HTTPException(502, message)
        finally:
            sync_lock.release()

    @router.put("/{event_id}/watch")
    def watch(event_id: str, body: WatchRequest):
        init_store(db_path)
        with sqlite3.connect(db_path, timeout=15) as db:
            count = db.execute("UPDATE trade_events SET watched=? WHERE id=?", (int(body.watched), event_id)).rowcount
        if not count:
            raise HTTPException(404, "Trade not found")
        return {"watched": body.watched}

    @router.get("/{event_id}/evidence")
    def evidence(event_id: str):
        init_store(db_path)
        with sqlite3.connect(db_path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT observed_at,raw_json FROM trade_observations WHERE event_id=? ORDER BY observed_at", (event_id,)).fetchall()
        if not rows:
            raise HTTPException(404, "Trade not found")
        return {"observations": [{"observed_at": r["observed_at"], "provider_record": json.loads(r["raw_json"])} for r in rows]}

    return router
