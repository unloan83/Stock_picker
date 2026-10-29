"""Resolve whatever the user typed ("hdfc bank", "Tata Steel Ltd", "TATASTEEL") to an NSE symbol.

Order: index names -> common aliases -> exact NSE symbol -> company-name match against NSE's official equity list
(downloaded once a day) -> fuzzy match -> Yahoo Finance search restricted to NSE listings.
"""

import csv
import difflib
import io
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from .data import INDEX_YAHOO

log = logging.getLogger(__name__)

NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
LIST_MAX_AGE_SECONDS = 24 * 3600

# Common short names that do not match the official company name or symbol.
ALIASES = {
    "HDFC": "HDFCBANK",  # HDFC Ltd merged into HDFC Bank in 2023
    "SBI": "SBIN",
    "STATE BANK": "SBIN",
    "HUL": "HINDUNILVR",
    "L&T": "LT",
    "LARSEN": "LT",
    "AIRTEL": "BHARTIARTL",
    "MAHINDRA": "M&M",
    "KOTAK": "KOTAKBANK",
    "ULTRATECH": "ULTRACEMCO",
    "BANK NIFTY": "BANKNIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "FIN NIFTY": "FINNIFTY",
    "NIFTY 50": "NIFTY",
}

_SUFFIXES = re.compile(r"\b(LIMITED|LTD|CORPORATION|CORP|COMPANY|CO|INDIA|OF|THE|AND|&)\b")
_MISSPELT_SUFFIXES = ["LIMITED", "CORPORATION", "COMPANY"]  # e.g. "Limted" should be ignored too


def normalise_name(name: str) -> str:
    text = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    return " ".join(_SUFFIXES.sub(" ", text).split())


@dataclass
class Resolution:
    query: str
    symbol: str | None = None
    name: str | None = None
    suggestions: list[str] = field(default_factory=list)  # "SYMBOL (Company)" when ambiguous or not found


def _download_equity_list() -> dict[str, str]:
    resp = requests.get(NSE_EQUITY_LIST_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    rows = csv.DictReader(io.StringIO(resp.text))
    out = {}
    for row in rows:
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if row.get("SYMBOL") and row.get("NAME OF COMPANY"):
            out[row["SYMBOL"].upper()] = row["NAME OF COMPANY"]
    return out


def _yahoo_search(query: str) -> list[tuple[str, str]]:
    import yfinance as yf

    quotes = yf.Search(query, max_results=10, news_count=0).quotes
    return [(q["symbol"][:-3], q.get("longname") or q.get("shortname") or q["symbol"])
            for q in quotes if str(q.get("symbol", "")).endswith(".NS")]


class SymbolResolver:
    def __init__(self, cache_path: str | None = None,
                 list_loader: Callable[[], dict[str, str]] = _download_equity_list,
                 searcher: Callable[[str], list[tuple[str, str]]] = _yahoo_search) -> None:
        self._cache_path = cache_path
        self._loader = list_loader
        self._searcher = searcher
        self._list: dict[str, str] = {}
        self._loaded_at = 0.0
        self._norm: dict[str, str] = {}  # normalised name -> symbol

    # --- equity list --------------------------------------------------------------------------------------------

    def equity_list(self) -> dict[str, str]:
        if self._list and time.time() - self._loaded_at < LIST_MAX_AGE_SECONDS:
            return self._list
        data = self._read_cache()
        if not data:
            try:
                data = self._loader()
                self._write_cache(data)
            except Exception as exc:  # network or format problem: fall back to Yahoo search only
                log.warning("NSE equity list unavailable: %s", exc)
                data = self._read_cache(ignore_age=True) or self._list
        self._list = data
        self._loaded_at = time.time()
        self._norm = {normalise_name(n): s for s, n in data.items()}
        return data

    def _read_cache(self, ignore_age: bool = False) -> dict[str, str]:
        path = self._cache_path
        if not path or not os.path.exists(path):
            return {}
        if not ignore_age and time.time() - os.path.getmtime(path) > LIST_MAX_AGE_SECONDS:
            return {}
        with open(path, newline="", encoding="utf-8") as fh:
            return {r[0]: r[1] for r in csv.reader(fh) if len(r) == 2}

    def _write_cache(self, data: dict[str, str]) -> None:
        if not self._cache_path or not data:
            return
        with open(self._cache_path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(sorted(data.items()))

    def is_symbol(self, text: str) -> bool:
        text = text.upper()
        return text in INDEX_YAHOO or text in self.equity_list()

    # --- resolution ---------------------------------------------------------------------------------------------

    def resolve(self, query: str) -> Resolution:
        q = " ".join(query.upper().replace(".NS", "").split())
        if q in INDEX_YAHOO:
            return Resolution(query, q, q)
        listing = self.equity_list()
        if q in ALIASES:
            sym = ALIASES[q]
            return Resolution(query, sym, listing.get(sym, sym))
        compact = q.replace(" ", "")
        for candidate in (q, compact):
            if candidate in listing:
                return Resolution(query, candidate, listing[candidate])
            if candidate in INDEX_YAHOO:
                return Resolution(query, candidate, candidate)

        if listing:
            res = self._match_name(query, q)
            if res.symbol or res.suggestions:
                return res

        try:
            found = self._searcher(q)
        except Exception as exc:
            log.warning("Yahoo search failed for %r: %s", q, exc)
            found = []
        if found:
            sym, name = found[0]
            return Resolution(query, sym, name)
        return Resolution(query)

    def _match_name(self, query: str, q: str) -> Resolution:
        nq = " ".join(w for w in normalise_name(q).split()
                      if len(w) < 5 or not difflib.get_close_matches(w, _MISSPELT_SUFFIXES, n=1, cutoff=0.8))
        if not nq:
            return Resolution(query)
        if nq in self._norm:
            sym = self._norm[nq]
            return Resolution(query, sym, self._list[sym])

        words = nq.split()
        hits = [s for n, s in self._norm.items() if all(re.search(rf"\b{re.escape(w)}", n) for w in words)]
        starts = [s for s in hits if normalise_name(self._list[s]).startswith(nq)]
        pool = starts or hits
        if len(pool) == 1:
            return Resolution(query, pool[0], self._list[pool[0]])
        if len(pool) > 1:
            pool.sort(key=lambda s: len(self._list[s]))
            return Resolution(query, suggestions=[f"{s} ({self._list[s]})" for s in pool[:5]])

        close = difflib.get_close_matches(nq, list(self._norm), n=3, cutoff=0.75)
        close_syms = difflib.get_close_matches(q.replace(" ", ""), list(self._list), n=3, cutoff=0.8)
        if len(close) == 1 and not close_syms:
            sym = self._norm[close[0]]
            return Resolution(query, sym, self._list[sym])
        suggestions = [f"{self._norm[c]} ({self._list[self._norm[c]]})" for c in close]
        suggestions += [f"{s} ({self._list[s]})" for s in close_syms if s not in {self._norm[c] for c in close}]
        return Resolution(query, suggestions=suggestions[:5])
