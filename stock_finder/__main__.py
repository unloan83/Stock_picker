"""Stock Finder CLI: bot, manual analysis or daily website-name collection."""
import logging
import os
import sys


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in {"bot", "check", "web"}:
        print("Usage: python -m stock_finder {bot | check STOCKS | web [--no-send]}")
        raise SystemExit(2)
    if args[0] == "bot":
        from .bot import run
        run()
        return
    if args[0] == "web":
        from .daily_picks import run
        logging.basicConfig(level=logging.INFO)
        print(run(send="--no-send" not in args))
        return

    from .data import FreeMarketData
    from .engine import analyse_all, now_ist
    from .formatter import format_reply
    from .parser import parse_message
    from .resolver import SymbolResolver

    requests, unknown = parse_message(" ".join(args[1:]))
    at = now_ist()
    reports = analyse_all(FreeMarketData(),
                          SymbolResolver(os.environ.get("NSE_LIST_CACHE", "nse_equity_list.csv")),
                          requests, at.date())
    for message in format_reply(reports, unknown, at):
        print(message)


if __name__ == "__main__":
    main()
