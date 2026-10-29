"""SQLite log of every recommendation, so accuracy can be measured later against what the price actually did."""

import sqlite3
from datetime import datetime

from .models import OptionReport, StockReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    chat_id INTEGER,
    item TEXT NOT NULL,
    verdict TEXT NOT NULL,
    long_term_score REAL,
    intraday_score REAL,
    option_score REAL,
    price REAL,
    entry REAL,
    stop REAL,
    target REAL
)
"""


class RecommendationLog:
    def __init__(self, path: str) -> None:
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(SCHEMA)

    def record(self, at: datetime, chat_id: int | None, reports: list[StockReport | OptionReport]) -> None:
        rows = []
        for r in reports:
            if isinstance(r, OptionReport):
                rows.append((at.isoformat(), chat_id, r.label, r.verdict, None, None,
                             r.score.score if r.score else None, r.ltp, None, None, None))
            else:
                plan = r.intraday_plan
                rows.append((at.isoformat(), chat_id, r.symbol, r.verdict,
                             r.long_term.score if r.long_term else None,
                             r.intraday.score if r.intraday else None, None, r.price,
                             plan.entry if plan else None, plan.stop if plan else None,
                             plan.target if plan else None))
        with sqlite3.connect(self.path) as db:
            db.executemany(
                "INSERT INTO recommendations (ts, chat_id, item, verdict, long_term_score, intraday_score, "
                "option_score, price, entry, stop, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
