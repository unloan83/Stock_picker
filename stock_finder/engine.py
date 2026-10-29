"""Runs the analyses for each requested item and decides the verdict."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .data import Fundamentals, MarketData
from .intraday import score_intraday
from .longterm import score_long_term
from .models import OptionReport, OptionRequest, StockReport, StockRequest
from .options import find_row, max_pain, pcr, score_option

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
LONG_TERM_MIN = 65
INTRADAY_MIN = 65
OPTION_MIN = 65
EARNINGS_WARN_DAYS = 7

LONG_TERM = "LONG TERM"
INTRADAY = "INTRADAY"
NOT_RECOMMENDED = "NOT RECOMMENDED"
OPTION_BUY = "BUY"
OPTION_AVOID = "AVOID"
NO_DATA = "NO DATA"


def now_ist() -> datetime:
    return datetime.now(IST)


def analyse_stock(md: MarketData, req: StockRequest, today: date) -> StockReport:
    try:
        daily = md.daily(req.symbol)
    except Exception as exc:
        log.warning("daily data failed for %s: %s", req.symbol, exc)
        return StockReport(req.symbol, None, NO_DATA, error="no price data: check the NSE symbol, e.g. HDFCBANK")
    if daily.empty:
        return StockReport(req.symbol, None, NO_DATA, error="unknown NSE symbol, e.g. use HDFCBANK")

    try:
        fund = md.fundamentals(req.symbol)
    except Exception as exc:
        log.warning("fundamentals failed for %s: %s", req.symbol, exc)
        fund = Fundamentals()
    lt = score_long_term(daily, fund)

    report = StockReport(req.symbol, float(daily["Close"].iloc[-1]), NOT_RECOMMENDED, long_term=lt)
    try:
        bars = md.intraday(req.symbol)
        intra = score_intraday(bars, daily, today)
        report.intraday, report.intraday_plan = intra.score, intra.plan
        if not bars.empty:
            report.price = float(bars["Close"].iloc[-1])
        if intra.note:
            report.flags.append(f"intraday: {intra.note}")
    except Exception as exc:
        log.warning("intraday data failed for %s: %s", req.symbol, exc)
        report.flags.append("intraday data unavailable")

    if fund.next_earnings and 0 <= (fund.next_earnings - today).days <= EARNINGS_WARN_DAYS:
        report.flags.append(f"results due {fund.next_earnings:%d %b}: expect a big move")

    verdicts = []
    if lt.score >= LONG_TERM_MIN:
        verdicts.append(LONG_TERM)
    if report.intraday and report.intraday.score >= INTRADAY_MIN and report.intraday_plan:
        verdicts.append(f"{INTRADAY} {report.intraday_plan.direction}")
    report.verdict = " + ".join(verdicts) if verdicts else NOT_RECOMMENDED
    return report


def _wanted_expiry(req: OptionRequest, today: date) -> date | None:
    if req.expiry_day is None or req.expiry_month is None:
        return None
    year = today.year + (1 if req.expiry_month < today.month else 0)
    try:
        return date(year, req.expiry_month, req.expiry_day)
    except ValueError:
        return None


def analyse_option(md: MarketData, req: OptionRequest, today: date) -> OptionReport:
    label = f"{req.underlying} {req.strike:g} {req.kind}"
    try:
        chain = md.option_chain(req.underlying, _wanted_expiry(req, today))
    except Exception as exc:
        log.warning("option chain failed for %s: %s", label, exc)
        return OptionReport(label, NO_DATA, error=f"NSE option chain unavailable ({exc})")
    row = find_row(chain, req.strike)
    if row is None:
        near = sorted(chain.rows, key=lambda r: abs(r.strike - req.strike))[:3]
        hint = ", ".join(f"{r.strike:g}" for r in near)
        return OptionReport(label, NO_DATA, expiry=chain.expiry, spot=chain.spot,
                            error=f"strike not listed for {chain.expiry:%d %b}; nearest: {hint}")
    try:
        daily = md.daily(req.underlying)
    except Exception as exc:
        log.warning("underlying data failed for %s: %s", req.underlying, exc)
        return OptionReport(label, NO_DATA, error="underlying price history unavailable")

    score = score_option(chain, row, req.kind, daily, today)
    ltp = row.ce_ltp if req.kind == "CE" else row.pe_ltp
    iv = row.ce_iv if req.kind == "CE" else row.pe_iv
    return OptionReport(
        label=label,
        verdict=OPTION_BUY if score.score >= OPTION_MIN else OPTION_AVOID,
        score=score,
        expiry=chain.expiry,
        ltp=ltp,
        spot=chain.spot,
        iv=iv,
        pcr=pcr(chain),
        max_pain=max_pain(chain),
    )


def analyse_all(md: MarketData, requests: list[StockRequest | OptionRequest],
                today: date | None = None) -> list[StockReport | OptionReport]:
    today = today or now_ist().date()

    def run(req: StockRequest | OptionRequest) -> StockReport | OptionReport:
        if isinstance(req, OptionRequest):
            return analyse_option(md, req, today)
        return analyse_stock(md, req, today)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(run, requests))
