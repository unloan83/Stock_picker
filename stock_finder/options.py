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
    pts = 0.0

    def add(p: float, text: str) -> None:
        nonlocal pts
        pts += p
        drivers.append(Driver(text, p))

    close = daily["Close"]
    if len(close) >= 50:
        last = float(close.iloc[-1])
        s20 = float(ind.sma(close, 20).iloc[-1])
        s50 = float(ind.sma(close, 50).iloc[-1])
        aligned = (last > s20 > s50) if up else (last < s20 < s50)
        partly = (last > s20) if up else (last < s20)
        if aligned:
            add(30, f"underlying trend {'up' if up else 'down'} (20 & 50-day avgs)")
        elif partly:
            add(15, f"underlying short-term {'up' if up else 'down'}")
        else:
            add(-15, f"underlying trend against a {kind} buy")

    iv = row.ce_iv if up else row.pe_iv
    hv = realised_vol(daily)
    if iv and not math.isnan(hv) and hv > 0:
        ratio = (iv / 100) / hv
        if ratio <= 1.1:
            add(20, f"premium fairly priced (IV {iv:.0f}% vs realised {hv * 100:.0f}%)")
        elif ratio <= 1.4:
            add(10, f"premium slightly rich (IV {iv:.0f}% vs realised {hv * 100:.0f}%)")
        elif ratio > 1.6:
            add(-15, f"premium expensive (IV {iv:.0f}% vs realised {hv * 100:.0f}%)")

    dte = (chain.expiry - today).days
    if dte >= 5:
        add(15, f"{dte} days to expiry")
    elif dte >= 3:
        add(8, f"{dte} days to expiry")
    else:
        add(-15, f"only {dte} day(s) to expiry: fast time decay")

    if chain.spot:
        otm = (row.strike - chain.spot) / chain.spot if up else (chain.spot - row.strike) / chain.spot
        if otm <= 0.02:
            add(15, "strike near the money" if otm > -0.02 else "strike in the money")
        elif otm <= 0.04:
            add(7, f"strike {otm * 100:.1f}% out of the money")
        elif otm > 0.06:
            add(-15, f"strike {otm * 100:.1f}% out of the money: low odds")

    volume = row.ce_volume if up else row.pe_volume
    if volume >= 1000:
        add(10, "liquid contract")
    elif volume < 100:
        add(-10, "illiquid contract (wide spreads)")

    ratio_pcr = pcr(chain)
    if ratio_pcr is not None:
        if up and ratio_pcr >= 1.0:
            add(10, f"PCR {ratio_pcr:.2f}: put writers supporting")
        elif not up and ratio_pcr <= 0.8:
            add(10, f"PCR {ratio_pcr:.2f}: call writers capping")
        elif up and ratio_pcr < 0.7:
            add(-5, f"PCR {ratio_pcr:.2f}: call writers capping upside")
        elif not up and ratio_pcr > 1.3:
            add(-5, f"PCR {ratio_pcr:.2f}: put writers supporting")

    return Score(round(max(0.0, min(100.0, pts)), 1), drivers)
