"""Run an isolated, visibly synthetic full-app preview; never use production data.

    python preview.py

Frontend: npm run dev -- --hostname 127.0.0.1 --port 4317
Open http://127.0.0.1:4317/trade-evidence. Backend stays on port 8000.
"""
import os
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path

import uvicorn


def run():
    with tempfile.TemporaryDirectory(prefix="stock-analyzer-demo-") as directory:
        os.environ.update(DATA_DIR=directory, API_SECRET="", TRADE_EVIDENCE_DEMO="1",
                          CORS_ORIGINS="http://127.0.0.1:4317,http://localhost:4317")
        for name in ("QUIVER_API_KEY", "NANSEN_API_KEY"):
            os.environ.pop(name, None)
        import main
        from trade_evidence import init_store, now_utc, save_records
        main.POLYGON_API_KEY = ""
        # Explicitly omit scheduled jobs in a user-visible, synthetic preview.
        main.app.router.on_startup.clear()
        main.init_db()
        path = Path(directory) / "stocks.db"
        init_store(path)
        now = now_utc()
        yesterday = (now-timedelta(days=1)).date().isoformat()
        def congress(name, ticker, days):
            return dict(Representative=name, BioGuideID=name, Ticker=ticker,
                        TransactionDate=(now-timedelta(days=days)).date().isoformat(),
                        ReportDate=yesterday, Transaction="Purchase", Amount="15001",
                        Range="$15,001 - $50,000", TickerType="CS")
        save_records(path, "congress", [congress("Demo · Research candidate", "DEMO", 3),
                     congress("Demo · Late disclosure", "LATE", 40),
                     congress("Demo · Financial risk", "RISK", 3)], now)
        save_records(path, "insiders", [dict(Name="Demo · Stock grant", Ticker="GRANT", Date=yesterday,
                     fileDate=yesterday, TransactionCode="A", Shares=100, PricePerShare=20)], now)
        save_records(path, "crypto", [dict(chain="ethereum", transaction_hash="0x"+"1"*64,
                     trader_address="0x"+"2"*40, trader_address_label="Demo · Unverified wallet",
                     block_timestamp=(now-timedelta(hours=1)).isoformat(), token_sold_symbol="USDC",
                     token_bought_symbol="DEMO", token_bought_address="0x"+"3"*40,
                     token_sold_address="0x"+"4"*40, trade_value_usd=500)], now)
        with sqlite3.connect(path) as db:
            for ticker, flags in [("DEMO", 0), ("LATE", 0), ("RISK", 2)]:
                db.execute("INSERT INTO company_analyses(ticker,quant_score,fcf_yield,red_flags_high,analyzed_at) VALUES(?,?,?,?,?)",
                           (ticker, 75, .06, flags, now.isoformat()))
        print("SYNTHETIC DEMO. Temporary database; no provider keys or scheduled jobs.")
        uvicorn.run(main.app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
