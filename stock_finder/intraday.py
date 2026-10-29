"""Intraday (same-day) score for the opening hour, 0-100, scored separately for LONG and SHORT.

Uses today's 5-minute bars against prior sessions: opening gap, relative volume, VWAP, RSI, daily trend and position
in the day's range. The plan's stop and target are sized from the daily ATR.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import indicators as ind
from .models import Driver, IntradayPlan, Score

STOP_ATR = 0.5
TARGET_ATR = 1.0


@dataclass
class IntradayResult:
    score: Score | None
    plan: IntradayPlan | None
    note: str | None = None


def _relative_volume(bars: pd.DataFrame, today: date) -> float | None:
    """Today's volume so far divided by the average volume of prior sessions up to the same time of day."""
    day_index = bars.index.date
    today_bars = bars[day_index == today]
    cutoff = today_bars.index[-1].time()
    prior = bars[day_index < today]
    if prior.empty:
        return None
    same_window = prior[prior.index.time <= cutoff]
    per_day = same_window.groupby(same_window.index.date)["Volume"].sum()
    per_day = per_day[per_day > 0]
    if per_day.empty:
        return None
    return float(today_bars["Volume"].sum() / per_day.mean())


def score_intraday(bars: pd.DataFrame, daily: pd.DataFrame, today: date) -> IntradayResult:
    if bars.empty or bars.index[-1].date() != today:
        return IntradayResult(None, None, "no bars for today yet (market opens 09:15 IST)")
    today_bars = bars[bars.index.date == today]
    prior_bars = bars[bars.index.date < today]
    if prior_bars.empty:
        return IntradayResult(None, None, "no previous session bars")

    hist = daily[daily.index.date < today] if len(daily) else daily
    if len(hist) < 20:
        return IntradayResult(None, None, "not enough daily history")

    price = float(today_bars["Close"].iloc[-1])
    prev_close = float(prior_bars["Close"].iloc[-1])
    gap = float(today_bars["Open"].iloc[0]) / prev_close - 1
    vwap_now = float(ind.vwap(today_bars).iloc[-1])
    rel_vol = _relative_volume(bars, today)
    rsi_now = float(ind.rsi(bars["Close"]).iloc[-1])
    sma20 = float(ind.sma(hist["Close"], 20).iloc[-1])
    prev_daily_close = float(hist["Close"].iloc[-1])
    atr_d = float(ind.atr(hist).iloc[-1])
    day_high = float(today_bars["High"].max())
    day_low = float(today_bars["Low"].min())

    def side(direction: str) -> Score:
        up = direction == "LONG"
        sign = 1 if up else -1
        drivers: list[Driver] = []

        def add(name: str, value: str, want: str, max_pts: float, pts: float) -> None:
            drivers.append(Driver(name, value, want, pts, max_pts))

        above = (price - vwap_now) * sign > 0
        add("Price vs VWAP", f"{price:,.2f} vs {vwap_now:,.2f}", "above" if up else "below", 25,
            25 if above else -10)
        if rel_vol is None:
            add("Volume vs normal", "n/a", "≥1.5x", 0, 0)
        else:
            pts = 25 if rel_vol >= 1.5 else 12 if rel_vol >= 1.2 else -10 if rel_vol < 0.8 else 0
            add("Volume vs normal", f"{rel_vol:.1f}x", "≥1.5x", 25, pts)
        if up:
            pts = 15 if 50 <= rsi_now <= 70 else -10 if rsi_now > 75 else 0
            add("RSI (5-min)", f"{rsi_now:.0f}", "50-70", 15, pts)
        else:
            pts = 15 if 30 <= rsi_now <= 50 else -10 if rsi_now < 25 else 0
            add("RSI (5-min)", f"{rsi_now:.0f}", "30-50", 15, pts)
        trend_ok = (prev_daily_close - sma20) * sign > 0
        add("Daily close vs 20-day avg", f"{prev_daily_close:,.2f} vs {sma20:,.2f}", "above" if up else "below",
            15, 15 if trend_ok else 0)
        g = gap * sign
        pts = 10 if 0.003 <= g <= 0.03 else -10 if g > 0.04 else -5 if g < -0.01 else 0
        add("Opening gap", f"{gap * 100:+.1f}%", "+0.3% to +3%" if up else "-0.3% to -3%", 10, pts)
        edge = (day_high - price) if up else (price - day_low)
        near = atr_d > 0 and edge <= 0.25 * atr_d
        add(f"Distance from day {'high' if up else 'low'}", f"{edge:,.2f} (ATR {atr_d:,.2f})", "≤0.25 ATR", 10,
            10 if near else 0)
        pts_total = sum(d.points for d in drivers)
        return Score(round(max(0.0, min(100.0, pts_total)), 1), drivers)

    long_s, short_s = side("LONG"), side("SHORT")
    direction, best = ("LONG", long_s) if long_s.score >= short_s.score else ("SHORT", short_s)
    sign = 1 if direction == "LONG" else -1
    plan = IntradayPlan(direction=direction, entry=round(price, 2),
                        stop=round(price - sign * STOP_ATR * atr_d, 2),
                        target=round(price + sign * TARGET_ATR * atr_d, 2))
    note = "early in session: few bars, lower confidence" if len(today_bars) < 3 else None
    return IntradayResult(best, plan, note)
