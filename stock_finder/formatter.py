"""Plain-text Telegram replies: verdict, the key drivers, then every analysis parameter with its value and target."""

import re
from datetime import datetime

from .engine import INTRADAY_MIN, LONG_TERM, LONG_TERM_MIN, NO_DATA, NOT_RECOMMENDED, OPTION_BUY, OPTION_MIN
from .models import Driver, OptionReport, Score, StockReport

MAX_MESSAGE = 4000
DISCLAIMER = (
    "✓ full points · ◐ partial · ✗ none/penalty · – no data (not scored)\n"
    "Rule-based signals from free, possibly delayed data. Not investment advice."
)


def _mark(d: Driver) -> str:
    if not d.scored:
        return "–"
    if d.points >= d.max_points:
        return "✓"
    return "◐" if d.points > 0 else "✗"


def _param(d: Driver) -> str:
    if not d.scored:
        return f"  – {d.name}: no data"
    pts = f"+{d.points:g}" if d.points > 0 else f"{d.points:g}"
    return f"  {_mark(d)} {d.name}: {d.value} (want {d.want}) {pts}/{d.max_points:g}"


def _price(x: float | None) -> str:
    return f"₹{x:,.2f}" if x is not None else "₹—"


def _parse_name(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    if " · " in raw:
        company_raw, sector = raw.split(" · ", 1)
    else:
        company_raw, sector = raw, None
    company = re.sub(
        r"(?i)\s+(?:Limited|Ltd\.?|Corporation|Corp\.?|Incorporated|Inc\.?)\s*$",
        "",
        company_raw.strip(),
    ).strip()
    if company.isupper() and len(company) > 3:
        company = company.title()
    return company or None, sector or None


def _critical_drivers(score: Score, max_pos: int = 4, max_neg: int = 1) -> list[Driver]:
    pos = score.top(True, max_pos)
    neg = score.top(False, max_neg)
    seen: set[str] = set()
    picked: list[Driver] = []
    for d in pos + neg:
        if d.name not in seen:
            seen.add(d.name)
            picked.append(d)
    return picked


def format_stock(r: StockReport) -> str:
    company, sector = _parse_name(r.name)
    title = f"{company} ({r.symbol})" if company and company != r.symbol else r.symbol
    if r.verdict == NO_DATA:
        return f"⚪ {title}: {NO_DATA} ({r.error})"

    icon = "❌" if r.verdict == NOT_RECOMMENDED else "✅"
    lines = [f"{icon} {title} {_price(r.price)}: {r.verdict}"]
    if sector:
        lines.append(f"  {sector}")

    if r.verdict == NOT_RECOMMENDED:
        scores = []
        if r.long_term:
            scores.append(f"Long-term {r.long_term.score:.0f}/100")
        if r.intraday:
            scores.append(f"Intraday {r.intraday.score:.0f}/100")
        if scores:
            lines.append(f"  {' · '.join(scores)} (need 65)")
        shortfalls: list[Driver] = []
        if r.long_term:
            shortfalls.extend(r.long_term.top(False, 2))
        if r.intraday:
            shortfalls.extend(r.intraday.top(False, 1))
        for d in shortfalls[:3]:
            lines.append(_param(d))
        lines += [f"  ⚠ {f}" for f in r.flags]
        return "\n".join(lines)

    if r.long_term:
        ok = LONG_TERM in r.verdict
        mark = "✅" if ok else "—"
        lines.append(f"📈 Long-term {r.long_term.score:.0f}/100 (need {LONG_TERM_MIN}) {mark}")
        if ok:
            for d in _critical_drivers(r.long_term, max_pos=4, max_neg=1):
                lines.append(_param(d))
        else:
            for d in r.long_term.top(False, 2):
                lines.append(_param(d))

    if r.intraday and r.intraday_plan:
        p = r.intraday_plan
        ok = r.intraday.score >= INTRADAY_MIN
        mark = "✅" if ok else "—"
        lines.append(f"⚡ Intraday {p.direction} {r.intraday.score:.0f}/100 (need {INTRADAY_MIN}) {mark}")
        if ok:
            for d in _critical_drivers(r.intraday, max_pos=4, max_neg=1):
                lines.append(_param(d))
            lines.append(f"  Plan {p.direction}: entry {p.entry:,.2f} · stop {p.stop:,.2f} · target {p.target:,.2f}")
        else:
            for d in r.intraday.top(False, 2):
                lines.append(_param(d))

    lines += [f"  ⚠ {f}" for f in r.flags]
    return "\n".join(lines)


def format_option(r: OptionReport) -> str:
    if r.verdict == NO_DATA:
        return f"⚪ {r.label}: {NO_DATA} ({r.error})"
    icon = "✅" if r.verdict == OPTION_BUY else "❌"
    exp = f"{r.expiry:%d %b}" if r.expiry else "—"
    lines = [f"{icon} {r.label} {exp} @ {_price(r.ltp)}: {r.verdict}"]
    facts = [f"spot {r.spot:,.2f}" if r.spot else None,
             f"IV {r.iv:.1f}%" if r.iv else None,
             f"PCR {r.pcr:.2f}" if r.pcr is not None else None,
             f"max pain {r.max_pain:g}" if r.max_pain is not None else None]
    lines.append("  " + " · ".join(f for f in facts if f))
    if r.score:
        lines.append(f"🎯 Option buy {r.score.score:.0f}/100 (need {OPTION_MIN})")
        if r.verdict == OPTION_BUY:
            for d in _critical_drivers(r.score, max_pos=4, max_neg=1):
                lines.append(_param(d))
        else:
            for d in r.score.top(False, 2):
                lines.append(_param(d))
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


def split_text(text: str) -> list[str]:
    """Split a long text on line boundaries into Telegram-sized messages."""
    messages, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > MAX_MESSAGE:
            messages.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        messages.append(current)
    return messages
