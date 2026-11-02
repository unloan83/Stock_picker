from datetime import date, datetime

from stock_finder.engine import IST
from stock_finder.tv import (
    Segment,
    TvLog,
    build_index,
    extract_mentions,
    format_shortlist,
    read_transcript,
    shortlist,
    write_transcript,
)

LISTING = {
    "TATASTEEL": "Tata Steel Limited",
    "TATAMOTORS": "Tata Motors Limited",
    "HDFCBANK": "HDFC Bank Limited",
    "INFY": "Infosys Limited",
    "RELIANCE": "Reliance Industries Limited",
    "ITC": "ITC Limited",
    "SBIN": "State Bank of India",
    "IDEA": "Vodafone Idea Limited",
    "GOLDBEES": "Nippon India ETF Gold BeES",
    "M&M": "Mahindra & Mahindra Limited",
}
AT = datetime(2026, 11, 2, 8, 40, tzinfo=IST)
INDEX = build_index(LISTING)


def mentions(text: str, channel: str = "CNBC-TV18"):
    return extract_mentions(Segment(channel, AT, text), INDEX)


def test_buy_call_with_target_and_stop():
    [m] = mentions("My first pick is Tata Steel, buy it with a target of Rs 1,250 and a stop loss of 1,140.")
    assert (m.symbol, m.action, m.target, m.stop) == ("TATASTEEL", "BUY", 1250.0, 1140.0)


def test_sell_call_and_call_word_before_name():
    found = mentions("Sell HDFC Bank, target 1650. Next, I like Infosys, accumulate for 1900.")
    assert [(m.symbol, m.action) for m in found] == [("HDFCBANK", "SELL"), ("INFY", "BUY")]
    assert found[0].target == 1650.0


def test_names_aliases_and_short_symbols():
    found = mentions("Reliance looks good, and ITC too. SBI and M&M are on watch. Infosys Ltd results today.")
    assert [m.symbol for m in found] == ["RELIANCE", "ITC", "SBIN", "M&M", "INFY"]
    assert all(m.action == "MENTION" for m in found)


def test_common_words_are_not_stocks():
    assert mentions("I have an idea: gold looks strong, but the market is weak.") == []


def test_devanagari_call_words_and_digits():
    [m] = mentions("Tata Motors में खरीदारी करें, टारगेट ९५० और स्टॉप लॉस ९००")
    assert (m.symbol, m.action, m.target, m.stop) == ("TATAMOTORS", "BUY", 950.0, 900.0)


def test_conflicting_words_stay_a_mention():
    [m] = mentions("Don't sell Tata Steel, buy on dips.")
    assert m.action == "MENTION"


def test_shortlist_ranks_by_channels_with_calls():
    ms = (mentions("Buy Tata Steel target 1250", "Zee Business")
          + mentions("Tata Steel is a buy", "CNBC-TV18")
          + mentions("Sell Infosys", "NDTV Profit")
          + mentions("Reliance results today", "CNBC Awaaz"))
    items = shortlist(ms, LISTING)
    assert [(i.symbol, i.call, len(i.channels)) for i in items] == [
        ("TATASTEEL", "BUY", 2), ("INFY", "SELL", 1), ("RELIANCE", "MENTION", 1)]
    text = format_shortlist(items, date(2026, 11, 2), ["Zee Business", "CNBC-TV18"])
    assert "TATASTEEL BUY (2B/0S) on 2 ch" in text and "target 1250" in text
    assert "1 more stock(s) mentioned without a call." in text


def test_mixed_call():
    items = shortlist(mentions("Buy Infosys", "A") + mentions("Sell Infosys", "B"), LISTING)
    assert items[0].call == "MIXED"


def test_store_and_transcript_roundtrip(tmp_path):
    ms = mentions("Buy Tata Steel target 1250 stop loss 1140")
    log = TvLog(str(tmp_path / "t.db"))
    log.replace_day(AT.date(), ms)
    log.replace_day(AT.date(), ms)  # a re-run replaces, never duplicates
    assert log.day(AT.date()) == ms

    path = str(tmp_path / "Zee Business.txt")
    write_transcript(path, [Segment("Zee Business", AT, " buy Tata Steel \n")])
    assert read_transcript(path, "Zee Business") == [Segment("Zee Business", AT, "buy Tata Steel")]


