"""Tradier's real answers (read live through this client, 2026-11-04) in the shapes the first version got wrong: an
option with no trade, a bid of 0, and a bid and ask newer than the last trade."""
from __future__ import annotations

import datetime as dt
import inspect
import re
from decimal import Decimal

import httpx
import pytest

from ghostline.market import sources
from ghostline.market.sources import Tradier
from ghostline.practice import fill_price

TRADE = 1793826600000       # milliseconds, as Tradier sends them
NO_TRADE = {"symbol": "AAPL261211P00110000", "bid": 0.0, "ask": 2.13, "last": None, "trade_date": 0,
            "bid_date": 1793825485000, "ask_date": 1793826010000}
AFTER_THE_CLOSE = {"symbol": "SPY", "bid": 767.49, "ask": 767.55, "last": 766.81, "trade_date": TRADE,
                   "bid_date": TRADE + 7_000_000, "ask_date": TRADE + 7_100_000}


def at(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC)


def tradier(answer: dict) -> Tradier:
    return Tradier(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=answer))))


def quote_of(row: dict):
    (q,) = tradier({"quotes": {"quote": row}}).quotes([row["symbol"]])
    return q


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GHOSTLINE_KEY_TRADIER", "test-tradier")


def test_an_option_with_no_trade_is_stamped_with_its_quote_not_1970():
    assert quote_of(NO_TRADE).ts == at(NO_TRADE["ask_date"])


def test_a_quote_is_stamped_with_its_newest_time():
    q = quote_of(AFTER_THE_CLOSE)
    assert q.ts == at(AFTER_THE_CLOSE["ask_date"])
    assert q.last == Decimal("766.81")          # the last trade's price stays what it was


def test_a_quote_with_no_time_at_all_is_stamped_now():
    before = dt.datetime.now(dt.UTC)
    assert quote_of({**NO_TRADE, "bid_date": 0, "ask_date": 0}).ts >= before


def test_a_bid_or_ask_of_zero_means_none():
    assert quote_of(NO_TRADE).bid is None
    assert quote_of(NO_TRADE).ask == Decimal("2.13")
    assert quote_of({**NO_TRADE, "bid": 1.5, "ask": 0.0}).ask is None


def test_a_chain_row_with_a_bid_of_zero_has_no_bid_and_keeps_the_rest():
    row = {**NO_TRADE, "open_interest": 197, "greeks": {"delta": -0.01, "mid_iv": 0.9}}
    (out,) = tradier({"options": {"option": [row]}}).chain("AAPL", dt.date(2026, 12, 11))
    assert out["bid"] is None and out["ask"] == 2.13
    assert out["open_interest"] == 197 and out["greeks"]["delta"] == -0.01


@pytest.mark.parametrize(("side", "row"), [("sell_to_open", NO_TRADE), ("buy", {**NO_TRADE, "bid": 1.5, "ask": 0.0})])
def test_a_practice_order_never_fills_at_a_price_of_zero(side, row):
    q = quote_of(row)
    assert fill_price(side, "market", q.bid, q.ask, None, None).price is None


def test_the_client_can_only_read():
    # The owner's token has full access to the account, orders included (Tradier's documents, 2026-11-04). Anything
    # more than these reads is a deliberate change made here, never a side effect of another one.
    assert {n for n in vars(Tradier) if not n.startswith("_")} == {"name", "base_url", "quotes", "expirations", "chain"}
    assert set(re.findall(r"\.http\.(\w+)\(", inspect.getsource(sources))) == {"get"}
