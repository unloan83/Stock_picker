"""SQLite log of every recommendation, so each one can be checked later against what the price actually did."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

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
# Columns added after the first release; created on start-up if an older database lacks them.
EXTRA_COLUMNS = {
    "kind": "TEXT",  # "stock" or "option"
    "symbol": "TEXT",  # stock symbol or option underlying
    "direction": "TEXT",  # intraday plan direction
    "strike": "REAL",
    "option_type": "TEXT",  # CE / PE
    "expiry": "TEXT",  # ISO date
}


@dataclass
class Recommendation:
    id: int
    ts: datetime
    chat_id: int | None
    item: str
    verdict: str
    kind: str
    symbol: str
    price: float | None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    direction: str | None = None
    strike: float | None = None
    option_type: str | None = None
    expiry: date | None = None


class RecommendationLog:
    def __init__(self, path: str) -> None:
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(SCHEMA)
            have = {row[1] for row in db.execute("PRAGMA table_info(recommendations)")}
            for col, typ in EXTRA_COLUMNS.items():
                if col not in have:
                    db.execute(f"ALTER TABLE recommendations ADD COLUMN {col} {typ}")

    def record(self, at: datetime, chat_id: int | None, reports: list[StockReport | OptionReport]) -> None:
        rows = []
        for r in reports:
            if isinstance(r, OptionReport):
                rows.append(dict(
                    ts=at.isoformat(), chat_id=chat_id, item=r.label, verdict=r.verdict,
                    option_score=r.score.score if r.score else None, price=r.ltp, kind="option",
                    symbol=r.underlying, strike=r.strike, option_type=r.kind,
                    expiry=r.expiry.isoformat() if r.expiry else None))
            else:
                plan = r.intraday_plan
                rows.append(dict(
                    ts=at.isoformat(), chat_id=chat_id, item=r.symbol, verdict=r.verdict,
                    long_term_score=r.long_term.score if r.long_term else None,
                    intraday_score=r.intraday.score if r.intraday else None, price=r.price, kind="stock",
                    symbol=r.symbol, entry=plan.entry if plan else None, stop=plan.stop if plan else None,
                    target=plan.target if plan else None, direction=plan.direction if plan else None))
        with sqlite3.connect(self.path) as db:
            for row in rows:
                cols = ", ".join(row)
                marks = ", ".join("?" for _ in row)
                db.execute(f"INSERT INTO recommendations ({cols}) VALUES ({marks})", list(row.values()))

    def between(self, start: datetime, end: datetime, chat_id: int | None = None) -> list[Recommendation]:
        """Recommendations with start <= ts < end (timezone-aware datetimes), oldest first."""
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM recommendations ORDER BY ts, id").fetchall()
        out = []
        for row in rows:
            ts = datetime.fromisoformat(row["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=start.tzinfo)
            if not (start <= ts < end) or (chat_id is not None and row["chat_id"] != chat_id):
                continue
            kind = row["kind"] or ("option" if row["item"].endswith((" CE", " PE")) else "stock")
            symbol = row["symbol"] or row["item"].split()[0]
            out.append(Recommendation(
                id=row["id"], ts=ts, chat_id=row["chat_id"], item=row["item"], verdict=row["verdict"], kind=kind,
                symbol=symbol, price=row["price"], entry=row["entry"], stop=row["stop"], target=row["target"],
                direction=row["direction"], strike=row["strike"], option_type=row["option_type"],
                expiry=date.fromisoformat(row["expiry"]) if row["expiry"] else None))
        return out
