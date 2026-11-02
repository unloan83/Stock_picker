"""Morning job: record the channels until 09:15 IST, build the shortlist, store it and send it to Telegram."""

import glob
import logging
import os
from datetime import date, datetime, time

import requests

from .engine import IST, now_ist
from .formatter import split_text
from .resolver import SymbolResolver
from .tv import TvLog, build_index, extract_mentions, format_shortlist, read_transcript, shortlist, write_transcript

log = logging.getLogger(__name__)

END_TIME = time(9, 15)


def transcript_dir(day: date) -> str:
    return os.path.join(os.environ.get("TV_DIR", "tv_transcripts"), day.isoformat())


def build_from_transcripts(day: date, resolver: SymbolResolver, db_path: str) -> tuple[str, int]:
    """Re-read the day's transcript files, store the mentions and return (shortlist text, mention count)."""
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
    return format_shortlist(shortlist(mentions, listing), day, channels), len(mentions)


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chats = [c for c in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if c]
    if not token or not chats:
        log.info("Telegram not configured; shortlist printed only")
        return
    for chat in chats:
        for part in split_text(text):
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": int(chat), "text": part}, timeout=20)
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
    results = capture_all(channels, start, int(minutes * 60), os.path.join(folder, "audio"),
                          os.environ.get("TV_WHISPER_MODEL", "small"))
    for name, segments in results.items():
        write_transcript(os.path.join(folder, f"{name}.txt"), segments)
    db = os.environ.get("STOCK_FINDER_DB", "stock_finder.db")
    text, count = build_from_transcripts(day, SymbolResolver(os.environ.get("NSE_LIST_CACHE",
                                                                            "nse_equity_list.csv")), db)
    silent = [n for n, s in results.items() if not s]
    if silent:
        text += "\n⚠️ No audio/transcript from: " + ", ".join(silent)
    finished = now_ist()
    text += f"\nReady {finished:%H:%M} IST · {count} mention(s)"
    if send:
        send_telegram(text)
    return text


def previous_text(day: date, db_path: str, resolver: SymbolResolver) -> str:
    """Shortlist stored for `day` (used by the bot's /tv command)."""
    mentions = TvLog(db_path).day(day)
    if not mentions:
        return f"No TV shortlist recorded for {day:%d %b %Y}."
    channels = sorted({m.channel for m in mentions})
    return format_shortlist(shortlist(mentions, resolver.equity_list()), day, channels, include_mentions=True)

