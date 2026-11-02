"""Morning TV shortlist: find the stocks named on business TV and whether they were called BUY or SELL.

Input is transcript text (see `tv_capture.py` for recording and transcribing the live streams). Hindi channels are
translated to English by Whisper, but Devanagari call words that survive (खरीद, बेच, टारगेट, स्टॉप लॉस) are mapped too.

The shortlist is a record of what was said, not a recommendation. Its value is unknown until enough days are logged
and checked against prices.
"""

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from .data import INDEX_YAHOO
from .resolver import ALIASES, normalise_name

# Names TV anchors use that do not match the official company name.
TV_ALIASES = {
    **{k: v for k, v in ALIASES.items() if v not in INDEX_YAHOO},
    "RELIANCE": "RELIANCE",
    "RIL": "RELIANCE",
    "INFOSYS": "INFY",
    "MARUTI": "MARUTI",
    "ZOMATO": "ETERNAL",
    "ETERNAL": "ETERNAL",
    "PAYTM": "PAYTM",
    "NYKAA": "NYKAA",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "M AND M": "M&M",
    "M&M": "M&M",
    "ICICI": "ICICIBANK",
    "AXIS": "AXISBANK",
    "INDUSIND": "INDUSINDBK",
    "HERO MOTO": "HEROMOTOCO",
    "DR REDDY": "DRREDDY",
    "DR REDDYS": "DRREDDY",
    "SUN PHARMA": "SUNPHARMA",
    "ASIAN PAINTS": "ASIANPAINT",
    "ADANI ENT": "ADANIENT",
    "ADANI PORTS": "ADANIPORTS",
    "VODAFONE IDEA": "IDEA",
    "VODA IDEA": "IDEA",
}
# Short symbols that anchors say as words; other symbols under 4 letters are too likely to be ordinary words.
SHORT_SYMBOLS = {"ITC", "TCS", "HAL", "BEL", "LIC", "PNB", "IOC", "BPCL", "HPCL", "GAIL", "SAIL", "ONGC", "NTPC",
                 "BHEL", "NMDC", "IRFC", "RVNL", "IREDA", "IRCTC", "LT", "ABB", "DLF", "SRF", "UPL", "MRF", "ACC",
                 "PVR", "TVS"}
# NSE symbols that are also common English or anchor words; never matched as bare symbols.
STOP_SYMBOLS = {"IDEA", "GOLD", "HOME", "BEST", "TIPS", "MOTOR", "ORIENT", "LINDE", "ALL", "ONE", "CASH", "BANK",
                "POWER", "PLUS", "GOOD", "GLOBAL", "PRIME", "SMART", "FOCUS", "TODAY", "TREND", "VISION", "UNITY",
                "MASTER", "SUPER", "MARKET", "STAR", "HIGH", "SHARE", "PRICE", "TARGET", "STOCK", "LONG", "SHORT",
                "BUY", "SELL", "TRADE", "TRADING", "INDEX", "FUTURE", "OPTION", "CALL", "PUT", "OPEN", "CLOSE",
                "BASE", "SAFE", "GREEN", "SILVER", "CEMENT", "STEEL", "SUGAR", "TATA", "BAJAJ", "ADANI", "BIRLA",
                "MAX", "NEXT", "BLUE", "ROYAL", "FUSION", "NATIONAL", "CAPITAL", "TRUST", "FIRST", "UNIVERSAL",
                "GIFT", "NIFTY", "SENSEX", "RBI", "SEBI", "FED", "US", "DOLLAR", "RUPEE", "CRUDE", "MONDAY", "TUESDAY",
                "WEDNESDAY", "THURSDAY", "FRIDAY", "MORNING", "NEWS", "PRIVATE", "PUBLIC"}

BUY_WORDS = {"BUY", "BUYING", "ACCUMULATE", "ADD", "BULLISH", "OUTPERFORM", "UPGRADE", "KHARID", "KHAREED",
             "KHARIDARI", "KHAREEDARI", "KHARIDEN", "KHAREEDEN", "KHARIDNA"}
SELL_WORDS = {"SELL", "SELLING", "SHORT", "EXIT", "BEARISH", "AVOID", "UNDERPERFORM", "DOWNGRADE", "BECH", "BECHO",
              "BECHEN", "BIKWALI"}
TARGET_WORDS = {"TARGET", "TARGETS", "LAKSHYA"}
STOP_WORDS = {"STOPLOSS", "SL"}
# Words dropped from the token stream so names match however they are said ("Tata Steel Limited" == "Tata Steel").
_FILLER = {"LIMITED", "LTD", "CORPORATION", "CORP", "COMPANY", "CO", "INDIA", "OF", "THE", "AND"}
_DEVANAGARI = [("स्टॉप लॉस", " STOPLOSS "), ("स्टॉपलॉस", " STOPLOSS "), ("स्टॉप लॉस", " STOPLOSS "),
               ("खरीदारी", " BUY "), ("खरीद", " BUY "), ("बिकवाली", " SELL "), ("बेच", " SELL "),
               ("टारगेट", " TARGET "), ("लक्ष्य", " TARGET ")]
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MAX_PHRASE = 5
CALL_WINDOW = 25  # tokens after a mention searched for a call word, target or stop
CALL_LOOKBACK = 6  # tokens before a mention searched for a call word ("buy Tata Steel")
NUMBER_WINDOW = 5  # tokens after TARGET / STOPLOSS searched for the number


