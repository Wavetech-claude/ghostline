"""The strategy modules on synthetic bars: each writes the same signal record with its reason and evidence, and says
when it cannot measure something instead of guessing."""
import datetime as dt
from decimal import Decimal

from ghostline.modules import canslim, earnings_trade, macro_regime, options_math, pead, sell_puts, vcp
from ghostline.modules import trend as T

D = Decimal


def weekdays(n: int, start: dt.date = dt.date(2025, 9, 1)) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def bars_from(closes: list[float], volumes: list[float] | None = None, start: dt.date = dt.date(2025, 9, 1),
              opens: dict[int, float] | None = None) -> list[dict]:
    """Daily bars whose open is the prior close, except where `opens` names a gap."""
    days = weekdays(len(closes), start)
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        o = (opens or {}).get(i, prev)
        hi, lo = max(o, c) * 1.004, min(o, c) * 0.996
        v = volumes[i] if volumes else 1_000_000
        out.append({"ticker": "T", "timeframe": "1D", "ts": dt.datetime.combine(days[i], dt.time(13, 0), tzinfo=dt.UTC),
                    "open": D(str(round(o, 4))), "high": D(str(round(hi, 4))), "low": D(str(round(lo, 4))),
                    "close": D(str(round(c, 4))), "volume": D(str(v)), "source": "test", "as_of": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)})
    return out


