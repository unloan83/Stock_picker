"""Entry point.

    python -m stock_finder bot                         # run the Telegram bot
    python -m stock_finder check "Tata Steel, NIFTY 25000 CE"   # one-off analysis in the terminal
    python -m stock_finder tv [--minutes N] [--no-send]   # record TV until 09:15 IST, build the shortlist
    python -m stock_finder tv-rebuild [YYYY-MM-DD]        # rebuild a day's shortlist from saved transcripts
"""

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in {"bot", "check", "tv", "tv-rebuild"}:
        print(__doc__)
        raise SystemExit(2)
    if args[0] == "bot":
        from .bot import run

        run()
        return
    if args[0] in {"tv", "tv-rebuild"}:
        _tv(args)
        return

    from .data import FreeMarketData
    from .engine import analyse_all, now_ist
    from .formatter import format_reply
    from .parser import parse_message
    from .resolver import SymbolResolver

    requests, unknown = parse_message(" ".join(args[1:]))
    at = now_ist()
    reports = analyse_all(FreeMarketData(), SymbolResolver("nse_equity_list.csv"), requests, at.date())
    for text in format_reply(reports, unknown, at):
        print(text)


def _tv(args: list[str]) -> None:
    import logging
    import os
    from datetime import date

    from .engine import now_ist
    from .resolver import SymbolResolver
    from .tv_run import build_from_transcripts, morning

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args[0] == "tv":
        minutes = float(args[args.index("--minutes") + 1]) if "--minutes" in args else None
        print(morning(minutes, send="--no-send" not in args))
        return
    day = date.fromisoformat(args[1]) if len(args) > 1 else now_ist().date()
    resolver = SymbolResolver(os.environ.get("NSE_LIST_CACHE", "nse_equity_list.csv"))
    text, _ = build_from_transcripts(day, resolver, os.environ.get("STOCK_FINDER_DB", "stock_finder.db"))
    print(text)


if __name__ == "__main__":
    main()
