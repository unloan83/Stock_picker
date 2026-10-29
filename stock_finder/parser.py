"""Turn a free-text Telegram message into stock and option requests.

Accepted forms (case-insensitive, separated by commas or new lines; plain stocks may also be space-separated):
    RELIANCE, TCS INFY
    NIFTY 25000 CE
    BANKNIFTY 52000PE 27NOV
    RELIANCE 3000 CE 25-Nov
    HDFC BANK, State Bank of India      (common names are mapped to NSE symbols)
"""

import re

from .models import OptionRequest, StockRequest

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

_OPTION_RE = re.compile(
    r"^(?P<und>[A-Z0-9&\-]+?)\s*(?P<strike>\d+(?:\.\d+)?)\s*(?P<kind>CE|PE)"
    r"(?:\s+(?P<day>\d{1,2})[\s\-]?(?P<mon>[A-Z]{3})(?:[\s\-]?\d{2,4})?)?$"
)
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")

# Common company names (often typed with spaces) -> NSE trading symbols.
ALIASES = {
    "HDFC": "HDFCBANK",  # HDFC Ltd merged into HDFC Bank in 2023
    "HDFC BANK": "HDFCBANK",
    "HDFC LIFE": "HDFCLIFE",
    "ICICI BANK": "ICICIBANK",
    "AXIS BANK": "AXISBANK",
    "KOTAK": "KOTAKBANK",
    "KOTAK BANK": "KOTAKBANK",
    "KOTAK MAHINDRA BANK": "KOTAKBANK",
    "INDUSIND BANK": "INDUSINDBK",
    "SBI": "SBIN",
    "STATE BANK": "SBIN",
    "STATE BANK OF INDIA": "SBIN",
    "SBI LIFE": "SBILIFE",
    "BANK OF BARODA": "BANKBARODA",
    "PUNJAB NATIONAL BANK": "PNB",
    "BAJAJ FINANCE": "BAJFINANCE",
    "BAJAJ FINSERV": "BAJAJFINSV",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "TATA STEEL": "TATASTEEL",
    "TATA POWER": "TATAPOWER",
    "TATA CONSUMER": "TATACONSUM",
    "ASIAN PAINTS": "ASIANPAINT",
    "SUN PHARMA": "SUNPHARMA",
    "DR REDDY": "DRREDDY",
    "DR REDDYS": "DRREDDY",
    "HUL": "HINDUNILVR",
    "HINDUSTAN UNILEVER": "HINDUNILVR",
    "L&T": "LT",
    "LARSEN": "LT",
    "MARUTI SUZUKI": "MARUTI",
    "MAHINDRA": "M&M",
    "AIRTEL": "BHARTIARTL",
    "BHARTI AIRTEL": "BHARTIARTL",
    "ADANI ENTERPRISES": "ADANIENT",
    "ADANI PORTS": "ADANIPORTS",
    "ULTRATECH": "ULTRACEMCO",
    "ULTRATECH CEMENT": "ULTRACEMCO",
    "TECH MAHINDRA": "TECHM",
    "HCL TECH": "HCLTECH",
    "POWER GRID": "POWERGRID",
    "COAL INDIA": "COALINDIA",
    "JSW STEEL": "JSWSTEEL",
    "NESTLE": "NESTLEIND",
    "NESTLE INDIA": "NESTLEIND",
    "TITAN COMPANY": "TITAN",
    "BANK NIFTY": "BANKNIFTY",
    "FIN NIFTY": "FINNIFTY",
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}
_MAX_ALIAS_WORDS = max(len(k.split()) for k in ALIASES)


def _apply_aliases(chunk: str) -> str:
    """Replace known company names with NSE symbols, longest match first."""
    words, out, i = chunk.split(), [], 0
    while i < len(words):
        for n in range(min(_MAX_ALIAS_WORDS, len(words) - i), 0, -1):
            phrase = " ".join(words[i:i + n])
            if phrase in ALIASES:
                out.append(ALIASES[phrase])
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def parse_message(text: str) -> tuple[list[StockRequest | OptionRequest], list[str]]:
    """Return (requests in message order, unrecognised tokens)."""
    requests: list[StockRequest | OptionRequest] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[,\n;]+", text.upper()):
        chunk = _apply_aliases(" ".join(chunk.replace(".NS", "").split()))
        if not chunk:
            continue
        m = _OPTION_RE.match(chunk)
        if m:
            mon = m.group("mon")
            if mon and mon not in MONTHS:
                unknown.append(chunk)
                continue
            req = OptionRequest(
                underlying=m.group("und"),
                strike=float(m.group("strike")),
                kind=m.group("kind"),
                expiry_day=int(m.group("day")) if m.group("day") else None,
                expiry_month=MONTHS[mon] if mon else None,
            )
            key = f"{req.underlying}|{req.strike}|{req.kind}|{req.expiry_day}|{req.expiry_month}"
            if key not in seen:
                seen.add(key)
                requests.append(req)
            continue
        for token in chunk.split():
            if _SYMBOL_RE.match(token) and not token.isdigit():
                if token not in seen:
                    seen.add(token)
                    requests.append(StockRequest(token))
            else:
                unknown.append(token)
    return requests, unknown
