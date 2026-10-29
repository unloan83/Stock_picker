"""Long-term (months to years) score: trend + fundamentals + risk, 0-100.

Each rule adds points and records a driver. Missing data removes that rule's points from the maximum instead of
counting as zero, so a stock is not penalised for a field Yahoo did not return.
"""

import math

import pandas as pd

from . import indicators as ind
from .data import Fundamentals
from .models import Driver, Score

MIN_HISTORY = 210  # sessions; enough for a 200-day average


def _pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def score_long_term(daily: pd.DataFrame, fund: Fundamentals) -> Score:
    if len(daily) < MIN_HISTORY:
        return Score(0.0, [Driver(f"only {len(daily)} sessions of history (need {MIN_HISTORY})", -1)])

    close = daily["Close"]
    price = float(close.iloc[-1])
    sma50 = float(ind.sma(close, 50).iloc[-1])
    sma200 = float(ind.sma(close, 200).iloc[-1])
    mom_12_1 = ind.pct_return(close, 252, skip=21) if len(close) > 252 else ind.pct_return(close, len(close) - 1, 21)
    high_52w = float(close.tail(252).max())
    vol = ind.annualized_vol(close)
    mdd = ind.max_drawdown(close.tail(252))
    traded_value = float((close * daily["Volume"]).tail(20).mean())

    drivers: list[Driver] = []
    earned = 0.0
    available = 0.0

    def rule(max_pts: float, pts: float | None, good: str, bad: str | None = None) -> None:
        nonlocal earned, available
        if pts is None:  # data missing: rule not scored
            return
        available += max_pts
        earned += pts
        if pts > 0:
            drivers.append(Driver(good, pts))
        elif bad:
            drivers.append(Driver(bad, -max_pts))

    # Trend (35)
    rule(10, 10 if price > sma200 else 0, "price above 200-day average", "price below 200-day average")
    rule(10, 10 if sma50 > sma200 else 0, "50-day avg above 200-day (uptrend)", "50-day avg below 200-day (downtrend)")
    if not math.isnan(mom_12_1):
        pts = 10 if mom_12_1 > 0.10 else 5 if mom_12_1 > 0 else 0
        rule(10, pts, f"12-month momentum {_pct(mom_12_1)}", f"12-month momentum {_pct(mom_12_1)}")
    off_high = price / high_52w - 1
    rule(5, 5 if off_high > -0.15 else 0, f"near 52-week high ({_pct(off_high)})",
         f"{_pct(off_high)} below 52-week high")

    # Fundamentals (40)
    if fund.roe is not None:
        pts = 10 if fund.roe > 0.15 else 5 if fund.roe > 0.10 else 0
        rule(10, pts, f"ROE {fund.roe * 100:.0f}%", f"weak ROE {fund.roe * 100:.0f}%")
    if fund.debt_to_equity is not None:
        de = fund.debt_to_equity
        pts = 8 if de < 0.5 else 4 if de < 1.0 else 0
        rule(8, pts, f"low debt (D/E {de:.2f})", f"high debt (D/E {de:.2f})")
    if fund.earnings_growth is not None:
        g = fund.earnings_growth
        pts = 8 if g > 0.10 else 4 if g > 0 else 0
        rule(8, pts, f"earnings growth {_pct(g)}", f"earnings growth {_pct(g)}")
    if fund.revenue_growth is not None:
        g = fund.revenue_growth
        rule(6, 6 if g > 0.08 else 3 if g > 0 else 0, f"revenue growth {_pct(g)}", f"revenue growth {_pct(g)}")
    if fund.pe is not None:
        pe = fund.pe
        pts = 8 if 0 < pe < 40 else 4 if 0 < pe < 60 else 0
        bad = "loss-making (negative P/E)" if pe <= 0 else f"expensive (P/E {pe:.0f})"
        rule(8, pts, f"valuation reasonable (P/E {pe:.0f})", bad)

    # Risk (25)
    if not math.isnan(vol):
        rule(10, 10 if vol < 0.25 else 5 if vol < 0.35 else 0, f"volatility {vol * 100:.0f}%/yr",
             f"high volatility {vol * 100:.0f}%/yr")
    if not math.isnan(mdd):
        rule(10, 10 if mdd > -0.20 else 5 if mdd > -0.30 else 0, f"1-yr max fall {_pct(mdd)}",
             f"deep 1-yr fall {_pct(mdd)}")
    rule(5, 5 if traded_value > 1e8 else 0, "liquid (>₹10 cr/day traded)",
         f"illiquid (₹{traded_value / 1e7:.1f} cr/day traded)")

    score = 100 * earned / available if available else 0.0
    return Score(round(score, 1), drivers)