@dataclass
class Segment:
    channel: str
    at: datetime
    text: str


@dataclass
class Mention:
    channel: str
    at: datetime
    symbol: str
    action: str  # BUY / SELL / MENTION
    target: float | None = None
    stop: float | None = None
    snippet: str = ""


@dataclass
class ShortlistItem:
    symbol: str
    name: str
    call: str  # BUY / SELL / MIXED / MENTION
    channels: list[str] = field(default_factory=list)
    buys: int = 0
    sells: int = 0
    mentions: int = 0
    targets: list[float] = field(default_factory=list)
    stops: list[float] = field(default_factory=list)
    first_at: datetime | None = None


def build_index(listing: dict[str, str]) -> dict[tuple[str, ...], str]:
    """Phrase (as tokens) -> NSE symbol, from the official equity list plus TV aliases."""
    index: dict[tuple[str, ...], str] = {}
    for symbol, name in listing.items():
        words = tuple(w for w in normalise_name(name).split() if w not in _FILLER)
        if len(words) > 1 or (words and len(words[0]) >= 4 and words[0] not in STOP_SYMBOLS):
            index.setdefault(words, symbol)
    for symbol in listing:
        if symbol.isalpha() and symbol not in STOP_SYMBOLS and (len(symbol) >= 4 or symbol in SHORT_SYMBOLS):
            index.setdefault((symbol,), symbol)
    for alias, symbol in TV_ALIASES.items():
        if symbol in listing:
            index[tuple(_tokens(alias))] = symbol
    for symbol in SHORT_SYMBOLS & set(listing):
        index[(symbol,)] = symbol
    return index


def _tokens(text: str) -> list[str]:
    return [t for t, _ in _cased_tokens(text)]


def _cased_tokens(text: str) -> list[tuple[str, bool]]:
    """Upper-cased tokens, each with a flag that is True when the transcript wrote it in lower case."""
    for dev, eng in _DEVANAGARI:
        text = text.replace(dev, eng)
    text = text.translate(_DEV_DIGITS)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 1,250 -> 1250
    text = re.sub(r"\bstop\s*-?\s*loss\b", "STOPLOSS", text, flags=re.I)
    text = re.sub(r"\bM\s*&\s*M\b", "M&M", text, flags=re.I)
    out = []
    for tok in re.findall(r"[A-Za-z0-9&]+(?:\.\d+)?", text):
        up = tok.upper()
        if up not in _FILLER:
            out.append((up, tok.islower()))
    return out


def _number(tokens: list[str], start: int) -> float | None:
    for tok in tokens[start:start + NUMBER_WINDOW]:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            value = float(tok)
            return value if value > 0 else None
    return None


def _nearest_call(before: list[str], after: list[str]) -> str:
    """The call word closest to the stock name wins; BUY and SELL equally close (or none) is just a mention."""
    found: dict[str, int] = {}
    for dist, tok in [(len(before) - j, t) for j, t in enumerate(before)] + [(j + 1, t) for j, t in enumerate(after)]:
        for action, words in (("BUY", BUY_WORDS), ("SELL", SELL_WORDS)):
            if tok in words:
                found[action] = min(found.get(action, dist), dist)
    if len(found) == 1:
        return next(iter(found))
    if len(found) == 2 and found["BUY"] != found["SELL"]:
        return min(found, key=found.__getitem__)
    return "MENTION"


def extract_mentions(segment: Segment, index: dict[tuple[str, ...], str]) -> list[Mention]:
    """Every stock named in the segment, with the call made about it (if any)."""
    cased = _cased_tokens(segment.text)
    tokens = [t for t, _ in cased]
    hits: list[tuple[int, int, str]] = []  # (start, end, symbol)
    i = 0
    while i < len(tokens):
        for n in range(min(MAX_PHRASE, len(tokens) - i), 0, -1):
            sym = index.get(tuple(tokens[i:i + n]))
            # A one-word match written in lower case ("crude", "news") is an ordinary word, not a company name.
            if sym and not (n == 1 and cased[i][1]):
                hits.append((i, i + n, sym))
                i += n
                break
        else:
            i += 1

    mentions = []
    for k, (start, end, sym) in enumerate(hits):
        prev_end = hits[k - 1][1] if k else 0
        next_start = hits[k + 1][0] if k + 1 < len(hits) else len(tokens)
        after = tokens[end:min(next_start, end + CALL_WINDOW)]
        before = tokens[max(prev_end, start - CALL_LOOKBACK):start]
        action = _nearest_call(before, after)
        target = stop = None
        for j, tok in enumerate(after):
            if tok in TARGET_WORDS and target is None:
                target = _number(after, j + 1)
            elif tok in STOP_WORDS and stop is None:
                stop = _number(after, j + 1)
        snippet = " ".join(tokens[max(0, start - 3):min(len(tokens), end + 15)])
        mentions.append(Mention(segment.channel, segment.at, sym, action, target, stop, snippet))
    return mentions