def ramp(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def spy_bars(n: int) -> list[dict]:
    return bars_from(ramp(400, 440, n))


# ── VCP ─────────────────────────────────────────────────────────────────────────────────────────────────────
def vcp_closes() -> tuple[list[float], list[float]]:
    closes = ramp(50, 100, 220)                                 # a long Stage 2 rise
    closes += ramp(100, 80, 10)[1:] + ramp(80, 98, 12)[1:]     # T1: 20% deep
    closes += ramp(98, 88, 8)[1:] + ramp(88, 97, 10)[1:]       # T2: 10%
    closes += ramp(97, 93, 7)[1:] + ramp(93, 96, 6)[1:]        # T3: 4%, now 1% under the pivot
    vols = [1_000_000.0] * len(closes)
    for i in range(len(closes) - 11, len(closes) - 1):
        vols[i] = 350_000.0                                     # the dry-up into the pivot
    return closes, vols


def test_vcp_finds_tightening_contractions_in_a_stage_two_stock():
    closes, vols = vcp_closes()
    s = vcp.signal("T", bars_from(closes, vols), spy_bars(len(closes)))
    assert s.module == "vcp" and s.lane == "swing"
    assert s.evidence["trend_template_passed"] >= 6
    assert len(s.evidence["contractions"]) >= 2
    assert s.evidence["state"] in ("pre_breakout", "breakout") and s.actionable
    assert s.entry and s.stop and s.stop < s.entry < s.target
    assert 60 <= s.score <= 100 and "contractions" in s.reason


def test_vcp_refuses_without_history_and_outside_stage_two():
    s = vcp.signal("T", bars_from(ramp(50, 60, 100)), spy_bars(100))
    assert s.score == 0 and "fewer than 260" in s.reason
    down = ramp(100, 40, 300)
    s = vcp.signal("T", bars_from(down), spy_bars(300))
    assert not s.actionable and "not in a Stage 2" in s.reason and s.score < 30


def test_trend_helpers():
    closes = ramp(10, 20, 300)
    bars = bars_from(closes)
    hi, lo = T.range_52w(bars)
    assert hi > lo
    sw = T.swings(bars_from(ramp(10, 20, 20) + ramp(20, 10, 20) + ramp(10, 30, 20)))
    assert [s.kind for s in sw] == ["high", "low"]
    assert T.rs_score(55) == 100 and T.rs_score(-30) == 0 and T.rs_score(None) is None


# ── PEAD and the earnings reaction ──────────────────────────────────────────────────────────────────────────
def pead_bars() -> tuple[list[dict], list[dict]]:
    closes = ramp(90, 100, 60)
    report_day = weekdays(60)[59]
    closes += [106, 107, 108, 109, 110]       # the gap week, Monday to Friday (the report came after Friday's close)
    closes += [109, 108, 107, 106, 107]       # a red week: open 110, close 107 (high ≈ 110.4, low ≈ 105.6)
    closes += [108, 110, 112]                 # the current green week closing above the red high
    bars = bars_from(closes, opens={60: 106.0})   # the gap at Monday's open
    earnings = [{"ticker": "T", "at": report_day, "estimate": D("1.0"), "actual": D("1.2")}]
    return bars, earnings


def test_pead_reads_the_gap_the_red_candle_and_the_breakout():
    bars, earnings = pead_bars()
    s = pead.signal("T", bars, earnings)
    assert s is not None and s.evidence["gap_pct"] >= 3 and s.evidence["timing"] == "amc"
    assert s.evidence["stage"] == "breakout" and s.actionable and s.score >= 85
    red = s.evidence["red_candle"]
    assert s.entry == red["high"] and s.stop == red["low"] and abs(s.target - (s.entry + 2 * (s.entry - s.stop))) < 1e-6


def test_pead_is_silent_without_a_recent_report_and_flags_a_small_gap():
    bars, earnings = pead_bars()
    assert pead.signal("T", bars, []) is None
    old = [{"ticker": "T", "at": dt.date(2024, 1, 1), "estimate": None, "actual": D("1")}]
    assert pead.signal("T", bars, old) is None
    flat = bars_from(ramp(100, 101, 70))
    s = pead.signal("T", flat, [{"ticker": "T", "at": weekdays(70)[60], "estimate": None, "actual": D("1")}])
    assert s is not None and s.score == 0 and "without a 3% gap" in s.reason


def test_earnings_trade_scores_five_factors_and_grades():
    closes = ramp(80, 90, 220) + ramp(90.5, 100, 20) + [109, 110, 111, 112, 113]     # +11% into the report
    vols = [1_000_000.0] * 220 + [1_600_000.0] * 20 + [3_000_000.0] * 5
    bars = bars_from(closes, vols, opens={240: 108.5})
    report_day = bars[239]["ts"].date()
    s = earnings_trade.signal("T", bars, [{"ticker": "T", "at": report_day, "estimate": None, "actual": D("2")}])
    assert s is not None and set(s.evidence["scores"]) == {"gap", "trend", "volume", "ma200", "ma50"}
    assert s.evidence["gap_pct"] >= 7 and s.evidence["grade"] in ("A", "B") and s.actionable
    assert s.stop < s.entry < s.target
    assert earnings_trade.signal("T", bars, []) is None


# ── CANSLIM ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_canslim_scores_what_it_can_and_names_the_unknown():
    closes = [v + (1.5 if i % 2 else 0.0) for i, v in enumerate(ramp(50, 100, 300))]   # up on odd days, down on even
    vols = [1_500_000.0 if i % 2 else 800_000.0 for i in range(300)]                    # up days carry more volume
    bars = bars_from(closes, vols)
    last_day = bars[-1]["ts"].date()
    earnings = [{"ticker": "T", "at": last_day - dt.timedelta(days=10), "estimate": None, "actual": D("2.0")},
                {"ticker": "T", "at": last_day - dt.timedelta(days=375), "estimate": None, "actual": D("1.2")}]
    fundamentals = {"income_annual": [{"date": "2025-12-31", "eps": 3.0, "revenue": 1000}, {"date": "2024-12-31", "eps": 2.2, "revenue": 800},
                                      {"date": "2023-12-31", "eps": 1.7, "revenue": 650}, {"date": "2022-12-31", "eps": 1.3, "revenue": 500}]}
    s = canslim.signal("T", bars, spy_bars(300), earnings, fundamentals)
    c = s.evidence["components"]
    assert c["C"] == 100 and c["A"] == 80 and c["I"] is None and c["M"] is not None
    assert s.evidence["unknown"] == ["I"] and s.score > 60 and s.lane == "long"
    assert s.stop == round(s.entry * 0.92, 4) and s.target == round(s.entry * 1.25, 4)
    none = canslim.signal("T", bars, spy_bars(300), [], None)
    assert none.evidence["components"]["C"] is None and "C" in none.evidence["unknown"] and not none.actionable


def test_canslim_growth_arithmetic():
    g, meta = canslim.quarterly_growth([{"at": dt.date(2026, 1, 1), "actual": D("3")}, {"at": dt.date(2025, 1, 2), "actual": D("2")}])
    assert round(g) == 50 and meta["prior_year"] == "2"
    assert canslim.quarterly_growth([{"at": dt.date(2026, 1, 1), "actual": D("3")}])[0] is None
    cagr, meta = canslim.annual_growth([{"date": "2025", "eps": 2.0, "revenue": 100}, {"date": "2024", "eps": 1.6, "revenue": 85},
                                        {"date": "2023", "eps": 1.3, "revenue": 75}, {"date": "2022", "eps": 1.0, "revenue": 65}])
    assert 25 < cagr < 27 and meta["stable"]
    assert canslim.a_score(cagr, meta) == 60                          # 50 for 25–29% + the stability bonus


# ── the macro regime ────────────────────────────────────────────────────────────────────────────────────────
def month_end_bars(values: list[float], start: dt.date = dt.date(2024, 1, 31)) -> list[dict]:
    out, d = [], start
    for v in values:
        ts = dt.datetime.combine(d, dt.time(20, 0), tzinfo=dt.UTC)
        out.append({"ts": ts, "open": D(str(v)), "high": D(str(v)), "low": D(str(v)), "close": D(str(v)), "volume": D(1),
                    "source": "test", "as_of": ts})
        nxt = (d.replace(day=28) + dt.timedelta(days=4))
        d = nxt.replace(day=1) + dt.timedelta(days=31)
        d = d.replace(day=1) - dt.timedelta(days=1)
    return out


def test_macro_regime_needs_thirteen_months_and_then_names_one():
    short = {t: month_end_bars([100 + i for i in range(6)]) for t in macro_regime.ETFS}
    r = macro_regime.regime(short, {})
    assert set(r["unknown"]) == set(r["components"]) and r["regime"] == "unclear"
    n = 20
    bars = {"SPY": month_end_bars([100 + i for i in range(n)]), "RSP": month_end_bars([100 + 2 * i for i in range(n)]),
            "IWM": month_end_bars([100 + 3 * i for i in range(n)]), "HYG": month_end_bars([100 + i for i in range(n)]),
            "LQD": month_end_bars([100] * n), "TLT": month_end_bars([100 - i for i in range(n)]),
            "XLY": month_end_bars([100 + 2 * i for i in range(n)]), "XLP": month_end_bars([100] * n)}
    macro = {"DGS10": [(b["ts"].date(), 4.0 + 0.05 * i) for i, b in enumerate(bars["SPY"])],
             "DGS2": [(b["ts"].date(), 4.0) for b in bars["SPY"]]}
    r = macro_regime.regime(bars, macro)
    assert r["unknown"] == [] and r["labels"]["concentration"] == "broadening" and r["labels"]["size"] == "small_cap_leading"
    assert r["regime"] == "broadening" and r["confidence"] in ("high", "moderate")
    s = macro_regime.signal(bars, macro)
    assert s.ticker == "MARKET" and s.lane == "macro" and "broadening" in s.reason


# ── puts ────────────────────────────────────────────────────────────────────────────────────────────────────
def chain(today: dt.date) -> list[dict]:
    exp = today + dt.timedelta(days=35)
    far = today + dt.timedelta(days=70)
    rows = []
    for strike, delta, bid, oi, e in ((90, -0.18, 1.10, 900, exp), (95, -0.30, 2.20, 1500, exp), (100, -0.50, 4.0, 2000, exp),
                                      (95, -0.30, 3.0, 1500, far), (85, -0.10, 0.4, 100, exp)):
        rows.append({"option_symbol": f"T{e:%y%m%d}P{int(strike * 1000):08d}", "underlying": "T", "expiry": e, "strike": D(strike),
                     "kind": "put", "bid": D(str(bid)), "ask": D(str(bid + 0.1)), "delta": D(str(delta)), "iv": D("0.35"),
                     "open_interest": D(oi), "source": "tradier", "as_of": dt.datetime.now(dt.UTC)})
    rows.append({**rows[0], "option_symbol": "T260101C00100000", "kind": "call"})
    return rows


def test_sell_puts_picks_a_covered_liquid_put_and_rechecks_it_with_black_scholes():
    today = dt.date(2026, 10, 30)
    c = sell_puts.candidates("T", chain(today), 100.0, today, days_to_earnings=60)
    assert [x["strike"] for x in c] == [95.0, 90.0]            # 100 (delta −0.5) out, 85 (thin) out, 70 days out
    best = c[0]
    assert best["collateral"] == 9500 and best["dte"] == 35 and best["annualized_yield_pct"] > 20
    assert -0.45 < best["bs_delta"] < -0.15 and 0.5 < best["p_expires_worthless"] < 0.95
    assert sell_puts.candidates("T", chain(today), 100.0, today, days_to_earnings=10) == []   # earnings inside
    s = sell_puts.signal("T", chain(today), 100.0, today, 60, "tradier", None)
    assert s.actionable and s.option_symbol.endswith("P00095000") and s.stop == 85.5 and "collateral" in s.reason
    assert sell_puts.signal("T", chain(today), None, today, 60, "tradier", None) is None
    none = sell_puts.signal("T", chain(today), 100.0, today, 10, "tradier", None)
    assert none.score == 0 and "no put" in none.reason


def test_black_scholes_is_sane():
    p = options_math.put_price(100, 95, 35 / 365, 0.04, 0.35)
    assert 1.0 < p < 3.0
    d = options_math.put_delta(100, 95, 35 / 365, 0.04, 0.35)
    assert -0.4 < d < -0.2
    assert options_math.put_expires_worthless(100, 95, 35 / 365, 0.04, 0.35) > 0.6
    assert options_math.put_delta(100, 150, 35 / 365, 0.04, 0.35) < -0.99
