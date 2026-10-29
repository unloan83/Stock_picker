"""Telegram bot: send a list of NSE stocks/options, get a verdict with its drivers for each."""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .data import FreeMarketData
from .engine import IST, analyse_all, now_ist
from .formatter import format_reply, split_text
from .parser import parse_message
from .performance import todays_list, weekly_report
from .resolver import SymbolResolver
from .store import RecommendationLog

log = logging.getLogger(__name__)

HELP = (
    "Send NSE stocks (symbol or company name) and options, separated by commas or new lines:\n"
    "  RELIANCE, Tata Steel, hdfc bank\n"
    "  TCS INFY WIPRO   (exact symbols may share a line)\n"
    "  NIFTY 25000 CE\n"
    "  BANKNIFTY 52000 PE 27NOV   (expiry optional; nearest by default)\n\n"
    "Each stock gets LONG TERM / INTRADAY LONG|SHORT / NOT RECOMMENDED.\n"
    "Each option gets BUY / AVOID for an option buyer.\n"
    "Every reply lists each parameter: its value, the target and the points earned.\n"
    "Intraday signals need market data after 09:15 IST; best used 09:30-10:00.\n\n"
    "Every request is recorded. Commands:\n"
    "  /today   what was recorded today\n"
    "  /report  this week's hit/fail report (sent automatically every Saturday 10:00 IST)"
)
MAX_ITEMS = 25
REPORT_WEEKDAY = 5  # Saturday (Monday = 0)
REPORT_TIME = time(10, 0)


def allowed_chat_ids() -> set[int]:
    raw = os.environ.get("ALLOWED_CHAT_IDS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


def is_allowed(chat_id: int, allowed: set[int]) -> bool:
    return chat_id in allowed


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if chat is None or update.effective_message is None:
        return False
    if is_allowed(chat.id, context.bot_data["allowed"]):
        return True
    log.warning("rejected chat %s", chat.id)
    await update.effective_message.reply_text(
        f"Not authorised. Add this chat id to ALLOWED_CHAT_IDS: {chat.id}")
    return False


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guard(update, context):
        await update.effective_message.reply_text(HELP)  # type: ignore[union-attr]


async def _reply_long(update: Update, text: str) -> None:
    for part in split_text(text):
        await update.effective_message.reply_text(part)  # type: ignore[union-attr]


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.effective_message.reply_text("Checking this week's recommendations…")  # type: ignore[union-attr]
    text = await asyncio.to_thread(weekly_report, context.bot_data["log"], context.bot_data["md"], now_ist(),
                                   update.effective_chat.id)  # type: ignore[union-attr]
    await _reply_long(update, text)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    text = todays_list(context.bot_data["log"], now_ist(), update.effective_chat.id)  # type: ignore[union-attr]
    await _reply_long(update, text)


def next_report_time(now: datetime) -> datetime:
    """Next Saturday 10:00 IST strictly after `now`."""
    days = (REPORT_WEEKDAY - now.weekday()) % 7
    candidate = datetime.combine(now.date() + timedelta(days=days), REPORT_TIME, tzinfo=IST)
    return candidate if candidate > now else candidate + timedelta(days=7)


async def weekly_report_loop(app: Application) -> None:
    while True:
        when = next_report_time(now_ist())
        log.info("next weekly report at %s", when.isoformat())
        await asyncio.sleep(max(1.0, (when - now_ist()).total_seconds()))
        for chat_id in app.bot_data["allowed"]:
            try:
                text = await asyncio.to_thread(weekly_report, app.bot_data["log"], app.bot_data["md"], now_ist(),
                                               chat_id)
                for part in split_text(text):
                    await app.bot.send_message(chat_id, part)
            except Exception:
                log.exception("weekly report failed for chat %s", chat_id)


async def _post_init(app: Application) -> None:
    app.create_task(weekly_report_loop(app))


async def analyse_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    message = update.effective_message
    assert message is not None and message.text is not None
    requests, unknown = parse_message(message.text)
    if not requests:
        await message.reply_text("No symbols found.\n\n" + HELP)
        return
    if len(requests) > MAX_ITEMS:
        await message.reply_text(f"Analysing the first {MAX_ITEMS} of {len(requests)} items.")
        requests = requests[:MAX_ITEMS]

    await message.reply_text(f"Analysing {len(requests)} item(s)…")
    at = now_ist()
    reports = await asyncio.to_thread(analyse_all, context.bot_data["md"], context.bot_data["resolver"],
                                    requests, at.date())
    try:
        context.bot_data["log"].record(at, message.chat_id, reports)
    except Exception:
        log.exception("could not log recommendations")
    for text in format_reply(reports, unknown, at):
        await message.reply_text(text)


def build_app(token: str) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["allowed"] = allowed_chat_ids()
    app.bot_data["md"] = FreeMarketData()
    app.bot_data["resolver"] = SymbolResolver(os.environ.get("NSE_LIST_CACHE", "nse_equity_list.csv"))
    app.bot_data["log"] = RecommendationLog(os.environ.get("STOCK_FINDER_DB", "stock_finder.db"))
    app.add_handler(CommandHandler(["start", "help"], help_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyse_msg))
    return app


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not allowed_chat_ids():
        log.warning("ALLOWED_CHAT_IDS is empty: every chat will be refused (the reply shows its chat id)")
    build_app(token).run_polling(allowed_updates=Update.ALL_TYPES)
