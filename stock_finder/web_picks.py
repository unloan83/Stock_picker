"""Extract NSE stock names from dated, attributed Indian financial-news headlines.

News is a discovery mechanism only; the independent analysis engine issues verdicts.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree as ET

import requests

from .engine import IST
from .mentions import Mention, Segment, build_index, extract_mentions

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
    "upstox.com": "Upstox",
    "dhan.co": "Dhan",
    "angelone.in": "Angel One",
    "5paisa.com": "5paisa",
    "icicidirect.com": "ICICI Direct",
    "motilaloswal.com": "Motilal Oswal",
    "kotaksecurities.com": "Kotak Securities",
    "hdfcsec.com": "HDFC Securities",
    "sharekhan.com": "Sharekhan",
}
_MAX_ITEMS = 60
_SEARCHES = (
    '"stocks to buy" OR "stock picks" OR "analyst recommends"',
    '"stocks to watch" OR "stocks in focus" OR "brokerage"',
)


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


def _source_query(host: str, terms: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode({
        "q": f"({terms}) site:{host} when:2d",
        "hl": "en-IN", "gl": "IN", "ceid": "IN:en",
    })


def _fetch_source(http, day: date, listing: dict[str, str], host: str, terms: str):
    response = http.get(_source_query(host, terms),
                        headers={"User-Agent": "StockFinder/1.0"}, timeout=(5, 12))
    response.raise_for_status()
    return parse_feed(response.content, day, listing, host)


def collect(day: date, listing: dict[str, str], session=None) -> tuple[list[Mention], list[str], list[str]]:
    """Find current-day stock names without silently padding unavailable picks."""
    http = session or requests
    mentions: list[Mention] = []
    succeeded: set[str] = set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {
            pool.submit(_fetch_source, http, day, listing, host, terms): host
            for host in SOURCES for terms in _SEARCHES
        }
        for job in as_completed(jobs):
            host = jobs[job]
            try:
                mentions.extend(job.result())
                succeeded.add(host)
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                log.warning("Daily source %s failed: %s", SOURCES[host], type(exc).__name__)
    unique: dict[tuple[str, str, str], Mention] = {}
    for mention in mentions:
        unique[(mention.channel, mention.symbol, mention.snippet)] = mention
    checked = [name for host, name in SOURCES.items() if host in succeeded]
    failed = [name for host, name in SOURCES.items() if host not in succeeded]
    ordered = sorted(unique.values(), key=lambda m: (m.at, m.channel, m.symbol))
    return ordered, checked, failed
