"""Read-only current/recent-weekday website and model smoke test (CI, no Telegram)."""
import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_finder.data import FreeMarketData
from stock_finder.engine import analyse_all
from stock_finder.models import StockRequest
from stock_finder.resolver import SymbolResolver
from stock_finder.mentions import shortlist
from stock_finder.web_picks import collect


def main():
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    checked_day = today
    while checked_day.weekday() >= 5:
        checked_day -= timedelta(days=1)
    # Fixture is a real NSE universe snapshot, not a simulated stock-pick feed.
    with (Path("tests/data/nse_equity_list.csv")).open(newline="", encoding="utf-8") as fh:
        listing = dict(csv.reader(fh))
    mentions, fetched, failures = collect(checked_day, listing)
    candidates = shortlist(mentions, listing)
    symbols = [it.symbol for it in candidates[:20]]
    print("LIVE_SMOKE_SESSION_DATE:", checked_day, flush=True)
    print("LIVE_SMOKE_SOURCES_REACHED:", len(fetched), "OF", len(fetched) + len(failures), flush=True)
    print("LIVE_SMOKE_UNIQUE_SYMBOLS:", len(candidates), flush=True)
    print("LIVE_SMOKE_FIRST_20:", ",".join(symbols) or "(none)", flush=True)
    print("LIVE_SMOKE_SOURCES_FAILED:", ",".join(failures) or "(none)", flush=True)
    if not symbols:
        print("LIVE_SMOKE_RESULT: NO PUBLISHED STOCK NAMES RETURNED", flush=True)
        return
    # No Telegram and no writes to the recommendation SQLite DB.
    resolver = SymbolResolver(None, list_loader=lambda: listing, searcher=lambda x: [])
    reports = analyse_all(FreeMarketData(), resolver, [StockRequest(s) for s in symbols[:3]], checked_day)
    for report in reports:
        print("MODEL_RESULT:", report.symbol, report.verdict,
              "long_term_score:", report.long_term.score if report.long_term else "N/A",
              "intraday_score:", report.intraday.score if report.intraday else "N/A",
              flush=True)


if __name__ == "__main__":
    main()
