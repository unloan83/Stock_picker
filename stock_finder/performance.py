"""Weekly performance report: was each logged recommendation a hit or a fail?

Rules (one week is a short window for long-term calls, so those are an early read, not a final verdict):
- INTRADAY LONG/SHORT: HIT if the target was reached before the stop on that day (or it closed the day in profit);
  FAIL if the stop came first (or it closed at a loss). A bar touching both counts as the stop.
- LONG TERM: HIT if the stock beat NIFTY from that day's open to the latest close.
- NOT RECOMMENDED: HIT if the stock did not beat NIFTY over the same period (avoiding it was right).
- Option BUY: HIT if the premium is now higher than when recommended (at expiry: its intrinsic value).
- Option AVOID: HIT if the premium fell.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from .data import MarketData
from .engine import IST, LONG_TERM, NOT_RECOMMENDED, OPTION_AVOID, OPTION_BUY
from .options import find_row
from .store import Recommendation, RecommendationLog

log = logging.getLogger(__name__)

MARKET_CLOSE = time(15, 30)
BENCHMARK = "NIFTY"


@dataclass
class Outcome:
    rec: Recommendation
    check: str  # what was judged, e.g. "INTRADAY LONG", "LONG TERM", "BUY"
    hit: bool | None  # None: could not be judged yet
    detail: str


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# --- individual checks ----------------------------------------------------------------------------------------------

def judge_intraday(rec: Recommendation, bars: pd.DataFrame, now: datetime) -> Outcome:
    check = f"INTRADAY {rec.direction}"
    if rec.entry is None or rec.stop is None or rec.target is None:
        return Outcome(rec, check, None, "no plan was recorded")
    day = rec.ts.astimezone(IST).date()
    if day == now.date() and now.time() < MARKET_CLOSE:
        return Outcome(rec, check, None, "in progress (market still open)")
    # only bars that start at or after the call: earlier moves cannot count
    after = bars[(bars.index.date == day) & (bars.index >= rec.ts)] if len(bars) else bars
    if after.empty:
        return Outcome(rec, check, None, "no intraday prices after the call (made after market close?)")

    long = rec.direction == "LONG"
    for ts, bar in after.iterrows():
        hit_stop = bar["Low"] <= rec.stop if long else bar["High"] >= rec.stop
        hit_target = bar["High"] >= rec.target if long else bar["Low"] <= rec.target
        if hit_stop:
            loss = (rec.stop / rec.entry - 1) * (1 if long else -1)
            return Outcome(rec, check, False, f"stop {rec.stop:,.2f} hit at {ts:%H:%M} ({_pct(loss)})")
        if hit_target:
            gain = (rec.target / rec.entry - 1) * (1 if long else -1)
            return Outcome(rec, check, True, f"target {rec.target:,.2f} hit at {ts:%H:%M} ({_pct(gain)})")
    close = float(after["Close"].iloc[-1])
    ret = (close / rec.entry - 1) * (1 if long else -1)
    return Outcome(rec, check, ret > 0, f"neither stop nor target; closed {close:,.2f} ({_pct(ret)})")


def _return_since(daily: pd.DataFrame, day: date) -> tuple[float, float, float] | None:
    """(open on/after `day`, latest close, return) or None if there is no data from that day yet."""
    if daily.empty:
        return None
    since = daily[daily.index.date >= day]
    if since.empty:
        return None
    start = float(since["Open"].iloc[0])
    end = float(since["Close"].iloc[-1])
    return start, end, end / start - 1


def judge_vs_index(rec: Recommendation, check: str, daily: pd.DataFrame, index_daily: pd.DataFrame) -> Outcome:
    day = rec.ts.astimezone(IST).date()
    stock = _return_since(daily, day)
    index = _return_since(index_daily, day)
    if stock is None or index is None:
        return Outcome(rec, check, None, "no prices since the call yet")
    beat = stock[2] > index[2]
    hit = beat if check == LONG_TERM else not beat
    detail = f"{stock[0]:,.2f} → {stock[1]:,.2f} ({_pct(stock[2])}) vs {BENCHMARK} {_pct(index[2])}"
    return Outcome(rec, check, hit, detail)


def judge_option(rec: Recommendation, md: MarketData, now: datetime) -> Outcome:
    check = rec.verdict
    if rec.price is None or rec.strike is None or rec.option_type is None:
        return Outcome(rec, check, None, "premium was not recorded")
    expired = rec.expiry is not None and (rec.expiry < now.date()
                                          or (rec.expiry == now.date() and now.time() >= MARKET_CLOSE))
    try:
        if expired:
            daily = md.daily(rec.symbol)
            upto = daily[daily.index.date <= rec.expiry]
            if upto.empty:
                return Outcome(rec, check, None, "no underlying price at expiry")
            spot = float(upto["Close"].iloc[-1])
            value = max(0.0, spot - rec.strike) if rec.option_type == "CE" else max(0.0, rec.strike - spot)
            note = f" (expired; {rec.symbol} closed {spot:,.2f})"
        else:
            row = find_row(md.option_chain(rec.symbol, rec.expiry), rec.strike)
            ltp = (row.ce_ltp if rec.option_type == "CE" else row.pe_ltp) if row else None
            if ltp is None:
                return Outcome(rec, check, None, "current premium unavailable")
            value, note = ltp, ""
    except Exception as exc:
        log.warning("option check failed for %s: %s", rec.item, exc)
        return Outcome(rec, check, None, "option prices unavailable right now")
    change = value / rec.price - 1 if rec.price else 0.0
    hit = value > rec.price if rec.verdict == OPTION_BUY else value <= rec.price
    return Outcome(rec, check, hit, f"₹{rec.price:,.2f} → ₹{value:,.2f} ({_pct(change)}){note}")


def judge(rec: Recommendation, md: MarketData, now: datetime) -> list[Outcome]:
    """One outcome per call made (a 'LONG TERM + INTRADAY LONG' verdict gives two)."""
    if rec.kind == "option":
        return [judge_option(rec, md, now)] if rec.verdict in (OPTION_BUY, OPTION_AVOID) else []
    out: list[Outcome] = []
    try:
        if "INTRADAY" in rec.verdict:
            out.append(judge_intraday(rec, md.intraday(rec.symbol, period="1mo"), now))
        checks = [c for c in (LONG_TERM, NOT_RECOMMENDED) if c in rec.verdict]
        if checks:
            daily, index_daily = md.daily(rec.symbol), md.daily(BENCHMARK)
            out += [judge_vs_index(rec, c, daily, index_daily) for c in checks]
    except Exception as exc:
        log.warning("check failed for %s: %s", rec.item, exc)
        out.append(Outcome(rec, rec.verdict, None, "prices unavailable right now"))
    return out


# --- weekly report --------------------------------------------------------------------------------------------------

def week_start(now: datetime) -> datetime:
    monday = now.date() - timedelta(days=now.weekday())
    return datetime.combine(monday, time(0, 0), tzinfo=now.tzinfo)


def first_call_per_day(recs: list[Recommendation]) -> list[Recommendation]:
    """If the same item was sent several times in a day, judge the first (morning) call only."""
    seen: set[tuple[date, int | None, str]] = set()
    out = []
    for r in recs:
        key = (r.ts.astimezone(IST).date(), r.chat_id, r.item)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _group(check: str) -> str:
    if check.startswith("INTRADAY"):
        return "Intraday"
    return {LONG_TERM: "Long term", NOT_RECOMMENDED: "Not recommended",
            OPTION_BUY: "Option buy", OPTION_AVOID: "Option avoid"}.get(check, check)


def format_report(outcomes: list[Outcome], start: datetime, now: datetime) -> str:
    title = f"📅 Weekly report · {start:%a %d %b} – {now:%a %d %b %H:%M} IST"
    if not outcomes:
        return f"{title}\n\nNo recommendations were recorded this week."
    judged = [o for o in outcomes if o.hit is not None]
    hits = sum(1 for o in judged if o.hit)
    rate = f"{hits / len(judged):.0%}" if judged else "—"
    pending = len(outcomes) - len(judged)
    lines = [title, "",
             f"Result: {hits} hit · {len(judged) - hits} fail · hit rate {rate}"
             + (f" ({pending} not judged yet)" if pending else "")]
    groups: dict[str, list[Outcome]] = {}
    for o in judged:
        groups.setdefault(_group(o.check), []).append(o)
    if groups:
        lines.append("  " + " · ".join(f"{g} {sum(1 for o in os if o.hit)}/{len(os)}" for g, os in groups.items()))

    by_day: dict[date, list[Outcome]] = {}
    for o in outcomes:
        by_day.setdefault(o.rec.ts.astimezone(IST).date(), []).append(o)
    for day, items in sorted(by_day.items()):
        lines += ["", f"{day:%a %d %b}"]
        for o in items:
            mark = "⏳" if o.hit is None else "✅" if o.hit else "❌"
            lines.append(f"  {mark} {o.rec.item} · {o.check}: {o.detail}")
    lines += ["", "Hit = intraday target before stop · long term beat NIFTY · not recommended did not beat NIFTY · "
                  "option BUY premium rose / AVOID premium fell. One week is an early read for long-term calls."]
    return "\n".join(lines)


def weekly_report(log_store: RecommendationLog, md: MarketData, now: datetime,
                  chat_id: int | None = None) -> str:
    start = week_start(now)
    recs = first_call_per_day(log_store.between(start, now, chat_id))
    outcomes = [o for r in recs for o in judge(r, md, now)]
    return format_report(outcomes, start, now)


def todays_list(log_store: RecommendationLog, now: datetime, chat_id: int | None = None) -> str:
    start = datetime.combine(now.date(), time(0, 0), tzinfo=now.tzinfo)
    recs = first_call_per_day(log_store.between(start, now + timedelta(seconds=1), chat_id))
    if not recs:
        return f"📝 {now:%a %d %b}: no recommendations recorded yet today."
    lines = [f"📝 Recorded {now:%a %d %b} ({len(recs)})"]
    for r in recs:
        price = f" @ ₹{r.price:,.2f}" if r.price is not None else ""
        plan = f" · entry {r.entry:,.2f} stop {r.stop:,.2f} target {r.target:,.2f}" \
            if "INTRADAY" in r.verdict and r.entry else ""
        lines.append(f"  {r.ts.astimezone(IST):%H:%M} {r.item}{price}: {r.verdict}{plan}")
    return "\n".join(lines)
