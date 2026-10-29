"""Option-buyer score, 0-100: is buying this CE/PE a reasonable directional trade today?

Looks at underlying trend, premium cost (option IV vs the underlying's realised volatility), time to expiry,
distance from spot, liquidity and the put-call ratio. Also reports max pain.
"""

import math
from datetime import date

import pandas as pd

from . import indicators as ind
from .data import OptionChain, OptionRow
from .models import Driver, Score


def pcr(chain: OptionChain) -> float | None:
    ce = sum(r.ce_oi for r in chain.rows)
    pe = sum(r.pe_oi for r in chain.rows)
    return pe / ce if ce else None


def max_pain(chain: OptionChain) -> float | None:
    """Strike at which option writers pay out the least at expiry."""
    if not chain.rows:
        return None

    def payout(k: float) -> float:
        return sum(r.ce_oi * max(0.0, k - r.strike) + r.pe_oi * max(0.0, r.strike - k) for r in chain.rows)

    return min((r.strike for r in chain.rows), key=payout)


def realised_vol(daily: pd.DataFrame, days: int = 20) -> float:
    return ind.annualized_vol(daily["Close"], days)


def find_row(chain: OptionChain, strike: float) -> OptionRow | None:
    return next((r for r in chain.rows if abs(r.strike - strike) < 1e-6), None)


def score_option(chain: OptionChain, row: OptionRow, kind: str, daily: pd.DataFrame, today: date) -> Score:
    up = kind == "CE"
    drivers: list[Driver] = []

    def add(name: str, value: str, want: str, max_pts: float, pts: float) -> None:
        drivers.append(Driver(name, value, want, pts, max_pts))

    close = daily["Close"]
    want_trend = "price > 20d > 50d" if up else "price < 20d < 50d"
    if len(close) >= 50:
        last = float(close.iloc[-1])
        s20 = float(ind.sma(close, 20).iloc[-1])
        s50 = float(ind.sma(close, 50).iloc[-1])
        aligned = (last > s20 > s50) if up else (last < s20 < s50)
        partly = (last > s20) if up else (last < s20)
        add("Underlying trend", f"{last:,.0f} / 20d {s20:,.0f} / 50d {s50:,.0f}", want_trend, 30,
            30 if aligned else 15 if partly else -15)
    else:
        add("Underlying trend", "n/a", want_trend, 0, 0)

    iv = row.ce_iv if up else row.pe_iv
    hv = realised_vol(daily)
    if iv and not math.isnan(hv) and hv > 0:
        ratio = (iv / 100) / hv
        pts = 20 if ratio <= 1.1 else 10 if ratio <= 1.4 else -15 if ratio > 1.6 else 0
        add("IV vs realised vol (20d)", f"{iv:.1f}% vs {hv * 100:.1f}% ({ratio:.2f}x)", "≤1.1x", 20, pts)
    else:
        add("IV vs realised vol (20d)", "n/a", "≤1.1x", 0, 0)

    dte = (chain.expiry - today).days
    add("Days to expiry", str(dte), "≥5", 15, 15 if dte >= 5 else 8 if dte >= 3 else -15)

    if chain.spot:
        otm = (row.strike - chain.spot) / chain.spot if up else (chain.spot - row.strike) / chain.spot
        pts = 15 if otm <= 0.02 else 7 if otm <= 0.04 else -15 if otm > 0.06 else 0
        where = "OTM" if otm > 0 else "ITM"
        add("Strike vs spot", f"{abs(otm) * 100:.1f}% {where} (spot {chain.spot:,.0f})", "≤2% OTM", 15, pts)
    else:
        add("Strike vs spot", "n/a", "≤2% OTM", 0, 0)

    volume = row.ce_volume if up else row.pe_volume
    add("Contracts traded today", f"{volume:,.0f}", "≥1,000", 10, 10 if volume >= 1000 else -10 if volume < 100 else 0)

    ratio_pcr = pcr(chain)
    if ratio_pcr is None:
        add("Put-call ratio (OI)", "n/a", "≥1.0" if up else "≤0.8", 0, 0)
    elif up:
        add("Put-call ratio (OI)", f"{ratio_pcr:.2f}", "≥1.0", 10,
            10 if ratio_pcr >= 1.0 else -5 if ratio_pcr < 0.7 else 0)
    else:
        add("Put-call ratio (OI)", f"{ratio_pcr:.2f}", "≤0.8", 10,
            10 if ratio_pcr <= 0.8 else -5 if ratio_pcr > 1.3 else 0)

    return Score(round(max(0.0, min(100.0, sum(d.points for d in drivers))), 1), drivers)