def test_lower_case_word_is_not_a_company():
    index = build_index({**LISTING, "CRUDE": "Crude Ltd", "TIME": "Time Technoplast Limited"})
    found = extract_mentions(Segment("T", AT, "Crude is steady; it is time to buy. Time Technoplast is a buy."), index)
    assert [(m.symbol, m.action) for m in found] == [("TIME", "BUY")]


def test_nearest_call_word_wins():
    found = mentions("Accumulate Infosys, target 1900. Avoid Vodafone Idea for now.")
    assert [(m.symbol, m.action, m.target) for m in found] == [("INFY", "BUY", 1900.0), ("IDEA", "SELL", None)]


def test_called_stocks_get_engine_verdicts(tmp_path):
    from stock_finder.resolver import SymbolResolver
    from stock_finder.store import RecommendationLog
    from stock_finder.tv_run import verdicts
    from tests.fakes import FakeMarket, daily_series

    items = shortlist(mentions("Buy Tata Steel target 1250. Reliance results today."), LISTING)
    resolver = SymbolResolver(None, list_loader=lambda: LISTING, searcher=lambda q: [])
    md = FakeMarket({"TATASTEEL": daily_series(), "NIFTY": daily_series()})
    db = str(tmp_path / "r.db")
    replies = verdicts(items, md, resolver, AT, db, [11, 22])
    text = "\n".join(replies)
    assert "TATASTEEL" in text and "RELIANCE" not in text  # only stocks with a call are analysed
    rows = RecommendationLog(db).between(AT.replace(hour=0), AT.replace(hour=23))
    assert sorted((r.chat_id, r.symbol) for r in rows) == [(11, "TATASTEEL"), (22, "TATASTEEL")]
    assert verdicts(shortlist(mentions("Reliance results today."), LISTING), md, resolver, AT, db, []) == []


def test_read_wav_returns_float_samples(tmp_path):
    import struct
    import wave

    import pytest

    from stock_finder.tv_capture import SAMPLE_RATE, read_wav

    path = str(tmp_path / "chunk_000.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<3h", 0, 16384, -32768))
    audio = read_wav(path)
    assert audio.dtype.name == "float32" and list(audio) == [0.0, 0.5, -1.0]

    stereo = str(tmp_path / "stereo.wav")
    with wave.open(stereo, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\0" * 8)
    with pytest.raises(ValueError, match="mono"):
        read_wav(stereo)


def test_recording_test_picks_the_broadcast_covering_the_window():
    import importlib.util
    from datetime import datetime

    from stock_finder.engine import IST

    spec = importlib.util.spec_from_file_location("tv_recording_test", "scripts/tv_recording_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    w0 = datetime(2026, 11, 2, 8, 30, tzinfo=IST)
    w1 = datetime(2026, 11, 2, 9, 15, tzinfo=IST)
    ts = lambda h, m: datetime(2026, 11, 2, h, m, tzinfo=IST).timestamp()  # noqa: E731
    infos = [
        {"id": "live", "live_status": "is_live", "release_timestamp": ts(6, 0), "duration": None},
        {"id": "early", "live_status": "was_live", "release_timestamp": ts(7, 0), "duration": 100 * 60},  # to 08:40
        {"id": "morning", "live_status": "was_live", "release_timestamp": ts(8, 0), "duration": 120 * 60},
        {"id": "tiny", "live_status": "was_live", "release_timestamp": ts(9, 12), "duration": 600},
    ]
    info, start, a, b = mod.pick_recording(infos, w0, w1)
    assert info["id"] == "morning" and (a, b) == (w0, w1) and start.hour == 8
    assert mod.pick_recording(infos[:1] + infos[3:], w0, w1) is None
