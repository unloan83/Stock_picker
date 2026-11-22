"""Weekday website stock names -> existing Stock Finder model -> Telegram -> SQLite.

No YouTube capture and no publisher recommendation is used. The Telegram output
is the existing bot's model verdict format; news sources contribute names only.
"""
import fcntl
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date

import requests

from .data import FreeMarketData
from .engine import analyse_all, now_ist
from .formatter import format_reply
from .mentions import TvLog, shortlist
from .models import StockRequest
from .resolver import SymbolResolver
from .store import RecommendationLog
from .web_picks import collect

log = logging.getLogger(__name__)
MAX_STOCKS = 25
DAILY_TARGET = 20
DELIVERED_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_picks_delivered (
    day TEXT NOT NULL, chat_id INTEGER NOT NULL, part INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY(day, chat_id, part)
)"""



REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_picks_snapshot (
    day TEXT PRIMARY KEY, messages TEXT NOT NULL
)"""


def _saved_report(day: date, db_path: str) -> list[str] | None:
    with sqlite3.connect(db_path) as db:
        db.execute(REPORT_SCHEMA)
        row = db.execute("SELECT messages FROM daily_picks_snapshot WHERE day=?",
                         (day.isoformat(),)).fetchone()
    return json.loads(row[0]) if row else None


def _save_report(day: date, db_path: str, messages: list[str]) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(REPORT_SCHEMA)
        db.execute("INSERT OR IGNORE INTO daily_picks_snapshot (day, messages) VALUES (?, ?)",
                   (day.isoformat(), json.dumps(messages, ensure_ascii=False)))

def _chat_ids() -> list[int]:
    return sorted({int(v) for v in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if v})


@contextmanager
def _single_run(db_path: str):
    """One daily process even when CLI and systemd run at the same time."""
    with open(db_path + ".daily.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _deliver(messages: list[str], day: date, db_path: str) -> None:
    """Retry only unsent Telegram message parts on subsequent runs."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chats = _chat_ids()
    if not token or not chats:
        raise RuntimeError("Telegram daily delivery is not configured")
    with sqlite3.connect(db_path) as db:
        db.execute(DELIVERED_SCHEMA)
    for chat in chats:
        for index, message in enumerate(messages):
            with sqlite3.connect(db_path) as db:
                done = db.execute(
                    "SELECT 1 FROM daily_picks_delivered WHERE day=? AND chat_id=? AND part=?",
                    (day.isoformat(), chat, index),
                ).fetchone()
            if done:
                continue
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": message}, timeout=20,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise RuntimeError("Telegram daily delivery rejected")
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "INSERT OR IGNORE INTO daily_picks_delivered VALUES (?, ?, ?, ?)",
                    (day.isoformat(), chat, index, now_ist().isoformat()),
                )



def format_stock_list(day: date, symbols: list[str]) -> str:
    """Telegram first message: just today's collected NSE symbols."""
    if not symbols:
        raise ValueError("Cannot send an empty stock list")
    return ("📋 Stocks collected · " + day.strftime("%d %b %Y") +
            "\n" + "\n".join(f"{i}. {symbol}" for i, symbol in enumerate(symbols, 1)))


def previous_text(day: date, db_path: str, resolver: SymbolResolver) -> str:
    """On-demand /picks lookup: symbols only, never publisher recommendations."""
    items = shortlist(TvLog(db_path).day(day), resolver.equity_list())
    if not items:
        return "No website-discovered stock names recorded today."
    return "\n".join(item.symbol for item in items[:MAX_STOCKS])


def run(send: bool = True) -> str:
    at = now_ist()
    if at.weekday() >= 5:
        raise RuntimeError("Daily stock discovery is scheduled Monday-Friday only")
    db_path = os.environ.get("STOCK_FINDER_DB", "stock_finder.db")
    resolver = SymbolResolver(os.environ.get("NSE_LIST_CACHE", "nse_equity_list.csv"))
    listing = resolver.equity_list()
    if not listing:
        raise RuntimeError("NSE equity list missing; refusing unverified names")

    with _single_run(db_path):
        # Resume exactly the original message on a delivery retry, not a fresh
        # analysis whose text/part boundaries might have changed.
        if send:
            saved = _saved_report(at.date(), db_path)
            if saved is not None:
                _deliver(saved, at.date(), db_path)
                return "\n\n".join(saved)
        mentions, checked, failed = collect(at.date(), listing)
        if not checked:
            raise RuntimeError("All website discovery feeds failed")
        items = shortlist(mentions, listing)
        symbols = [item.symbol for item in items[:MAX_STOCKS]]
        if len(symbols) < DAILY_TARGET:
            log.warning("Only %d of ~%d requested NSE stock names found", len(symbols), DAILY_TARGET)
        if failed:
            log.warning("%d website feeds were unavailable", len(failed))
        if not symbols:
            raise RuntimeError("No current-day NSE stock names found; no recommendations sent")

        reports = analyse_all(FreeMarketData(), resolver,
                              [StockRequest(symbol) for symbol in symbols], at.date())
        if len(reports) != len(symbols):
            raise RuntimeError("Analysis did not return exactly one report per discovered symbol")
        # Use the same formatter and model verdicts as the interactive Telegram bot.
        messages = [format_stock_list(at.date(), symbols), *format_reply(reports, [], now_ist())]
        if not send:
            return "\n\n".join(messages)

        recommendations = RecommendationLog(db_path)
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        end = at.replace(hour=23, minute=59, second=59, microsecond=999999)
        prior = recommendations.between(start, end, None)
        # The legacy log has no origin field; reserve chat_id=NULL as the daily-job origin.
        already_logged = {r.symbol for r in prior if r.chat_id is None and r.kind == "stock"}
        to_log = [report for report in reports if report.symbol not in already_logged]
        if to_log:
            recommendations.record(at, None, to_log)
        TvLog(db_path).replace_day(at.date(), mentions)
        _save_report(at.date(), db_path, messages)
        _deliver(messages, at.date(), db_path)
        return "\n\n".join(messages)
