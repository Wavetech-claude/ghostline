"""The pre-computed numbers Claude reads: one bundle per ticker (price with source and time, trend statistics,
fundamentals, earnings, news, filings, the modules' signals, the regime, the owner's position, the balance, and the
sizes the code computed from the risk rules). Claude never reads raw bars or the rules themselves."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from ghostline import indicators, risk_service
from ghostline.db import Database
from ghostline.market import store
from ghostline.modules import trend as T
from ghostline.practice import Practice


def _j(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _j(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_j(x) for x in v]
    return v


def _pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if b else None


def ticker_facts(db: Database, ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    p = Practice(db)
    bars = store.bars(db, t, "1D", limit=400)
    closes = [float(b["close"]) for b in bars]
    out: dict[str, Any] = {"ticker": t, "as_of": dt.datetime.now(dt.UTC).isoformat()}
    q = p.latest_quote(t)
    out["quote"] = _j({k: q.get(k) for k in ("bid", "ask", "last", "ts", "source", "as_of", "delayed")}) if q else None
    if bars:
        hi, lo = T.range_52w(bars)
        vols = [float(b["volume"]) for b in bars]
        out["price_stats"] = {
            "last_close": closes[-1],
            "bars": len(bars),
            "source": bars[-1]["source"],
            "newest_bar": bars[-1]["ts"].isoformat(),
            "high_52w": hi,
            "low_52w": lo,
            "from_52w_high_pct": _pct(closes[-1], hi),
            "from_52w_low_pct": _pct(closes[-1], lo),
            "sma20": T.last(indicators.sma(closes, 20)),
            "sma50": T.last(indicators.sma(closes, 50)),
            "sma200": T.last(indicators.sma(closes, 200)),
            "rsi14": T.last(indicators.rsi(closes, 14)),
            "atr14": T.atr(bars),
            "avg_volume_20d": T.avg(vols[-20:]),
            "return_1m_pct": _pct(closes[-1], closes[-22]) if len(closes) > 22 else None,
            "return_3m_pct": _pct(closes[-1], closes[-64]) if len(closes) > 64 else None,
            "return_6m_pct": _pct(closes[-1], closes[-127]) if len(closes) > 127 else None,
            "return_1y_pct": _pct(closes[-1], closes[-253]) if len(closes) > 253 else None,
        }
        out["price_stats"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out["price_stats"].items()}
    else:
        out["price_stats"] = None
    f = db.fetch_one(
        "SELECT payload, source, as_of FROM fundamentals WHERE ticker = %s ORDER BY as_of DESC LIMIT 1", (t,)
    )
    if f:
        payload = f["payload"] if isinstance(f["payload"], dict) else json.loads(f["payload"])
        profile, ratios = payload.get("profile") or {}, payload.get("ratios") or {}
        out["fundamentals"] = _j(
            {
                "source": f["source"],
                "as_of": f["as_of"],
                "company": profile.get("companyName"),
                "sector": profile.get("sector"),
                "industry": profile.get("industry"),
                "market_cap": profile.get("mktCap") or profile.get("marketCap"),
                "beta": profile.get("beta"),
                "pe_ttm": ratios.get("peRatioTTM") or ratios.get("priceToEarningsRatioTTM"),
                "pb_ttm": ratios.get("priceToBookRatioTTM"),
                "roe_ttm": ratios.get("returnOnEquityTTM"),
                "debt_to_equity_ttm": ratios.get("debtEquityRatioTTM") or ratios.get("debtToEquityRatioTTM"),
                "income_annual": (payload.get("income_annual") or [])[:4],
            }
        )
    else:
        out["fundamentals"] = None
    earnings = db.fetch_all(
        "SELECT at, estimate, actual, source, as_of FROM earnings WHERE ticker = %s ORDER BY at DESC LIMIT 6", (t,)
    )
    for e in earnings:
        if e["estimate"] not in (None, 0) and e["actual"] is not None:
            e["surprise_pct"] = round(float((e["actual"] - e["estimate"]) / abs(e["estimate"]) * 100), 2)
    out["earnings"] = _j(earnings)
    nxt = db.fetch_one("SELECT at FROM earnings WHERE ticker = %s AND at >= CURRENT_DATE ORDER BY at LIMIT 1", (t,))
    out["next_earnings"] = nxt["at"].isoformat() if nxt else None
    out["days_to_earnings"] = (nxt["at"] - dt.date.today()).days if nxt else None
    out["news"] = _j(
        db.fetch_all(
            "SELECT headline, url, published_at, source, tone FROM news WHERE ticker = %s "
            "ORDER BY published_at DESC LIMIT 10",
            (t,),
        )
    )
    out["filings"] = _j(
        db.fetch_all("SELECT form, filed_at, url FROM filings WHERE ticker = %s ORDER BY filed_at DESC LIMIT 5", (t,))
    )
    out["signals"] = _j(
        db.fetch_all(
            "SELECT module, lane, direction, score, actionable, entry, stop, target, reason, day "
            "FROM signals WHERE ticker = %s AND day = (SELECT max(day) FROM signals WHERE ticker = %s) "
            "ORDER BY score DESC",
            (t, t),
        )
    )
    regime = db.fetch_one(
        "SELECT reason, score, day FROM signals WHERE module = 'macro_regime' ORDER BY day DESC, id DESC LIMIT 1"
    )
    out["macro_regime"] = _j(regime) if regime else None
    out["macro"] = _j(
        db.fetch_all(
            "SELECT DISTINCT ON (series) series, ts, value FROM macro_series WHERE value IS NOT NULL "
            "ORDER BY series, ts DESC"
        )
    )
    out["positions"] = _j(
        db.fetch_all(
            "SELECT b.kind AS book, p.quantity FROM positions p JOIN books b ON b.id = p.book_id WHERE p.ticker = %s",
            (t,),
        )
    )
    ideas = db.fetch_all(
        "SELECT lane, module, status, entry, stop, target, quantity, size_share, max_loss, collateral, refusal "
        "FROM ideas WHERE ticker = %s AND expires_at > now() ORDER BY id DESC LIMIT 5",
        (t,),
    )
    out["ideas"] = _j(ideas)
    # the balance and the sizes the code computed from the rules (shares, never dollar rules)
    rules = risk_service.rule_set(db)
    book = "real" if p.facts(p.book_id("real")).equity > 0 else "practice"
    facts = p.facts(p.book_id(book))
    loss_share = rules.rules["max_loss_per_idea_share"].value if "max_loss_per_idea_share" in rules.rules else None
    price = Decimal(str(closes[-1])) if closes else None
    sizing: dict[str, Any] = {
        "book": book,
        "equity": str(facts.equity),
        "available": str(facts.available),
        "rules_set": not rules.missing,
        "max_loss_share": str(loss_share) if loss_share else None,
    }
    if price and loss_share and facts.equity > 0:
        risk_money = (facts.equity * loss_share).quantize(Decimal("0.01"))
        for stop_pct in (5, 8, 10):
            stop = (price * (1 - Decimal(stop_pct) / 100)).quantize(Decimal("0.01"))
            shares = (risk_money / (price - stop)).quantize(Decimal("0.0001"))
            value = (shares * price).quantize(Decimal("0.01"))
            cap = (facts.equity * Decimal("0.20")).quantize(Decimal("0.01"))
            sizing[f"stop_{stop_pct}pct"] = {
                "stop": str(stop),
                "shares": str(shares),
                "position_value": str(min(value, cap)),
                "risk_money": str(risk_money),
                "cap_20pct": str(cap),
            }
    out["sizing"] = sizing
    return out


def brief_facts(db: Database) -> dict[str, Any]:
    """What the morning brief talks about: the regime, the macro series, positions with their latest prices and
    day moves, the watchlist's movers, today's and this week's earnings, and the newest ideas."""
    p = Practice(db)
    out: dict[str, Any] = {"for_date": dt.date.today().isoformat(), "as_of": dt.datetime.now(dt.UTC).isoformat()}
    regime = db.fetch_one(
        "SELECT reason, score, day, evidence FROM signals WHERE module = 'macro_regime' "
        "ORDER BY day DESC, id DESC LIMIT 1"
    )
    out["macro_regime"] = _j({"reason": regime["reason"], "day": regime["day"]}) if regime else None
    out["macro"] = _j(
        db.fetch_all(
            "SELECT DISTINCT ON (series) series, ts, value FROM macro_series WHERE value IS NOT NULL "
            "ORDER BY series, ts DESC"
        )
    )
    rows = db.fetch_all(
        "SELECT b.kind AS book, p.ticker, p.quantity FROM positions p JOIN books b ON b.id = p.book_id ORDER BY 1, 2"
    )
    tickers = sorted({r["ticker"] for r in rows} | {w["ticker"] for w in db.fetch_all("SELECT ticker FROM watchlist")})
    moves = []
    for t in tickers:
        bars = store.bars(db, t, "1D", limit=3)
        if len(bars) >= 2:
            c, pc = float(bars[-1]["close"]), float(bars[-2]["close"])
            moves.append(
                {
                    "ticker": t,
                    "close": c,
                    "day_change_pct": _pct(c, pc),
                    "bar_date": bars[-1]["ts"].date().isoformat(),
                    "source": bars[-1]["source"],
                }
            )
    out["positions"] = _j(rows)
    out["moves"] = sorted(moves, key=lambda m: -abs(m["day_change_pct"] or 0))
    out["earnings_this_week"] = _j(
        db.fetch_all(
            "SELECT ticker, at FROM earnings WHERE at BETWEEN CURRENT_DATE AND CURRENT_DATE + 7 "
            "AND ticker = ANY(%s) ORDER BY at",
            (tickers,),
        )
    )
    out["ideas"] = _j(
        db.fetch_all(
            "SELECT i.lane, i.ticker, i.module, i.status, i.entry, i.stop, i.target, i.quantity, i.refusal, s.reason "
            "FROM ideas i JOIN signals s ON s.id = i.signal_id WHERE i.created_at > now() - interval '36 hours' "
            "ORDER BY i.status = 'open' DESC, s.score DESC LIMIT 12"
        )
    )
    out["news"] = _j(
        db.fetch_all(
            "SELECT ticker, headline, published_at, source FROM news WHERE published_at > now() - interval '36 hours' "
            "AND ticker = ANY(%s) ORDER BY published_at DESC LIMIT 15",
            (tickers,),
        )
    )
    book = "real" if p.facts(p.book_id("real")).equity > 0 else "practice"
    f = p.facts(p.book_id(book))
    out["account"] = {
        "book": book,
        "equity": str(f.equity),
        "available": str(f.available),
        "open_positions": f.open_positions,
    }
    return out
