import sqlite3
from datetime import date, datetime, time

import pandas as pd

from stock_finder.bot import next_report_time
from stock_finder.engine import IST
from stock_finder.models import IntradayPlan, OptionReport, Score, StockReport
from stock_finder.performance import (
    first_call_per_day,
    judge_intraday,
    judge_option,
    judge_vs_index,
    todays_list,
    weekly_report,
)
from stock_finder.store import Recommendation, RecommendationLog
from tests.fakes import FakeMarket, chain

MON = date(2026, 10, 26)
SAT_10 = datetime(2026, 10, 31, 10, 0, tzinfo=IST)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


def bars(day: date, rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """rows of (high, low, close), 5 minutes apart from 09:15."""
    idx = pd.date_range(at(day, 9, 15), periods=len(rows), freq="5min")
    return pd.DataFrame({"Open": [r[2] for r in rows], "High": [r[0] for r in rows], "Low": [r[1] for r in rows],
                         "Close": [r[2] for r in rows], "Volume": 1000.0}, index=idx)


def daily(days: list[date], opens: list[float], closes: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=IST) for d in days])
    return pd.DataFrame({"Open": opens, "High": closes, "Low": opens, "Close": closes, "Volume": 1e6}, index=idx)


def rec(**kw) -> Recommendation:
    base = dict(id=1, ts=at(MON, 9, 40), chat_id=1, item="TCS", verdict="INTRADAY LONG", kind="stock",
                symbol="TCS", price=100.0, entry=100.0, stop=99.0, target=102.0, direction="LONG")
    base.update(kw)
    return Recommendation(**base)


# --- intraday ------------------------------------------------------------------------------------------------------

def test_intraday_target_before_stop_is_hit():
    b = bars(MON, [(100, 99.5, 100)] * 6 + [(101, 100, 100.8), (102.1, 100.5, 102), (102, 98, 98.5)])
    o = judge_intraday(rec(), b, SAT_10)
    assert o.hit is True and "target 102.00 hit at 09:50" in o.detail


def test_intraday_stop_first_is_fail_and_short_side_mirrors():
    b = bars(MON, [(100, 99.5, 100)] * 6 + [(100.2, 98.9, 99), (103, 99, 102.5)])
    assert judge_intraday(rec(), b, SAT_10).hit is False
    short = rec(direction="SHORT", stop=101.0, target=98.0)
    b2 = bars(MON, [(100, 99.5, 100)] * 6 + [(100.1, 97.9, 98)])
    o = judge_intraday(short, b2, SAT_10)
    assert o.hit is True and "+2.0%" in o.detail


def test_intraday_neither_level_uses_close_and_ignores_bars_before_the_call():
    b = bars(MON, [(105, 90, 100)] * 5 + [(100.5, 99.5, 100.4)] * 4)  # wild bars before 09:40 are ignored
    o = judge_intraday(rec(), b, SAT_10)
    assert o.hit is True and "closed 100.40" in o.detail


def test_intraday_same_day_before_close_is_pending():
    o = judge_intraday(rec(), bars(MON, [(100, 99.5, 100)] * 8), at(MON, 11, 0))
    assert o.hit is None and "in progress" in o.detail


# --- long term / not recommended ------------------------------------------------------------------------------------

def test_vs_index():
    days = [MON, date(2026, 10, 27), date(2026, 10, 30)]
    stock = daily(days, [100, 101, 103], [101, 102, 105])
    nifty = daily(days, [20000, 20100, 20200], [20100, 20200, 20400])  # +2%
    lt = judge_vs_index(rec(verdict="LONG TERM"), "LONG TERM", stock, nifty)
    assert lt.hit is True and "(+5.0%) vs NIFTY +2.0%" in lt.detail
    nr = judge_vs_index(rec(verdict="NOT RECOMMENDED"), "NOT RECOMMENDED", stock, nifty)
    assert nr.hit is False
    early = judge_vs_index(rec(ts=at(date(2026, 11, 2), 9, 40)), "LONG TERM", stock, nifty)
    assert early.hit is None


# --- options -------------------------------------------------------------------------------------------------------

