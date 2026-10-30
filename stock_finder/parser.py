"""Turn a free-text Telegram message into stock and option requests.

Each comma / new-line separated item is one request; names are resolved to NSE symbols later. Examples:
    RELIANCE, Tata Steel, hdfc bank
    TCS INFY WIPRO                      (several exact NSE symbols on one line are split later)
    NIFTY 25000 CE
    Bank Nifty 52000PE 27NOV
    HDFC Bank 1800 CE 25-Nov
"""

import re

from .models import OptionRequest, StockRequest

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

_HORIZON_RE = re.compile(
    r"\s*\(\s*(?:SHORT|LONG|INTRADAY|SWING|TERM|BUY|SELL|HOLD|CALL|PUT|OPT|EQ|EQUITY|STOCK)[^)]*\)\s*",
    re.IGNORECASE,
)
_OPTION_RE = re.compile(
    r"^(?P<und>[A-Z][A-Z0-9&.'\- ]*?)\s*(?P<strike>\d+(?:\.\d+)?)\s*(?P<kind>CE|PE)"
    r"(?:\s+(?P<day>\d{1,2})[\s\-]?(?P<mon>[A-Z]{3})(?:[\s\-]?\d{2,4})?)?$"
)
_NAME_RE = re.compile(r"^[A-Z0-9&.'\-() ]{1,60}$")


def parse_message(text: str) -> tuple[list[StockRequest | OptionRequest], list[str]]:
    """Return (requests in message order, unrecognised items)."""
    requests: list[StockRequest | OptionRequest] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw_chunk in re.split(r"[,\n;]+", text.upper()):
        chunk = _HORIZON_RE.sub(" ", raw_chunk)
        chunk = " ".join(chunk.replace(".NS", "").split())
        if not chunk:
            continue
        m = _OPTION_RE.match(chunk)
        if m:
            mon = m.group("mon")
            if mon and mon not in MONTHS:
                unknown.append(chunk)
                continue
            req: StockRequest | OptionRequest = OptionRequest(
                underlying=m.group("und").strip(),
                strike=float(m.group("strike")),
                kind=m.group("kind"),
                expiry_day=int(m.group("day")) if m.group("day") else None,
                expiry_month=MONTHS[mon] if mon else None,
            )
        elif _NAME_RE.match(chunk) and any(c.isalpha() for c in chunk):
            req = StockRequest(chunk)
        else:
            unknown.append(chunk)
            continue
        key = repr(req)
        if key not in seen:
            seen.add(key)
            requests.append(req)
    return requests, unknown
