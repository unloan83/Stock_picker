from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_finder import indicators as ind
from stock_finder.data import Fundamentals, OptionChain, OptionRow, parse_nse_chain, pick_expiry
from stock_finder.engine import (
    INTRADAY,
    LONG_TERM,
    NO_DATA,
    NOT_RECOMMENDED,
    OPTION_AVOID,
    OPTION_BUY,
    analyse_all,
)
from stock_finder.formatter import format_reply
from stock_finder.intraday import score_intraday
from stock_finder.longterm import score_long_term
from stock_finder.models import OptionRequest, StockRequest
from stock_finder.options import max_pain, pcr
from stock_finder.resolver import SymbolResolver
from tests.fakes import FakeMarket, chain, daily_series, intraday_bars

TODAY = date(2026, 10, 29)
STRONG = Fundamentals(roe=0.22, debt_to_equity=0.2, pe=25, earnings_growth=0.18, revenue_growth=0.12)
WEAK = Fundamentals(roe=0.04, debt_to_equity=2.5, pe=-5, earnings_growth=-0.3, revenue_growth=-0.1)


# --- indicators ---------------------------------------------------------------------------------------------------

def test_rsi_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_max_drawdown_and_return():
    s = pd.Series([100, 120, 90, 110], dtype=float)
    assert ind.max_drawdown(s) == pytest.approx(-0.25)
    assert ind.pct_return(s, 3) == pytest.approx(0.10)
    assert ind.pct_return(s, 3, skip=1) == pytest.approx(-0.10)


def test_vwap_weights_by_volume():
    df = pd.DataFrame({"High": [10, 20], "Low": [10, 20], "Close": [10, 20], "Volume": [1, 3]}, dtype=float)
    assert ind.vwap(df).iloc[-1] == pytest.approx(17.5)


# --- long term ----------------------------------------------------------------------------------------------------

def test_uptrend_with_strong_fundamentals_scores_high():
    s = score_long_term(daily_series(drift=0.0015, vol=0.008, volume=5e6), STRONG)
    assert s.score >= 80
    assert any("200-day" in d.name for d in s.top(True, 10))
    assert len(s.drivers) == 12 and all(d.want for d in s.drivers)


def test_downtrend_with_weak_fundamentals_scores_low():
    s = score_long_term(daily_series(drift=-0.0015, vol=0.03, volume=1e4), WEAK)
    assert s.score < 30
    pe = next(d for d in s.drivers if d.name == "P/E")
    assert (pe.value, pe.points, pe.max_points) == ("-5.0", 0, 8)


def test_missing_fundamentals_are_not_penalised():
    data = daily_series(drift=0.0015, vol=0.008, volume=5e6)
    s = score_long_term(data, Fundamentals())
    assert s.score >= 80
    assert sum(not d.scored for d in s.drivers) == 5


def test_short_history_is_flagged():
    s = score_long_term(daily_series(days=100), STRONG)
    assert s.score == 0 and "history" in s.drivers[0].name


# --- intraday -----------------------------------------------------------------------------------------------------

def _hist(end=date(2026, 10, 28), drift=0.002):
    return daily_series(days=60, drift=drift, vol=0.005, end=end)


def test_intraday_strong_open_scores_long():
    hist = _hist()
    prev = float(hist["Close"].iloc[-1])
    path = list(prev * (1.008 + 0.001 * np.arange(8)))  # gap up ~0.8%, grinding higher
    bars = intraday_bars(TODAY, prev, path, today_volume=40_000)
    r = score_intraday(bars, hist, TODAY)
    assert r.plan is not None and r.plan.direction == "LONG"
    assert r.score is not None and r.score.score >= 65
    assert r.plan.stop < r.plan.entry < r.plan.target


def test_intraday_weak_open_scores_short():
    hist = _hist(drift=-0.002)
    prev = float(hist["Close"].iloc[-1])
    path = list(prev * (0.992 - 0.001 * np.arange(8)))
    r = score_intraday(intraday_bars(TODAY, prev, path, today_volume=40_000), hist, TODAY)
    assert r.plan is not None and r.plan.direction == "SHORT"
    assert r.plan.target < r.plan.entry < r.plan.stop


def test_intraday_before_open_returns_note():
    hist = _hist()
    bars = intraday_bars(date(2026, 10, 28), 100, [100, 101])  # last bars are yesterday's
    r = score_intraday(bars, hist, TODAY)
    assert r.score is None and "09:15" in (r.note or "")


# --- options ------------------------------------------------------------------------------------------------------

