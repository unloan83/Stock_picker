"""Telegram bot: send a list of NSE stocks/options, get a verdict with its drivers for each."""

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .data import FreeMarketData
from .engine import analyse_all, now_ist
from .formatter import format_reply
from .parser import parse_message
from .store import RecommendationLog

log = logging.getLogger(__name__)

HELP = (
    "Send NSE stocks and options, separated by commas or new lines:\n"
    "  RELIANCE, TCS, INFY\n"
    "  NIFTY 25000 CE\n"
    "  BANKNIFTY 52000 PE 27NOV   (expiry optional; nearest by default)\n\n"
    "Each stock gets LONG TERM / INTRADAY LONG|SHORT / NOT RECOMMENDED with its drivers.\n"
    "Each option gets BUY / AVOID for an option buyer.\n"
    "Intraday signals need market data after 09:15 IST; best used 09:30-10:00."
)
MAX_ITEMS = 25


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
    reports = await asyncio.to_thread(analyse_all, context.bot_data["md"], requests, at.date())
    try:
        context.bot_data["log"].record(at, message.chat_id, reports)
    except Exception:
        log.exception("could not log recommendations")
    for text in format_reply(reports, unknown, at):
        await message.reply_text(text)


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["allowed"] = allowed_chat_ids()
    app.bot_data["md"] = FreeMarketData()
    app.bot_data["log"] = RecommendationLog(os.environ.get("STOCK_FINDER_DB", "stock_finder.db"))
    app.add_handler(CommandHandler(["start", "help"], help_cmd))
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
