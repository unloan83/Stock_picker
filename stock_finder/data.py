"""Free NSE data sources: Yahoo Finance (via yfinance) for prices/fundamentals, NSE website for option chains.

Everything network-bound lives here so the analysis modules stay pure and testable with fakes.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd
import requests

log = logging.getLogger(__name__)

# NSE index names -> Yahoo tickers (for the underlying's price history)
INDEX_YAHOO = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
    "NIFTYNXT50": "^NSMIDCP",
}


def yahoo_ticker(symbol: str) -> str:
    return INDEX_YAHOO.get(symbol, f"{symbol}.NS")


def is_index(symbol: str) -> bool:
    return symbol in INDEX_YAHOO


@dataclass
class Fundamentals:
    roe: float | None = None  # fraction, 0.18 = 18%
    debt_to_equity: float | None = None  # ratio, 0.4 = 40%
    pe: float | None = None
    earnings_growth: float | None = None  # fraction, yoy
    revenue_growth: float | None = None  # fraction, yoy
    sector: str | None = None
    next_earnings: date | None = None


@dataclass
class OptionRow:
    strike: float
    ce_oi: float = 0.0
    pe_oi: float = 0.0
    ce_ltp: float | None = None
    pe_ltp: float | None = None
    ce_iv: float | None = None  # percent, 14.5 = 14.5%
    pe_iv: float | None = None
    ce_volume: float = 0.0
    pe_volume: float = 0.0


@dataclass
class OptionChain:
    symbol: str
    expiry: date
    spot: float
    rows: list[OptionRow] = field(default_factory=list)
    expiries: list[date] = field(default_factory=list)


class MarketData(Protocol):
    def daily(self, symbol: str) -> pd.DataFrame: ...
    def intraday(self, symbol: str, period: str = "5d") -> pd.DataFrame: ...
    def fundamentals(self, symbol: str) -> Fundamentals: ...
    def option_chain(self, symbol: str, expiry: date | None) -> OptionChain: ...


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


class FreeMarketData:
    """Yahoo Finance + NSE public endpoints. Results are cached for a few minutes per symbol."""

    CACHE_SECONDS = 180

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}
        self._nse = NseClient()

    def _cached(self, key: tuple[str, str], loader: Any) -> Any:
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.CACHE_SECONDS:
            return hit[1]
        value = loader()
        self._cache[key] = (time.time(), value)
        return value

    def daily(self, symbol: str) -> pd.DataFrame:
        import yfinance as yf

        def load() -> pd.DataFrame:
            df = yf.Ticker(yahoo_ticker(symbol)).history(period="2y", interval="1d", auto_adjust=True)
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])

        return self._cached(("daily", symbol), load)

    def intraday(self, symbol: str, period: str = "5d") -> pd.DataFrame:
        """5-minute bars in IST. Yahoo keeps about 60 days of them."""
        import yfinance as yf

        def load() -> pd.DataFrame:
            df = yf.Ticker(yahoo_ticker(symbol)).history(period=period, interval="5m", auto_adjust=False)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            if df.index.tz is not None:
                df.index = df.index.tz_convert("Asia/Kolkata")
            return df

        return self._cached((f"intraday-{period}", symbol), load)

    def fundamentals(self, symbol: str) -> Fundamentals:
        import yfinance as yf

        def load() -> Fundamentals:
            if is_index(symbol):
                return Fundamentals()
            t = yf.Ticker(yahoo_ticker(symbol))
            info: dict[str, Any] = {}
            try:
                info = t.info or {}
            except Exception as exc:  # yfinance raises many types on partial data
                log.warning("info failed for %s: %s", symbol, exc)
            de = _num(info.get("debtToEquity"))
            return Fundamentals(
                roe=_num(info.get("returnOnEquity")),
                debt_to_equity=de / 100 if de is not None else None,  # Yahoo reports D/E in percent
                pe=_num(info.get("trailingPE")),
                earnings_growth=_num(info.get("earningsGrowth")),
                revenue_growth=_num(info.get("revenueGrowth")),
                sector=info.get("sector"),
                next_earnings=_next_earnings(t),
            )

        return self._cached(("fund", symbol), load)

    def option_chain(self, symbol: str, expiry: date | None) -> OptionChain:
        return self._cached(("oc", f"{symbol}|{expiry}"), lambda: self._nse.option_chain(symbol, expiry))


def _next_earnings(ticker: Any) -> date | None:
    try:
        cal = ticker.calendar
    except Exception:
        return None
    dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if not dates:
        return None
    today = date.today()
    upcoming = sorted(d for d in dates if isinstance(d, date) and d >= today)
    return upcoming[0] if upcoming else None


class NseClient:
    """Minimal NSE option-chain client. NSE requires browser-like headers and cookies from the home page."""

    BASE = "https://www.nseindia.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    }

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self._session_at = 0.0

    def _get(self, path: str, params: dict[str, str]) -> Any:
        if self._session is None or time.time() - self._session_at > 600:
            self._session = requests.Session()
            self._session.headers.update(self.HEADERS)
            self._session.get(f"{self.BASE}/option-chain", timeout=10)
            self._session_at = time.time()
        resp = self._session.get(f"{self.BASE}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def option_chain(self, symbol: str, expiry: date | None) -> OptionChain:
        kind = "Indices" if is_index(symbol) else "Equity"
        try:
            info = self._get("/api/option-chain-contract-info", {"symbol": symbol})
            expiries = sorted(_parse_nse_date(d) for d in info.get("expiryDates", []))
            chosen = pick_expiry(expiries, expiry)
            payload = self._get(
                "/api/option-chain-v3",
                {"type": kind, "symbol": symbol, "expiry": chosen.strftime("%d-%b-%Y")},
            )
        except (requests.RequestException, ValueError) as exc:
            log.info("v3 option chain failed for %s (%s); trying legacy endpoint", symbol, exc)
            path = "/api/option-chain-indices" if kind == "Indices" else "/api/option-chain-equities"
            payload = self._get(path, {"symbol": symbol})
            expiries = sorted(_parse_nse_date(d) for d in payload.get("records", {}).get("expiryDates", []))
            chosen = pick_expiry(expiries, expiry)
        return parse_nse_chain(symbol, payload, chosen, expiries)


def _parse_nse_date(text: str) -> date:
    return datetime.strptime(text, "%d-%b-%Y").date()


def pick_expiry(expiries: list[date], wanted: date | None) -> date:
    """Nearest expiry on/after today, or the one matching `wanted`'s day and month."""
    if not expiries:
        raise ValueError("NSE returned no expiries")
    if wanted is not None:
        for e in expiries:
            if (e.day, e.month) == (wanted.day, wanted.month):
                return e
        raise ValueError(f"no expiry on {wanted:%d %b}; available: "
                         + ", ".join(f"{e:%d%b}" for e in expiries[:5]))
    today = date.today()
    upcoming = [e for e in expiries if e >= today]
    return upcoming[0] if upcoming else expiries[-1]


