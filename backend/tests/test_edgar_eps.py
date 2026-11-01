"""EPS history from SEC EDGAR for every scanned stock (the owner, 2026-11-01: the long-term lane was empty).

CANSLIM needs C (the latest quarter's EPS against a year earlier) and A (three years of annual EPS). FMP's free plan
gives them only for stocks the owner holds or watches, so 48 of 50 scanned stocks had neither and no long-term idea
could ever pass. EDGAR's company concepts (free, no key) give reported EPS and revenue for every listed company."""
import datetime as dt
from decimal import Decimal

import httpx

from ghostline.db import Database
from ghostline.ideas import Scan
from ghostline.market import store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Edgar
from ghostline.worker import schedule
from tests.conftest import settings


def _q(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


EPS = {"units": {"USD/shares": [
    _q("2024-03-31", "2024-06-29", 1.00, "2024-08-01"),
    _q("2024-06-30", "2024-09-28", 1.10, "2024-11-01"),
    _q("2025-03-30", "2025-06-28", 1.40, "2025-08-01"),
    _q("2025-03-30", "2025-06-28", 1.45, "2025-11-01"),            # restated later: the later filing wins
    _q("2025-06-29", "2025-09-27", 1.65, "2025-11-01"),
    _q("2021-09-26", "2022-09-24", 3.00, "2022-10-28", "10-K"),
    _q("2022-09-25", "2023-09-30", 3.60, "2023-11-03", "10-K"),
    _q("2023-10-01", "2024-09-28", 4.40, "2024-11-01", "10-K"),
    _q("2024-09-29", "2025-09-27", 5.50, "2025-10-31", "10-K"),
]}}
REVENUE = {"units": {"USD": [
    _q("2021-09-26", "2022-09-24", 100e9, "2022-10-28", "10-K"),
    _q("2022-09-25", "2023-09-30", 120e9, "2023-11-03", "10-K"),
    _q("2023-10-01", "2024-09-28", 150e9, "2024-11-01", "10-K"),
    _q("2024-09-29", "2025-09-27", 190e9, "2025-10-31", "10-K"),
]}}


def _http(missing_revenue_tag=True):
    seen = []

    def handler(r):
        seen.append(r.url.path)
        if r.url.path.endswith("/EarningsPerShareDiluted.json"):
            return httpx.Response(200, json=EPS)
        if r.url.path.endswith("/RevenueFromContractWithCustomerExcludingAssessedTax.json"):
            return httpx.Response(200, json=REVENUE)
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_edgar_gives_quarterly_and_annual_eps_with_revenue():
    http, _ = _http()
    quarters, years = Edgar(http).eps_history("0000320193")
    assert quarters[0] == (dt.date(2025, 9, 27), Decimal("1.65"))
    assert (dt.date(2025, 6, 28), Decimal("1.45")) in quarters                 # the restatement, not the first value
    assert years[0] == (dt.date(2025, 9, 27), Decimal("5.5"), Decimal("190000000000.0"))
    assert len(quarters) == 4 and len(years) == 4


def test_the_job_fills_every_scanned_stock_once_a_week(fresh_db):
    db = Database(settings())
    db.open()
    try:
        store.save_tickers(db, [("AAPL", "Apple Inc.", "0000320193")], "sec", dt.datetime.now(dt.UTC))
        db.execute("INSERT INTO scan_universe (ticker, day, reason) VALUES ('AAPL', CURRENT_DATE, 'test')")
        http, seen = _http()
        assert Jobs(db, http).pull_eps_history() == {"tickers": 1, "rows": 8, "fresh": 0}
        assert Jobs(db, http).pull_eps_history() == {"tickers": 0, "rows": 0, "fresh": 1}   # a week old at most
        rows = db.fetch_all("SELECT kind, count(*) AS n FROM eps_history WHERE ticker = 'AAPL' GROUP BY kind")
        assert {r["kind"]: r["n"] for r in rows} == {"quarter": 4, "year": 4}
    finally:
        db.close()


def test_the_eps_history_is_a_production_pull():
    assert "pull_eps_history" in schedule.PULLS


def test_the_scan_reads_edgar_when_fmp_has_nothing(fresh_db):
    db = Database(settings())
    db.open()
    try:
        store.save_tickers(db, [("AAPL", "Apple Inc.", "0000320193")], "sec", dt.datetime.now(dt.UTC))
        db.execute("INSERT INTO scan_universe (ticker, day, reason) VALUES ('AAPL', CURRENT_DATE, 'test')")
        http, _ = _http()
        Jobs(db, http).pull_eps_history()
        sc = Scan(db, dt.date.today())
        assert sc.earnings("AAPL") == []                     # PEAD and the earnings trade read report dates: untouched
        earnings, fundamentals = sc.canslim_inputs("AAPL", sc.earnings("AAPL"), sc.fundamentals("AAPL"))
        assert [e["actual"] for e in earnings if e["actual"] is not None][-1] == Decimal("1.65")
        income = fundamentals["income_annual"]
        assert income[0]["eps"] == Decimal("5.5") and income[0]["revenue"] == Decimal("190000000000.0")
        assert income[0]["source"] == "sec"
    finally:
        db.close()
