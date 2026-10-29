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
        pts = 0.0

        def add(p: float, text: str) -> None:
            nonlocal pts
            pts += p
            drivers.append(Driver(text, p))

        if (price - vwap_now) * sign > 0:
            add(25, f"price {'above' if up else 'below'} VWAP {vwap_now:.2f}")
        else:
            add(-10, f"price on wrong side of VWAP {vwap_now:.2f}")
        if rel_vol is not None:
            if rel_vol >= 1.5:
                add(25, f"volume {rel_vol:.1f}x normal")
            elif rel_vol >= 1.2:
                add(12, f"volume {rel_vol:.1f}x normal")
            elif rel_vol < 0.8:
                add(-10, f"thin volume ({rel_vol:.1f}x normal)")
        if up:
            if 50 <= rsi_now <= 70:
                add(15, f"RSI {rsi_now:.0f} (healthy strength)")
            elif rsi_now > 75:
                add(-10, f"RSI {rsi_now:.0f} overbought")
        else:
            if 30 <= rsi_now <= 50:
                add(15, f"RSI {rsi_now:.0f} (weakening)")
            elif rsi_now < 25:
                add(-10, f"RSI {rsi_now:.0f} oversold")
        if (prev_daily_close - sma20) * sign > 0:
            add(15, f"daily trend {'up' if up else 'down'} (vs 20-day avg)")
        g = gap * sign
        if 0.003 <= g <= 0.03:
            add(10, f"gap {gap * 100:+.1f}% in trade direction")
        elif g > 0.04:
            add(-10, f"gap {gap * 100:+.1f}% too large (fade risk)")
        elif g < -0.01:
            add(-5, f"gap {gap * 100:+.1f}% against trade")
        edge = (day_high - price) if up else (price - day_low)
        if atr_d > 0 and edge <= 0.25 * atr_d:
            add(10, f"trading near day {'high' if up else 'low'}")
        return Score(round(max(0.0, min(100.0, pts)), 1), drivers)

    long_s, short_s = side("LONG"), side("SHORT")
    direction, best = ("LONG", long_s) if long_s.score >= short_s.score else ("SHORT", short_s)
    sign = 1 if direction == "LONG" else -1
    plan = IntradayPlan(direction=direction, entry=round(price, 2),
                        stop=round(price - sign * STOP_ATR * atr_d, 2),
                        target=round(price + sign * TARGET_ATR * atr_d, 2))
    note = "early in session: few bars, lower confidence" if len(today_bars) < 3 else None
    return IntradayResult(best, plan, note)
