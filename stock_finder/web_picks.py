"""Conservative daily stock calls from dated, attributed financial-news RSS headlines.

No page scraping, no generative inference, and no trading on ambiguous prose.
RSS feeds are discovery only: extraction is restricted to the published headline.
"""
import logging
import os
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree as ET

import requests

from .engine import IST
from .tv import Mention, Segment, build_index, extract_mentions

log = logging.getLogger(__name__)

# Publisher attribution is checked independently of the Google News search query.
SOURCES = {
    "moneycontrol.com": "Moneycontrol",
    "economictimes.indiatimes.com": "The Economic Times",
    "business-standard.com": "Business Standard",
    "livemint.com": "Mint",
    "cnbctv18.com": "CNBC-TV18",
    "ndtvprofit.com": "NDTV Profit",
    "zeebiz.com": "Zee Business",
}
_CALL_BUY = re.compile(r"\b(?:stocks? to buy|buy (?:call|rating|recommendation)|buy these stocks|top buys?)\b", re.I)
_CALL_SELL = re.compile(r"\b(?:stocks? to sell|sell (?:call|rating|recommendation)|stocks? to avoid)\b", re.I)
_MAX_ITEMS = 30


def _publisher(item: ET.Element, expected_host: str) -> str | None:
    source = item.find("source")
    if source is None:
        return None
    host = urlparse(source.get("url", "")).hostname or ""
    if host.lower().removeprefix("www.") != expected_host:
        return None
    return (source.text or SOURCES[expected_host]).strip()


def parse_feed(xml: bytes | str, day: date, listing: dict[str, str], expected_host: str) -> list[Mention]:
    """Only same-IST-day, source-verified, explicit stock calls survive."""
    index = build_index(listing)
    result: list[Mention] = []
    seen: set[tuple[str, str, str]] = set()
    root = ET.fromstring(xml)
    for item in root.findall("./channel/item")[:_MAX_ITEMS]:
        name = _publisher(item, expected_host)
        if not name:
            continue
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(IST)
        except (TypeError, ValueError, OverflowError):
            continue
        if published.date() != day:
            continue
        # Google News adds " - Publisher" to the end of a title.
        headline = title.rsplit(" - ", 1)[0].strip()
        has_buy, has_sell = bool(_CALL_BUY.search(headline)), bool(_CALL_SELL.search(headline))
        if not (has_buy or has_sell):
            continue
        if has_buy and has_sell:
            continue  # mixed-direction roundups cannot safely label each ticker
        for mention in extract_mentions(Segment(name, published, headline), index):
            # A headline about a stock can contain a general market verb without an actual call.
            # Retain only headlines whose explicit headline-level recommendation is unambiguous.
            direction = "BUY" if has_buy else "SELL"
            if mention.action not in ("MENTION", direction):
                continue
            key = (name, link, mention.symbol)
            if key in seen:
                continue
            seen.add(key)
            result.append(Mention(name, published, mention.symbol, direction,
                                  mention.target, mention.stop, f"{headline} | {link}"))
    return result


def collect(day: date, listing: dict[str, str], session=None) -> tuple[list[Mention], list[str], list[str]]:
    """Independent source failures are reported, never silently treated as no recommendations."""
    http = session or requests
    mentions: list[Mention] = []
    succeeded: list[str] = []
    failures: list[str] = []
    for host, label in SOURCES.items():
        query = f'("stocks to buy" OR "stocks to sell" OR "stock recommendations") site:{host} when:2d'
        url = "https://news.google.com/rss/search?" + urlencode({
            "q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en",
        })
        try:
            response = http.get(url, headers={"User-Agent": "StockFinder/1.0"},
                                timeout=(5, 12))
            response.raise_for_status()
            mentions.extend(parse_feed(response.content, day, listing, host))
            succeeded.append(label)
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            failures.append(label)
            log.warning("Daily picks source %s unavailable: %s", label, type(exc).__name__)
    return mentions, succeeded, failures
