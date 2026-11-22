"""Isolated, one-off full end-to-end mock-live test, including real Telegram if configured.

Reads the latest trading day's web sources, analyses up to 25 names and records
all recommendations in a temporary SQLite file. Never modifies production state.
"""
import csv
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, time
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from stock_finder import daily_picks
from stock_finder.mentions import TvLog
from stock_finder.resolver import SymbolResolver
from stock_finder.store import RecommendationLog

IST = ZoneInfo("Asia/Kolkata")


def main():
    today = datetime.now(IST).date()
    day = today
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    simulated_at = datetime.combine(day, time(9, 25), tzinfo=IST)
    with Path("tests/data/nse_equity_list.csv").open(newline="", encoding="utf-8") as fh:
        listing = {symbol: name for symbol, name in csv.reader(fh)}
    resolver = SymbolResolver(None, list_loader=lambda: listing, searcher=lambda q: [])
    configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("ALLOWED_CHAT_IDS"))
    print(f"MOCK_LIVE_DATE: {day}", flush=True)
    print(f"MOCK_TELEGRAM_CONFIGURED: {configured}", flush=True)

    with tempfile.TemporaryDirectory(prefix="stock-picker-mock-") as root:
        db = os.path.join(root, "mock_only.db")
        with patch.dict(os.environ, {"STOCK_FINDER_DB": db}), \
             patch.object(daily_picks, "now_ist", return_value=simulated_at), \
             patch.object(daily_picks, "SymbolResolver", return_value=resolver):
            # When GitHub secrets are missing this remains a full model preview.
            # Never silently pretend a Telegram message was actually delivered.
            result = daily_picks.run(send=configured)
        print("MOCK_REPORT_LENGTH:", len(result), flush=True)
        print("MOCK_REPORT_START", flush=True)
        print(result[:30000], flush=True)
        print("MOCK_REPORT_END", flush=True)
        if configured:
            rows = RecommendationLog(db).between(
                simulated_at.replace(hour=0, minute=0),
                simulated_at.replace(hour=23, minute=59), None)
            with sqlite3.connect(db) as conn:
                delivered = conn.execute(
                    "SELECT COUNT(*) FROM daily_picks_delivered").fetchone()[0]
            picks = TvLog(db).day(day)
            print("MOCK_RECOMMENDATION_ROWS:", len(rows), flush=True)
            print("MOCK_STOCK_NAMES:", len({p.symbol for p in picks}), flush=True)
            print("MOCK_TELEGRAM_MESSAGES_DELIVERED:", delivered, flush=True)
            if len(rows) == 0 or delivered == 0:
                raise RuntimeError("Missing mock recommendation records or Telegram confirmation")
        else:
            print("MOCK_TELEGRAM_NOT_SENT: No configured GitHub Actions bot token/chat IDs", flush=True)


if __name__ == "__main__":
    main()
