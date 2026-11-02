"""Morning job: record the channels until 09:15 IST, build the shortlist, store it and send it to Telegram."""

import glob
import logging
import os
from datetime import date, datetime, time, timedelta

import requests

from .data import FreeMarketData, MarketData
from .engine import IST, analyse_all, now_ist
from .formatter import format_reply, split_text
from .models import StockRequest
from .resolver import SymbolResolver
from .store import RecommendationLog
from .tv import (
    ShortlistItem,
    TvLog,
    build_index,
    extract_mentions,
    format_shortlist,
    read_transcript,
    shortlist,
    write_transcript,
)

log = logging.getLogger(__name__)

END_TIME = time(9, 15)
TRANSCRIBE_GRACE = timedelta(minutes=3)  # stop transcribing at 09:18 so verdicts reach Telegram by about 09:20
MAX_VERDICTS = 25  # same cap as a message to the bot


def transcript_dir(day: date) -> str:
    return os.path.join(os.environ.get("TV_DIR", "tv_transcripts"), day.isoformat())


def build_from_transcripts(day: date, resolver: SymbolResolver,
                           db_path: str) -> tuple[str, int, list[ShortlistItem]]:
    """Re-read the day's transcript files, store the mentions; returns (shortlist text, mention count, items)."""
    listing = resolver.equity_list()
    index = build_index(listing)
    folder = transcript_dir(day)
    channels, mentions = [], []
    for path in sorted(glob.glob(os.path.join(folder, "*.txt"))):
        channel = os.path.splitext(os.path.basename(path))[0]
        channels.append(channel)
        for seg in read_transcript(path, channel):
            mentions += extract_mentions(seg, index)
    TvLog(db_path).replace_day(day, mentions)
    items = shortlist(mentions, listing)
    return format_shortlist(items, day, channels), len(mentions), items


def _chat_ids() -> list[int]:
    return [int(c) for c in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if c]


def verdicts(items: list[ShortlistItem], md: MarketData, resolver: SymbolResolver, at: datetime, db_path: str,
             chat_ids: list[int]) -> list[str]:
    """Run the stocks TV called BUY/SELL through the bot's own engine, log them like a request, return the replies."""
    called = [it.symbol for it in items if it.call != "MENTION"][:MAX_VERDICTS]
    if not called:
        return []
    reports = analyse_all(md, resolver, [StockRequest(sym) for sym in called], at.date())
    rec_log = RecommendationLog(db_path)
    for chat_id in chat_ids or [None]:
        try:
            rec_log.record(at, chat_id, reports)
        except Exception:
            log.exception("could not log TV verdicts")
    return format_reply(reports, [], at)


def send_telegram(messages: list[str]) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chats = _chat_ids()
    if not token or not chats:
        log.info("Telegram not configured; shortlist printed only")
        return
    for chat in chats:
        for part in (p for text in messages for p in split_text(text)):
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat, "text": part}, timeout=20)
            if not resp.ok:
                log.warning("Telegram send to %s failed: HTTP %s", chat, resp.status_code)


def morning(minutes: float | None = None, send: bool = True) -> str:
    from .tv_capture import capture_all, channels_from_env, check_tools

    missing = check_tools()
    if missing:
        raise SystemExit("Cannot record: " + "; ".join(missing))
    start = now_ist()
    if minutes is None:
        end = datetime.combine(start.date(), END_TIME, tzinfo=IST)
        minutes = (end - start).total_seconds() / 60
    if minutes < 1:
        raise SystemExit("Nothing to record: it is already past 09:15 IST (use --minutes to force)")
    day = start.date()
    folder = transcript_dir(day)
    os.makedirs(folder, exist_ok=True)
    channels = channels_from_env()
    log.info("recording %d channel(s) for %.0f min into %s", len(channels), minutes, folder)
    deadline = start + timedelta(minutes=minutes) + TRANSCRIBE_GRACE
    results = capture_all(channels, start, int(minutes * 60), os.path.join(folder, "audio"),
                          os.environ.get("TV_WHISPER_MODEL", "base"), deadline)
    for name, (segments, _) in results.items():
        write_transcript(os.path.join(folder, f"{name}.txt"), segments)
    db = os.environ.get("STOCK_FINDER_DB", "stock_finder.db")
    resolver = SymbolResolver(os.environ.get("NSE_LIST_CACHE", "nse_equity_list.csv"))
    text, count, items = build_from_transcripts(day, resolver, db)
    silent = [n for n, (segs, _) in results.items() if not segs]
    if silent:
        text += "\n⚠️ No audio/transcript from: " + ", ".join(silent)
    late = {n: k for n, (_, k) in results.items() if k}
    if late:
        text += "\n⚠️ Not transcribed in time (2-min chunks): " + ", ".join(f"{n} {k}" for n, k in late.items())
    text += f"\nTranscribed by {now_ist():%H:%M} IST · {count} mention(s)"
    try:
        replies = verdicts(items, FreeMarketData(), resolver, now_ist(), db, _chat_ids())
    except Exception as exc:
        log.exception("verdicts failed")
        replies = [f"⚠️ Verdicts failed: {exc}"]
    messages = [text, *replies]
    if send:
        send_telegram(messages)
    return "\n\n".join(messages)


def previous_text(day: date, db_path: str, resolver: SymbolResolver) -> str:
    """Shortlist stored for `day` (used by the bot's /tv command)."""
    mentions = TvLog(db_path).day(day)
    if not mentions:
        return f"No TV shortlist recorded for {day:%d %b %Y}."
    channels = sorted({m.channel for m in mentions})
    return format_shortlist(shortlist(mentions, resolver.equity_list()), day, channels, include_mentions=True)