def parse_nse_chain(symbol: str, payload: dict[str, Any], expiry: date, expiries: list[date]) -> OptionChain:
    records = payload.get("records", {})
    spot = _num(records.get("underlyingValue")) or 0.0
    rows: dict[float, OptionRow] = {}
    for item in records.get("data", []):
        exp_text = item.get("expiryDate") or item.get("expiryDates")
        if exp_text and _parse_nse_date(exp_text) != expiry:
            continue
        strike = _num(item.get("strikePrice"))
        if strike is None:
            continue
        row = rows.setdefault(strike, OptionRow(strike=strike))
        for side in ("CE", "PE"):
            leg = item.get(side)
            if not leg:
                continue
            p = side.lower()
            setattr(row, f"{p}_oi", _num(leg.get("openInterest")) or 0.0)
            setattr(row, f"{p}_ltp", _num(leg.get("lastPrice")))
            setattr(row, f"{p}_iv", _num(leg.get("impliedVolatility")) or None)
            setattr(row, f"{p}_volume", _num(leg.get("totalTradedVolume")) or 0.0)
            if not spot:
                spot = _num(leg.get("underlyingValue")) or 0.0
    return OptionChain(symbol=symbol, expiry=expiry, spot=spot,
                       rows=sorted(rows.values(), key=lambda r: r.strike), expiries=expiries)
