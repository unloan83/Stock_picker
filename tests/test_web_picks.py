from datetime import date

from stock_finder.web_picks import parse_feed

LISTING = {"TATASTEEL": "Tata Steel Limited", "INFY": "Infosys Limited"}


def feed(source="https://www.moneycontrol.com", publication="Mon, 02 Nov 2026 03:00:00 GMT",
         title="Stocks to buy today: Tata Steel, Infosys"):
    return (f"<rss><channel><item><title>{title}</title>"
            f"<link>https://news.google.com/rss/articles/example</link>"
            f"<pubDate>{publication}</pubDate>"
            f'<source url="{source}">Moneycontrol</source>'
            "</item></channel></rss>")


def test_explicit_dated_attributed_buy_headline():
    calls = parse_feed(feed(), date(2026, 11, 2), LISTING, "moneycontrol.com")
    assert {c.symbol for c in calls} == {"TATASTEEL", "INFY"}
    assert {c.action for c in calls} == {"BUY"}
    assert all("https://" in c.snippet for c in calls)


def test_wrong_source_and_stale_date_rejected():
    assert not parse_feed(feed(source="https://other.example"), date(2026, 11, 2),
                          LISTING, "moneycontrol.com")
    assert not parse_feed(feed(), date(2026, 11, 3), LISTING, "moneycontrol.com")


def test_no_ambiguous_or_non_calls():
    assert not parse_feed(feed(title="Stocks to buy and stocks to sell: Tata Steel"),
                          date(2026, 11, 2), LISTING, "moneycontrol.com")
    assert not parse_feed(feed(title="Tata Steel surges 4 percent today"),
                          date(2026, 11, 2), LISTING, "moneycontrol.com")
