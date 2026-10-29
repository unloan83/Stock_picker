"""Plain-text Telegram replies: a verdict line plus the few drivers behind it."""

from datetime import datetime

from .engine import INTRADAY, LONG_TERM, NO_DATA, NOT_RECOMMENDED, OPTION_BUY
from .models import OptionReport, Score, StockReport

MAX_MESSAGE = 4000
DISCLAIMER = "Rule-based signals from free, possibly delayed data. Not investment advice."


def _drivers(score: Score | None, pos: int, neg: int) -> list[str]:
    if score is None:
        return []
    lines = [f"  + {d.text}" for d in score.top(True, pos)]
    lines += [f"  − {d.text}" for d in score.top(False, neg)]
    return lines


def _price(x: float | None) -> str:
    return f"₹{x:,.2f}" if x is not None else "₹—"


def format_stock(r: StockReport) -> str:
    if r.verdict == NO_DATA:
        return f"⚪ {r.symbol}: {NO_DATA} ({r.error})"
    icon = "❌" if r.verdict == NOT_RECOMMENDED else "✅"
    lt = f"{r.long_term.score:.0f}" if r.long_term else "—"
    it = f"{r.intraday.score:.0f}" if r.intraday else "—"
    lines = [f"{icon} {r.symbol} {_price(r.price)}: {r.verdict}", f"  Long-term {lt}/100 · Intraday {it}/100"]

    if LONG_TERM in r.verdict:
        lines += _drivers(r.long_term, 3, 1)
    if INTRADAY in r.verdict and r.intraday_plan:
        p = r.intraday_plan
        lines += [line.replace("  ", "  intraday ", 1) for line in _drivers(r.intraday, 3, 1)]
        lines.append(f"  Plan {p.direction}: entry {p.entry:,.2f} · stop {p.stop:,.2f} · target {p.target:,.2f}")
    if r.verdict == NOT_RECOMMENDED:
        lines += _drivers(r.long_term, 1, 2)
        if r.intraday:
            lines += [f"  intraday − {d.text}" for d in r.intraday.top(False, 1)]
    lines += [f"  ⚠ {f}" for f in r.flags]
    return "\n".join(lines)


def format_option(r: OptionReport) -> str:
    if r.verdict == NO_DATA:
        return f"⚪ {r.label}: {NO_DATA} ({r.error})"
    icon = "✅" if r.verdict == OPTION_BUY else "❌"
    exp = f"{r.expiry:%d %b}" if r.expiry else "—"
    lines = [f"{icon} {r.label} {exp} @ {_price(r.ltp)}: {r.verdict} ({r.score.score:.0f}/100)" if r.score
             else f"{icon} {r.label}: {r.verdict}"]
    facts = [f"spot {r.spot:,.2f}" if r.spot else None,
             f"IV {r.iv:.1f}%" if r.iv else None,
             f"PCR {r.pcr:.2f}" if r.pcr is not None else None,
             f"max pain {r.max_pain:g}" if r.max_pain is not None else None]
    lines.append("  " + " · ".join(f for f in facts if f))
    lines += _drivers(r.score, 3, 2)
    lines += [f"  ⚠ {f}" for f in r.flags]
    return "\n".join(lines)


def format_reply(reports: list[StockReport | OptionReport], unknown: list[str], at: datetime) -> list[str]:
    """Return one or more Telegram messages (split under the 4096-char limit)."""
    blocks = [f"📊 Stock Finder · {at:%d %b %H:%M} IST"]
    for r in reports:
        blocks.append(format_option(r) if isinstance(r, OptionReport) else format_stock(r))
    if unknown:
        blocks.append("❓ Not understood: " + ", ".join(unknown))
    blocks.append(DISCLAIMER)

    messages, current = [], ""
    for b in blocks:
        if current and len(current) + len(b) + 2 > MAX_MESSAGE:
            messages.append(current)
            current = ""
        current = f"{current}\n\n{b}" if current else b
    if current:
        messages.append(current)
    return messages

