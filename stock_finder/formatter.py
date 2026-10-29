"""Plain-text Telegram replies: verdict, the key drivers, then every analysis parameter with its value and target."""

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


def _why(score: Score) -> str:
    good = [d.text for d in score.top(True, 2)]
    bad = [d.text for d in score.top(False, 2)]
    parts = []
    if good:
        parts.append("strengths: " + ", ".join(good))
    if bad:
        parts.append("weaknesses: " + ", ".join(bad))
    return "  Why: " + "; ".join(parts) if parts else ""


def _section(title: str, score: Score) -> list[str]:
    lines = [title]
    why = _why(score)
    if why:
        lines.append(why)
    lines += [_param(d) for d in score.drivers]
    return lines


def _price(x: float | None) -> str:
    return f"₹{x:,.2f}" if x is not None else "₹—"


def format_stock(r: StockReport) -> str:
    if r.verdict == NO_DATA:
        return f"⚪ {r.symbol}: {NO_DATA} ({r.error})"
    icon = "❌" if r.verdict == NOT_RECOMMENDED else "✅"
    lines = [f"{icon} {r.symbol} {_price(r.price)}: {r.verdict}"]
    if r.name and r.name != r.symbol:
        lines.append(f"  {r.name}")
    if r.long_term:
        mark = "✅" if LONG_TERM in r.verdict else "—"
        lines += _section(f"📈 Long-term {r.long_term.score:.0f}/100 (need {LONG_TERM_MIN}) {mark}", r.long_term)
    if r.intraday and r.intraday_plan:
        p = r.intraday_plan
        ok = r.intraday.score >= INTRADAY_MIN
        lines += _section(f"⚡ Intraday {p.direction} {r.intraday.score:.0f}/100 (need {INTRADAY_MIN}) "
                          f"{'✅' if ok else '—'}", r.intraday)
        if ok:
            lines.append(f"  Plan {p.direction}: entry {p.entry:,.2f} · stop {p.stop:,.2f} · target {p.target:,.2f}")
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
        lines += _section(f"🎯 Option buy {r.score.score:.0f}/100 (need {OPTION_MIN})", r.score)
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
