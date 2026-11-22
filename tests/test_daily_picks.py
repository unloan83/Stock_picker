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
