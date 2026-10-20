"""Preview the original analyzer using a disposable copy of a SQLite snapshot.

Run: python snapshot_preview.py /path/to/consistent-backup.db
The source snapshot is never opened for writing. No synthetic records are added.
"""
import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import uvicorn


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    if not args.snapshot.is_file():
        parser.error("Snapshot must be an existing SQLite file.")
    with tempfile.TemporaryDirectory(prefix="stock-analyzer-snapshot-") as directory:
        destination = Path(directory) / "stocks.db"
        shutil.copyfile(args.snapshot, destination)
        with sqlite3.connect(destination) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot integrity check failed.")
            count = db.execute("SELECT COUNT(*) FROM company_analyses").fetchone()[0]
        os.environ.update(DATA_DIR=directory, API_SECRET="",
                          PREVIEW_LABEL="LOCAL PREVIEW · Copy of saved production data. Changes stay in this temporary copy. Market data may be stale; live trade feeds are not connected.",
                          CORS_ORIGINS="http://127.0.0.1:4317,http://localhost:4317")
        for name in ("QUIVER_API_KEY", "NANSEN_API_KEY", "TRADE_EVIDENCE_DEMO"):
            os.environ.pop(name, None)
        import main
        main.POLYGON_API_KEY = ""
        main.app.router.on_startup.clear()
        main.init_db()
        print(f"Local snapshot preview: {count} saved analyses. No scheduled jobs or provider keys.")
        uvicorn.run(main.app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
