from datetime import date

from stock_finder.daily_picks import _save_report, _saved_report


def test_daily_snapshot_roundtrip_is_immutable(tmp_path):
    database = str(tmp_path / "picks.db")
    day = date(2026, 11, 23)
    messages = ["Stock Finder: TATASTEEL LONG TERM", "INFY: NOT RECOMMENDED"]
    assert _saved_report(day, database) is None
    _save_report(day, database, messages)
    _save_report(day, database, ["CHANGED"])
    assert _saved_report(day, database) == messages
    assert _saved_report(date(2026, 11, 24), database) is None


def test_names_only_on_demand(tmp_path):
    from datetime import datetime

    from stock_finder.engine import IST
    from stock_finder.mentions import Mention, TvLog
    from stock_finder.resolver import SymbolResolver
    from stock_finder.daily_picks import previous_text

    day = date(2026, 11, 23)
    at = datetime(2026, 11, 23, 9, 20, tzinfo=IST)
    database = str(tmp_path / "picks.db")
    TvLog(database).replace_day(day, [Mention("Dhan", at, "TATASTEEL", "MENTION")])
    resolver = SymbolResolver(None, list_loader=lambda: {"TATASTEEL": "Tata Steel Limited"},
                              searcher=lambda q: [])
    assert previous_text(day, database, resolver) == "TATASTEEL"

def test_mock_telegram_send_and_idempotent_delivery(tmp_path, monkeypatch):
    """Real delivery code and SQLite acknowledgements, fake HTTP transport."""
    import sqlite3
    from stock_finder import daily_picks

    db = str(tmp_path / "delivery.db")
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, json, timeout):
        sent.append((json["chat_id"], json["text"], timeout))
        return Response()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "MOCK_TOKEN")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "100001")
    monkeypatch.setattr(daily_picks.requests, "post", fake_post)

    day = date(2026, 11, 20)
    messages = ["Mock live report: TATASTEEL LONG TERM", "Mock live report: INFY INTRADAY SHORT"]
    daily_picks._deliver(messages, day, db)
    daily_picks._deliver(messages, day, db)
    assert sent == [(100001, messages[0], 20), (100001, messages[1], 20)]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_picks_delivered").fetchone()[0] == 2


def test_stock_list_precedes_model_output():
    from stock_finder.daily_picks import format_stock_list
    result = format_stock_list(date(2026, 11, 20), ["PAYTM", "INFY", "TCS"])
    assert result == "📋 Stocks collected · 20 Nov 2026\n1. PAYTM\n2. INFY\n3. TCS"
    assert "BUY" not in result and "SELL" not in result


def test_no_empty_stock_list():
    import pytest
    from stock_finder.daily_picks import format_stock_list
    with pytest.raises(ValueError):
        format_stock_list(date(2026, 11, 20), [])
