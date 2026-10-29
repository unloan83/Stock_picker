"""Turn a free-text Telegram message into stock and option requests.

Accepted forms (case-insensitive, separated by commas or new lines; plain stocks may also be space-separated):
    RELIANCE, TCS INFY
    NIFTY 25000 CE
    BANKNIFTY 52000PE 27NOV
    RELIANCE 3000 CE 25-Nov
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


def parse_message(text: str) -> tuple[list[StockRequest | OptionRequest], list[str]]:
    """Return (requests in message order, unrecognised tokens)."""
    requests: list[StockRequest | OptionRequest] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[,\n;]+", text.upper()):
        chunk = " ".join(chunk.replace(".NS", "").split())
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
