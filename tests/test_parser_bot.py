from datetime import datetime

from stock_finder.bot import allowed_chat_ids, is_allowed
from stock_finder.models import OptionRequest, StockRequest
from stock_finder.parser import parse_message
from stock_finder.store import RecommendationLog


def test_parses_names_symbols_and_options():
    reqs, unknown = parse_message(
        "reliance, TCS infy\nNIFTY 25000 CE\nbanknifty 52000pe 27NOV; M&M.NS\nTata Steel\nHDFC Bank 1800 CE 25-Nov")
    assert reqs == [
        StockRequest("RELIANCE"), StockRequest("TCS INFY"),
        OptionRequest("NIFTY", 25000.0, "CE"),
        OptionRequest("BANKNIFTY", 52000.0, "PE", 27, 11),
        StockRequest("M&M"), StockRequest("TATA STEEL"),
        OptionRequest("HDFC BANK", 1800.0, "CE", 25, 11),
    ]
    assert unknown == []


def test_dedupes_and_reports_unknown():
    reqs, unknown = parse_message("TCS, tcs, 12345, hello!, NIFTY 25000 CE 27XYZ")
    assert reqs == [StockRequest("TCS")]
    assert unknown == ["12345", "HELLO!", "NIFTY 25000 CE 27XYZ"]


def test_allowed_chat_ids(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "123, -456")
    ids = allowed_chat_ids()
    assert is_allowed(123, ids) and is_allowed(-456, ids) and not is_allowed(789, ids)
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    assert allowed_chat_ids() == set()


def test_recommendation_log(tmp_path):
    import sqlite3

    from stock_finder.models import Score, StockReport

    path = str(tmp_path / "log.db")
    RecommendationLog(path).record(datetime(2026, 10, 29, 9, 40), 1,
                                   [StockReport("TCS", 4100.0, "LONG TERM", long_term=Score(80))])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT item, verdict, long_term_score FROM recommendations").fetchall() == [
            ("TCS", "LONG TERM", 80.0)]