def shortlist(mentions: list[Mention], listing: dict[str, str]) -> list[ShortlistItem]:
    """One row per stock, most-called first: stocks called on more channels rank above single-channel ones."""
    items: dict[str, ShortlistItem] = {}
    called_on: dict[str, set[str]] = defaultdict(set)
    for m in sorted(mentions, key=lambda m: m.at):
        it = items.setdefault(m.symbol, ShortlistItem(m.symbol, listing.get(m.symbol, m.symbol), "MENTION",
                                                      first_at=m.at))
        if m.channel not in it.channels:
            it.channels.append(m.channel)
        if m.action == "BUY":
            it.buys += 1
        elif m.action == "SELL":
            it.sells += 1
        else:
            it.mentions += 1
        if m.action != "MENTION":
            called_on[m.symbol].add(m.channel)
        if m.target is not None:
            it.targets.append(m.target)
        if m.stop is not None:
            it.stops.append(m.stop)
    for it in items.values():
        if it.buys or it.sells:
            it.call = "BUY" if it.buys > it.sells else "SELL" if it.sells > it.buys else "MIXED"
    return sorted(items.values(), key=lambda it: (-len(called_on[it.symbol]), -(it.buys + it.sells),
                                                  -len(it.channels), -it.mentions, it.symbol))


def format_shortlist(items: list[ShortlistItem], day: date, channels: list[str], include_mentions: bool = False) -> str:
    calls = [it for it in items if it.call != "MENTION"]
    lines = [f"📺 TV shortlist {day:%d %b %Y} (08:30-09:15)", f"Channels: {', '.join(channels) or 'none'}"]
    if not calls:
        lines.append("No BUY/SELL calls found.")
    icon = {"BUY": "🟢", "SELL": "🔴", "MIXED": "🟡"}
    for it in calls:
        extra = []
        if it.targets:
            extra.append("target " + "/".join(f"{t:g}" for t in sorted(set(it.targets))))
        if it.stops:
            extra.append("SL " + "/".join(f"{s:g}" for s in sorted(set(it.stops))))
        tail = f" · {', '.join(extra)}" if extra else ""
        lines.append(f"{icon[it.call]} {it.symbol} {it.call} ({it.buys}B/{it.sells}S) on {len(it.channels)} ch: "
                     f"{', '.join(it.channels)}{tail}")
    rest = [it for it in items if it.call == "MENTION"]
    if rest:
        if include_mentions:
            lines.append("Mentioned without a call: " + ", ".join(it.symbol for it in rest))
        else:
            lines.append(f"{len(rest)} more stock(s) mentioned without a call.")
    lines.append("What TV said, not a recommendation.")
    return "\n".join(lines)


# --- storage ------------------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS tv_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    ts TEXT NOT NULL,
    channel TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    target REAL,
    stop REAL,
    snippet TEXT
)
"""


class TvLog:
    def __init__(self, path: str) -> None:
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(SCHEMA)

    def replace_day(self, day: date, mentions: list[Mention]) -> None:
        """Store one day's mentions, replacing any earlier run for that day."""
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM tv_mentions WHERE day = ?", (day.isoformat(),))
            db.executemany(
                "INSERT INTO tv_mentions (day, ts, channel, symbol, action, target, stop, snippet) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(day.isoformat(), m.at.isoformat(), m.channel, m.symbol, m.action, m.target, m.stop, m.snippet)
                 for m in mentions])

    def day(self, day: date) -> list[Mention]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT ts, channel, symbol, action, target, stop, snippet FROM tv_mentions "
                              "WHERE day = ? ORDER BY ts, id", (day.isoformat(),)).fetchall()
        return [Mention(ch, datetime.fromisoformat(ts), sym, act, tgt, sl, snip or "")
                for ts, ch, sym, act, tgt, sl, snip in rows]


# --- transcript files ---------------------------------------------------------------------------------------------

def write_transcript(path: str, segments: list[Segment]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for s in segments:
            fh.write(f"{s.at.isoformat()}\t{s.text.strip()}\n")


def read_transcript(path: str, channel: str) -> list[Segment]:
    """Lines of "ISO-timestamp<TAB>text", as written by `write_transcript`."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            ts, _, text = line.rstrip("\n").partition("\t")
            if text:
                out.append(Segment(channel, datetime.fromisoformat(ts), text))
    return out
