"""Entry point.

    python -m stock_finder bot                         # run the Telegram bot
    python -m stock_finder check RELIANCE, NIFTY 25000 CE   # one-off analysis in the terminal
"""

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in {"bot", "check"}:
        print(__doc__)
        raise SystemExit(2)
    if args[0] == "bot":
        from .bot import run

        run()
        return

    from .data import FreeMarketData
    from .engine import analyse_all, now_ist
    from .formatter import format_reply
    from .parser import parse_message

    requests, unknown = parse_message(" ".join(args[1:]))
    at = now_ist()
    for text in format_reply(analyse_all(FreeMarketData(), requests, at.date()), unknown, at):
        print(text)


if __name__ == "__main__":
    main()
