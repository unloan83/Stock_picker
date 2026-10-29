"""Long-term (months to years) score: trend + fundamentals + risk, 0-100.

Every parameter is recorded with its value, target and points. Missing data removes that parameter's points from the
maximum instead of counting as zero, so a stock is not penalised for a field Yahoo did not return.
"""

import math

import pandas as pd

from . import indicators as ind
from .data import Fundamentals
from .models import Driver, Score

MIN_HISTORY = 210  # sessions; enough for a 200-day average


def _pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{int(round(x * 100)):+d}%"


def _tiered(value: float | None, full: bool, half: bool, max_pts: float) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return max_pts if full else max_pts / 2 if half else 0.0


def score_long_term(daily: pd.DataFrame, fund: Fundamentals) -> Score:
    if len(daily) < MIN_HISTORY:
        return Score(0.0, [Driver("Price history", f"{len(daily)} sessions", f"≥{MIN_HISTORY}", 0, 1)])

    close = daily["Close"]
    price = float(close.iloc[-1])
    sma50 = float(ind.sma(close, 50).iloc[-1])
    sma200 = float(ind.sma(close, 200).iloc[-1])
    mom = ind.pct_return(close, 252, skip=21) if len(close) > 252 else ind.pct_return(close, len(close) - 1, 21)
    off_high = price / float(close.tail(252).max()) - 1
    vol = ind.annualized_vol(close)
    mdd = ind.max_drawdown(close.tail(252))
    traded_cr = float((close * daily["Volume"]).tail(20).mean()) / 1e7

    drivers: list[Driver] = []

    def rule(name: str, value: str, want: str, max_pts: float, pts: float | None) -> None:
        if pts is None:
            drivers.append(Driver(name, "n/a", want, 0, 0))
        else:
            drivers.append(Driver(name, value, want, pts, max_pts))

    # Trend (35)
    rule("Price vs 200-day avg", f"{price:,.1f} vs {sma200:,.1f} ({price / sma200 - 1:+.1%})", "above", 10,
         10 if price > sma200 else 0)
    rule("50-day vs 200-day avg", f"{sma50:,.1f} vs {sma200:,.1f}", "50 above 200", 10, 10 if sma50 > sma200 else 0)
    rule("12-month momentum", _pct(mom), ">+10%", 10, _tiered(mom, mom > 0.10, mom > 0, 10))
    rule("From 52-week high", _pct(off_high), "within -15%", 5, 5 if off_high > -0.15 else 0)

    # Fundamentals (40)
    roe, de, eg, rg, pe = fund.roe, fund.debt_to_equity, fund.earnings_growth, fund.revenue_growth, fund.pe
    rule("ROE", f"{roe * 100:.0f}%" if roe is not None else "", ">15%", 10,
         _tiered(roe, roe is not None and roe > 0.15, roe is not None and roe > 0.10, 10))
    rule("Debt/Equity", f"{de:.2f}" if de is not None else "", "<0.5", 8,
         _tiered(de, de is not None and de < 0.5, de is not None and de < 1.0, 8))
    rule("Earnings growth (yoy)", _pct(eg) if eg is not None else "", ">+10%", 8,
         _tiered(eg, eg is not None and eg > 0.10, eg is not None and eg > 0, 8))
    rule("Revenue growth (yoy)", _pct(rg) if rg is not None else "", ">+8%", 6,
         _tiered(rg, rg is not None and rg > 0.08, rg is not None and rg > 0, 6))
    rule("P/E", f"{pe:.1f}" if pe is not None else "", "0-40", 8,
         _tiered(pe, pe is not None and 0 < pe < 40, pe is not None and 0 < pe < 60, 8))

    # Risk (25)
    rule("Volatility (1 yr)", f"{vol * 100:.0f}%", "<25%", 10, _tiered(vol, vol < 0.25, vol < 0.35, 10))
    rule("Max fall (1 yr)", _pct(mdd), "better than -20%", 10, _tiered(mdd, mdd > -0.20, mdd > -0.30, 10))
    rule("Avg traded value", f"₹{traded_cr:,.1f} cr/day", ">₹10 cr", 5, 5 if traded_cr > 10 else 0)

    available = sum(d.max_points for d in drivers)
    earned = sum(d.points for d in drivers)
    return Score(round(100 * earned / available, 1) if available else 0.0, drivers)
