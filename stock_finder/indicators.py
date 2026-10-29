"""Pure technical indicators on pandas Series/DataFrames (OHLCV columns: Open, High, Low, Close, Volume)."""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP over the given bars (pass one session only)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"].cumsum()
    return (typical * df["Volume"]).cumsum() / vol.replace(0, np.nan)


def annualized_vol(close: pd.Series, lookback: int = TRADING_DAYS) -> float:
    rets = close.pct_change().dropna().tail(lookback)
    return float(rets.std() * np.sqrt(TRADING_DAYS)) if len(rets) > 1 else float("nan")


def max_drawdown(close: pd.Series) -> float:
    """Most negative peak-to-trough move, as a fraction (e.g. -0.25)."""
    if close.empty:
        return float("nan")
    return float((close / close.cummax() - 1).min())


def pct_return(close: pd.Series, days: int, skip: int = 0) -> float:
    """Return over `days` sessions ending `skip` sessions ago."""
    if len(close) <= days:
        return float("nan")
    end = close.iloc[-1 - skip]
    start = close.iloc[-1 - days]
    return float(end / start - 1)
