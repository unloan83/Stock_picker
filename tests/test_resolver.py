from stock_finder.engine import resolve_requests
from stock_finder.models import StockRequest
from stock_finder.resolver import SymbolResolver

LISTING = {
    "HDFCBANK": "HDFC Bank Limited",
    "HDFCLIFE": "HDFC Life Insurance Company Limited",
    "TATASTEEL": "Tata Steel Limited",
    "TATAPOWER": "The Tata Power Company Limited",
    "TATAMOTORS": "Tata Motors Limited",
    "SBIN": "State Bank of India",
    "INFY": "Infosys Limited",
    "TCS": "Tata Consultancy Services Limited",
    "RELIANCE": "Reliance Industries Limited",
    "M&M": "Mahindra & Mahindra Limited",
}


def make(searcher=lambda q: [], loader=lambda: LISTING, cache=None):
    return SymbolResolver(cache, list_loader=loader, searcher=searcher)


def test_symbols_names_and_aliases():
    r = make()
    cases = {
        "TCS": "TCS", "tcs": "TCS", "HDFC BANK": "HDFCBANK", "hdfc bank ltd": "HDFCBANK", "HDFC": "HDFCBANK",
        "Tata Steel": "TATASTEEL", "state bank of india": "SBIN", "SBI": "SBIN", "infosys": "INFY",
        "Reliance Industries": "RELIANCE", "tata consultancy": "TCS", "mahindra & mahindra": "M&M",
        "NIFTY": "NIFTY", "bank nifty": "BANKNIFTY", "RELIANCE.NS": "RELIANCE", "Tata Power": "TATAPOWER",
    }
    for query, symbol in cases.items():
        assert r.resolve(query).symbol == symbol, query


def test_ambiguous_name_gives_suggestions():
    res = make().resolve("TATA")
    assert res.symbol is None
    assert any(s.startswith("TATASTEEL") for s in res.suggestions) and len(res.suggestions) >= 3


def test_typo_is_matched_fuzzily():
    assert make().resolve("Infosys Limted").symbol == "INFY"
    assert make().resolve("Reliance Industris").symbol == "RELIANCE"


def test_falls_back_to_yahoo_search_when_list_unavailable():
    def broken():
        raise OSError("offline")

    r = make(searcher=lambda q: [("ZOMATO", "Zomato Ltd")], loader=broken)
    res = r.resolve("zomato")
    assert (res.symbol, res.name) == ("ZOMATO", "Zomato Ltd")
    assert make(loader=broken).resolve("nothing here").symbol is None


def test_equity_list_cache(tmp_path):
    path = str(tmp_path / "list.csv")
    make(cache=path).equity_list()

    def broken():
        raise AssertionError("should read cache")

    assert make(loader=broken, cache=path).resolve("tata steel").symbol == "TATASTEEL"


def test_line_of_symbols_is_split_but_names_are_not():
    jobs = resolve_requests(make(), [StockRequest("TCS INFY"), StockRequest("TATA STEEL"),
                                     StockRequest("HDFC BANK"), StockRequest("HDFCBANK")])
    assert [res.symbol for _, res in jobs] == ["TCS", "INFY", "TATASTEEL", "HDFCBANK"]
