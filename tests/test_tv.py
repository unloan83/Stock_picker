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