def test_pcr_and_max_pain():
    c = OptionChain("NIFTY", date(2026, 11, 4), 100, rows=[
        OptionRow(90, ce_oi=10, pe_oi=100), OptionRow(100, ce_oi=50, pe_oi=50), OptionRow(110, ce_oi=100, pe_oi=10)])
    assert pcr(c) == pytest.approx(1.0)
    assert max_pain(c) == 100


def test_pick_expiry():
    exps = [date(2026, 10, 28), date(2026, 11, 4), date(2026, 11, 25)]
    assert pick_expiry(exps, date(2026, 11, 25)) == date(2026, 11, 25)
    with pytest.raises(ValueError):
        pick_expiry(exps, date(2026, 12, 1))


def test_parse_nse_chain_filters_expiry():
    payload = {"records": {"underlyingValue": 25010.5, "data": [
        {"strikePrice": 25000, "expiryDate": "04-Nov-2026",
         "CE": {"openInterest": 10, "lastPrice": 120, "impliedVolatility": 11.5, "totalTradedVolume": 900},
         "PE": {"openInterest": 20, "lastPrice": 100, "impliedVolatility": 0, "totalTradedVolume": 50}},
        {"strikePrice": 25000, "expiryDate": "25-Nov-2026", "CE": {"openInterest": 999}},
    ]}}
    c = parse_nse_chain("NIFTY", payload, date(2026, 11, 4), [date(2026, 11, 4)])
    assert c.spot == 25010.5 and len(c.rows) == 1
    row = c.rows[0]
    assert (row.ce_oi, row.pe_oi, row.ce_iv, row.pe_iv) == (10, 20, 11.5, None)


# --- engine + formatter -------------------------------------------------------------------------------------------

RESOLVER = SymbolResolver(
    None,
    list_loader=lambda: {"GOODCO": "Good Company Limited", "BADCO": "Bad Company Limited",
                         **{f"BADCO{i}": "Bad Company Limited" for i in range(60)}},
    searcher=lambda q: [],
)


def _market():
    up = daily_series(drift=0.0015, vol=0.008, volume=5e6)
    down = daily_series(drift=-0.0015, vol=0.03, volume=1e4, seed=2)
    prev = float(up["Close"].iloc[-1])
    bars = intraday_bars(TODAY, prev, list(prev * (1.008 + 0.001 * np.arange(8))), today_volume=40_000)
    nifty = daily_series(start=20000, drift=0.0008, vol=0.008, seed=3)
    return FakeMarket(
        daily={"GOODCO": up, "BADCO": down, "NIFTY": nifty, **{f"BADCO{i}": down for i in range(60)}},
        intraday={"GOODCO": bars},
        fundamentals={"GOODCO": STRONG, "BADCO": WEAK, **{f"BADCO{i}": WEAK for i in range(60)}},
        chains={"NIFTY": chain(spot=float(nifty["Close"].iloc[-1]), iv=13.0)},
    )


def test_engine_verdicts_end_to_end():
    md = _market()
    spot = md._chains["NIFTY"].spot
    near = round(spot / 100) * 100
    reports = analyse_all(md, RESOLVER, [
        StockRequest("GOODCO"), StockRequest("BADCO"), StockRequest("NOSUCH"),
        OptionRequest("NIFTY", near, "CE"), OptionRequest("NIFTY", near - 900, "PE"), OptionRequest("NIFTY", 1, "CE"),
    ], TODAY)
    good, bad, missing, ce, pe, nostrike = reports
    assert LONG_TERM in good.verdict and INTRADAY in good.verdict
    assert bad.verdict == NOT_RECOMMENDED
    assert missing.verdict == NO_DATA
    assert ce.verdict == OPTION_BUY, ce.score
    assert pe.verdict == OPTION_AVOID
    assert nostrike.verdict == NO_DATA and "nearest" in (nostrike.error or "")

    text = "\n".join(format_reply(reports, ["???"], pd.Timestamp("2026-10-29 09:40")))
    assert "GOODCO" in text and "Plan LONG" in text and "Not understood: ???" in text
    assert "Why: strengths:" in text and "weaknesses:" in text
    assert "✓ ROE: 22% (want >15%) +10/10" in text
    assert "Volume vs normal" in text and "IV vs realised vol" in text
    assert "NOSUCH: NO DATA (not found on NSE" in text


def test_reply_is_split_under_telegram_limit():
    md = _market()
    reports = analyse_all(md, RESOLVER, [StockRequest(f"BADCO{i}") for i in range(60)], TODAY)
    msgs = format_reply(reports, [], pd.Timestamp("2026-10-29 09:40"))
    assert len(msgs) > 1 and all(len(m) <= 4096 for m in msgs)
