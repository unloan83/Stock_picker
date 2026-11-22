from datetime import date

from stock_finder.web_picks import parse_feed

LISTING = {"TATASTEEL": "Tata Steel Limited", "INFY": "Infosys Limited"}


def feed(source="https://www.moneycontrol.com", publication="Mon, 02 Nov 2026 03:00:00 GMT",
         title="Stocks to watch today: Tata Steel, Infosys"):
    return (f"<rss><channel><item><title>{title}</title>"
            f"<link>https://news.google.com/rss/articles/example</link>"
            f"<pubDate>{publication}</pubDate>"
            f'<source url="{source}">Moneycontrol</source>'
            "</item></channel></rss>")


def test_names_collected_without_publisher_buy_call():
    picks = parse_feed(feed(), date(2026, 11, 2), LISTING, "moneycontrol.com")
    assert {p.symbol for p in picks} == {"TATASTEEL", "INFY"}
    assert {p.action for p in picks} == {"MENTION"}
    assert all(p.target is None and p.stop is None for p in picks)
    assert all("https://" in p.snippet for p in picks)


def test_sell_headline_is_just_a_name():
    picks = parse_feed(feed(title="Stocks to sell: Tata Steel"), date(2026, 11, 2),
                       LISTING, "moneycontrol.com")
    assert [(p.symbol, p.action) for p in picks] == [("TATASTEEL", "MENTION")]


def test_wrong_source_and_stale_date_rejected():
    assert not parse_feed(feed(source="https://other.example"), date(2026, 11, 2),
                          LISTING, "moneycontrol.com")
    assert not parse_feed(feed(), date(2026, 11, 3), LISTING, "moneycontrol.com")


def test_no_names_means_no_candidate():
    assert not parse_feed(feed(title="Markets rise on global sentiment"),
                          date(2026, 11, 2), LISTING, "moneycontrol.com")
