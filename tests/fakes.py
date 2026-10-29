"""Synthetic market data for tests (shape matches what yfinance/NSE return)."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from stock_finder.data import Fundamentals, OptionChain, OptionRow

IST = "Asia/Kolkata"


def daily_series(days: int = 400, start: float = 100.0, drift: float = 0.001, vol: float = 0.01,
                 end: date = date(2026, 10, 28), seed: int = 1, volume: float = 2e6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(days)
    close = start * np.cumprod(1 + rets)
    idx = pd.bdate_range(end=end, periods=days, tz=IST)
    return pd.DataFrame({"Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": np.full(days, volume)}, index=idx)


def intraday_bars(today: date, prev_close: float, today_path: list[float], prev_days: int = 4,
                  prev_volume: float = 10_000, today_volume: float = 20_000) -> pd.DataFrame:
    """Flat prior sessions at `prev_close`, then today's 5-minute closes following `today_path`."""
    rows, idx = [], []
    day = today - timedelta(days=prev_days + 2)
    made = 0
    while made < prev_days:
        if day.weekday() < 5 and day < today:
            for i in range(75):
                ts = datetime.combine(day, time(9, 15)) + timedelta(minutes=5 * i)
                rows.append((prev_close, prev_close * 1.001, prev_close * 0.999, prev_close, prev_volume))
                idx.append(ts)
            made += 1
        day += timedelta(days=1)
    prev = today_path[0]
    for i, c in enumerate(today_path):
        ts = datetime.combine(today, time(9, 15)) + timedelta(minutes=5 * i)
        rows.append((prev, max(prev, c) * 1.0005, min(prev, c) * 0.9995, c, today_volume))
        idx.append(ts)
        prev = c
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                        index=pd.DatetimeIndex(idx).tz_localize(IST))


def chain(spot: float = 25000, expiry: date = date(2026, 11, 4), pcr_bias: float = 1.2,
          iv: float = 12.0, volume: float = 5000) -> OptionChain:
    rows = [OptionRow(strike=float(k), ce_oi=1000.0, pe_oi=1000.0 * pcr_bias, ce_ltp=100.0, pe_ltp=90.0,
                      ce_iv=iv, pe_iv=iv, ce_volume=volume, pe_volume=volume)
            for k in range(int(spot // 100) * 100 - 1000, int(spot // 100) * 100 + 1100, 100)]
    return OptionChain(symbol="NIFTY", expiry=expiry, spot=spot, rows=rows, expiries=[expiry])


class FakeMarket:
    def __init__(self, daily: dict[str, pd.DataFrame], intraday: dict[str, pd.DataFrame] | None = None,
                 fundamentals: dict[str, Fundamentals] | None = None, chains: dict[str, OptionChain] | None = None):
        self._daily = daily
        self._intraday = intraday or {}
        self._fund = fundamentals or {}
        self._chains = chains or {}

    def daily(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._daily:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return self._daily[symbol]

    def intraday(self, symbol: str) -> pd.DataFrame:
        return self._intraday.get(symbol, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))

    def fundamentals(self, symbol: str) -> Fundamentals:
        return self._fund.get(symbol, Fundamentals())

    def option_chain(self, symbol: str, expiry: date | None) -> OptionChain:
        if symbol not in self._chains:
            raise RuntimeError("no chain")
        return self._chains[symbol]