def test_option_buy_live_and_expired():
    c = chain(spot=25000, expiry=date(2026, 11, 4))  # every CE ltp is 100
    md = FakeMarket(daily={"NIFTY": daily([date(2026, 10, 29)], [25000], [25300])}, chains={"NIFTY": c})
    buy = rec(kind="option", item="NIFTY 25000 CE", verdict="BUY", symbol="NIFTY", price=80.0, strike=25000.0,
              option_type="CE", expiry=date(2026, 11, 4))
    o = judge_option(buy, md, SAT_10)
    assert o.hit is True and "₹80.00 → ₹100.00" in o.detail
    avoid = rec(**{**buy.__dict__, "verdict": "AVOID"})
    assert judge_option(avoid, md, SAT_10).hit is False

    expired = rec(**{**buy.__dict__, "expiry": date(2026, 10, 29), "price": 250.0})
    o = judge_option(expired, md, SAT_10)  # intrinsic 300 at expiry
    assert o.hit is True and "expired" in o.detail and "₹300.00" in o.detail


# --- storage, report, schedule --------------------------------------------------------------------------------------

def test_store_upgrades_old_database_and_round_trips(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as db:  # schema of the first release, with one old row
        db.execute("CREATE TABLE recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                   "chat_id INTEGER, item TEXT NOT NULL, verdict TEXT NOT NULL, long_term_score REAL, "
                   "intraday_score REAL, option_score REAL, price REAL, entry REAL, stop REAL, target REAL)")
        db.execute("INSERT INTO recommendations (ts, chat_id, item, verdict, price) VALUES (?, 1, 'INFY', "
                   "'NOT RECOMMENDED', 1500)", (at(MON, 9, 30).isoformat(),))
    store = RecommendationLog(path)
    store.record(at(MON, 9, 45), 1, [
        StockReport("TCS", 100.0, "INTRADAY LONG", intraday=Score(70),
                    intraday_plan=IntradayPlan("LONG", 100.0, 99.0, 102.0)),
        OptionReport("NIFTY 25000 CE", "BUY", underlying="NIFTY", strike=25000.0, kind="CE",
                     expiry=date(2026, 11, 4), ltp=80.0),
    ])
    recs = store.between(at(MON, 0, 0), SAT_10, chat_id=1)
    assert [(r.item, r.kind, r.symbol) for r in recs] == [
        ("INFY", "stock", "INFY"), ("TCS", "stock", "TCS"), ("NIFTY 25000 CE", "option", "NIFTY")]
    assert recs[1].direction == "LONG" and recs[2].expiry == date(2026, 11, 4) and recs[2].option_type == "CE"
    assert store.between(at(MON, 0, 0), SAT_10, chat_id=2) == []


def test_first_call_per_day():
    a, b, c = rec(id=1), rec(id=2, ts=at(MON, 11, 0)), rec(id=3, ts=at(date(2026, 10, 27), 9, 40))
    assert [r.id for r in first_call_per_day([a, b, c])] == [1, 3]


def test_weekly_report_end_to_end(tmp_path):
    store = RecommendationLog(str(tmp_path / "log.db"))
    store.record(at(MON, 9, 40), 1, [
        StockReport("TCS", 100.0, "LONG TERM + INTRADAY LONG", intraday_plan=IntradayPlan("LONG", 100, 99, 102)),
        StockReport("INFY", 50.0, "NOT RECOMMENDED"),
        StockReport("ZZZ", None, "NO DATA"),
    ])
    days = [MON, date(2026, 10, 30)]
    md = FakeMarket(
        daily={"TCS": daily(days, [100, 103], [101, 105]), "INFY": daily(days, [50, 50], [50, 49]),
               "NIFTY": daily(days, [20000, 20000], [20100, 20200])},
        intraday={"TCS": bars(MON, [(100, 99.5, 100)] * 6 + [(102.5, 100, 102)])},
    )
    text = weekly_report(store, md, SAT_10, chat_id=1)
    assert "Result: 3 hit · 0 fail · hit rate 100%" in text
    assert "Intraday 1/1 · Long term 1/1 · Not recommended 1/1" in text
    assert "Mon 26 Oct" in text and "✅ TCS · INTRADAY LONG: target 102.00 hit" in text
    assert "ZZZ" not in text
    assert "No recommendations" in weekly_report(store, md, SAT_10, chat_id=99)

    listing = todays_list(store, at(MON, 12, 0), chat_id=1)
    assert "TCS @ ₹100.00: LONG TERM + INTRADAY LONG · entry 100.00 stop 99.00 target 102.00" in listing


def test_next_report_time_is_saturday_10_ist():
    assert next_report_time(at(date(2026, 10, 29), 9, 0)) == SAT_10  # Thursday
    assert next_report_time(SAT_10) == at(date(2026, 11, 7), 10, 0)  # exactly at 10:00 -> next week
    assert next_report_time(at(date(2026, 10, 31), 9, 59)) == SAT_10
