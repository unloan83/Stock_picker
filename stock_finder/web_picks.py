"""Extract NSE stock names from dated, attributed Indian financial-news headlines.

News is a discovery mechanism only; the independent analysis engine issues verdicts.
"""
import logging
from datetime import date
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
    """Return named NSE securities from same-day, attributed headlines, without inferring direction."""
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
        for mention in extract_mentions(Segment(name, published, headline), index):
            key = (name, link, mention.symbol)
            if key in seen:
                continue
            seen.add(key)
            # Deliberately ignore any journalist/broker recommendation and numeric levels.
            # The existing analysis engine determines its own verdict from market data.
            result.append(Mention(name, published, mention.symbol, "MENTION",
                                  snippet=f"{headline} | {link}"))
    return result


def collect(day: date, listing: dict[str, str], session=None) -> tuple[list[Mention], list[str], list[str]]:
    """Independently collect named tickers; source failures remain visible."""
    http = session or requests
    mentions: list[Mention] = []
    succeeded: list[str] = []
    failures: list[str] = []
    for host, label in SOURCES.items():
        query = f'("stocks to buy" OR "stocks to watch" OR "stock picks" OR "stock recommendations") site:{host} when:2d'
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
