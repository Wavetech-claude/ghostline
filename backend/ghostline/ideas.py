"""The daily scan: every module over every ticker in the universe writes its signal; the actionable signals become
ideas in three lanes (long, swing, puts), each sized to the balance and passed through the risk rules. A refused
idea is kept with its reason, never hidden: the app says what is missing."""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from typing import Any

from ghostline import ledger, occ, risk, risk_service
from ghostline.db import Database
from ghostline.market import store
from ghostline.modules import Signal, canslim, earnings_trade, macro_regime, pead, sell_puts, vcp
from ghostline.practice import Practice

logger = logging.getLogger("ghostline.ideas")
MAX_SIZE_SHARE = Decimal("0.20")  # no single idea above a fifth of the balance (O'Neil's 10–20%)
THRESHOLDS = {"canslim": 70, "vcp": 70, "pead": 65, "earnings_trade": 70, "sell_puts": 60}
LANE_OF = {"canslim": "long", "vcp": "swing", "pead": "swing", "earnings_trade": "swing", "sell_puts": "puts"}


def _q(x: float | None, places: str = "0.0001") -> Decimal | None:
    return None if x is None else Decimal(str(x)).quantize(Decimal(places))


class Scan:
    def __init__(self, db: Database, today: dt.date | None = None) -> None:
        self.db = db
        self.today = today or dt.date.today()
        self.practice = Practice(db)

    # ── reads ──
    def universe(self) -> list[str]:
        rows = self.db.fetch_all("SELECT ticker FROM scan_universe WHERE day = %s", (self.today,))
        found = {r["ticker"] for r in rows}
        found.update(store.tracked_tickers(self.db))
        found.discard("MARKET")
        return sorted(t for t in found if t not in macro_regime.ETFS or t == "SPY")

    def bars(self, ticker: str) -> list[dict]:
        return store.bars(self.db, ticker, "1D", limit=420)

    def earnings(self, ticker: str) -> list[dict]:
        return self.db.fetch_all(
            "SELECT ticker, at, estimate, actual FROM earnings WHERE ticker = %s ORDER BY at", (ticker,)
        )

    def fundamentals(self, ticker: str) -> dict | None:
        row = self.db.fetch_one(
            "SELECT payload FROM fundamentals WHERE ticker = %s ORDER BY as_of DESC LIMIT 1", (ticker,)
        )
        if not row:
            return None
        p = row["payload"]
        return p if isinstance(p, dict) else json.loads(p)

    def chain(self, ticker: str) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT DISTINCT ON (option_symbol) * FROM option_chains WHERE underlying = %s "
            "AND as_of > now() - interval '2 days' "
            "ORDER BY option_symbol, as_of DESC",
            (ticker,),
        )
        return rows

    def macro(self) -> dict[str, list[tuple[dt.date, float]]]:
        rows = self.db.fetch_all(
            "SELECT series, ts, value FROM macro_series WHERE value IS NOT NULL ORDER BY series, ts"
        )
        out: dict[str, list[tuple[dt.date, float]]] = {}
        for r in rows:
            out.setdefault(r["series"], []).append((r["ts"], float(r["value"])))
        return out

    # ── the scan ──
    def run(self) -> dict[str, Any]:
        spy = self.bars("SPY")
        etf_bars = {t: self.bars(t) for t in macro_regime.ETFS}
        out = {"day": self.today.isoformat(), "tickers": 0, "signals": 0, "ideas": 0, "refused": 0, "skipped": []}
        self.save(macro_regime.signal(etf_bars, self.macro()))
        out["signals"] += 1
        for ticker in self.universe():
            bars = self.bars(ticker)
            if not bars:
                out["skipped"].append(ticker)
                continue
            earnings = self.earnings(ticker)
            signals = [
                vcp.signal(ticker, bars, spy),
                canslim.signal(ticker, bars, spy, earnings, self.fundamentals(ticker)),
                pead.signal(ticker, bars, earnings),
                earnings_trade.signal(ticker, bars, earnings),
            ]
            chain = self.chain(ticker)
            if chain:
                q = self.practice.latest_price(ticker)
                signals.append(
                    sell_puts.signal(
                        ticker,
                        chain,
                        float(q) if q is not None else None,
                        self.today,
                        self.practice._days_to_earnings(ticker),
                        chain[0]["source"],
                        chain[0]["as_of"],
                        min_open_interest=self._oi_floor(),
                    )
                )
            out["tickers"] += 1
            for s in signals:
                if s is None:
                    continue
                sid = self.save(s)
                out["signals"] += 1
                if s.actionable and s.score >= THRESHOLDS.get(s.module, 101) and s.lane in ("long", "swing", "puts"):
                    made = self.idea(sid, s)
                    out["ideas" if made["status"] == "open" else "refused"] += 1
            if len(out["skipped"]) > 200:
                break
        return out

    def _oi_floor(self) -> float:
        row = self.db.fetch_one("SELECT value, enabled FROM risk_rules WHERE key = 'liquidity_min_open_interest'")
        return float(row["value"]) if row and row["enabled"] else 500.0

    def save(self, s: Signal) -> int:
        row = self.db.fetch_one(
            "INSERT INTO signals (ticker, module, lane, direction, score, actionable, horizon_days, entry, stop, "
            "target, "
            "reason, evidence, day, source, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
            "%s, %s, %s) "
            "ON CONFLICT (ticker, module, day) DO UPDATE SET score = EXCLUDED.score, actionable = EXCLUDED.actionable, "
            "entry = EXCLUDED.entry, stop = EXCLUDED.stop, target = EXCLUDED.target, reason = EXCLUDED.reason, "
            "evidence = EXCLUDED.evidence, source = EXCLUDED.source, as_of = EXCLUDED.as_of, "
            "direction = EXCLUDED.direction "
            "RETURNING id",
            (
                s.ticker,
                s.module,
                s.lane,
                s.direction,
                s.score,
                s.actionable,
                s.horizon_days,
                _q(s.entry),
                _q(s.stop),
                _q(s.target),
                s.reason,
                json.dumps(s.evidence, default=str),
                self.today,
                s.source,
                s.as_of or dt.datetime.now(dt.UTC),
            ),
        )
        with self.db.connection() as conn:
            conn.commit()
        return int(row["id"])

    # ── ideas ──
    def book_for_sizing(self) -> tuple[str, int]:
        """The real book when it holds money, else practice: ideas are sized to the balance the owner has."""
        for kind in ("real", "practice"):
            bid = self.practice.book_id(kind)
            if self.practice.facts(bid).equity > 0:
                return kind, bid
        return "practice", self.practice.book_id("practice")

    def idea(self, signal_id: int, s: Signal) -> dict:
        lane = LANE_OF.get(s.module, s.lane)
        open_same = self.db.fetch_one(
            "SELECT id FROM ideas WHERE lane = %s AND ticker = %s AND module = %s AND status = 'open' "
            "AND expires_at > now()",
            (lane, s.ticker, s.module),
        )
        if open_same:
            return {"status": "open", "id": open_same["id"], "existing": True}
        book, book_id = self.book_for_sizing()
        facts = self.practice.facts(book_id)
        rules = risk_service.rule_set(self.db)
        loss_share = (
            rules.rules["max_loss_per_idea_share"].value
            if "max_loss_per_idea_share" in rules.rules
            else Decimal("0.01")
        )
        entry, stop = _q(s.entry), _q(s.stop)
        qty = size_share = max_loss = collateral = None
        if s.module == "sell_puts" and s.option_symbol:
            opt = occ.parse(s.option_symbol)
            collateral_per = opt.collateral_per_contract
            contracts = int(facts.available // collateral_per) if collateral_per else 0
            qty = Decimal(max(contracts, 1))
            collateral = ledger.money(collateral_per * qty)
            premium = entry or Decimal(0)
            max_loss = ledger.money((opt.strike - (stop or Decimal(0)) - premium) * 100 * qty)
            size_share = (collateral / facts.equity).quantize(Decimal("0.0001")) if facts.equity else None
            u = self.practice.latest_quote(s.ticker) or {}
            spread = None
            if u.get("bid") is not None and u.get("ask") is not None and Decimal(u["ask"]) > 0:
                spread = ((Decimal(u["ask"]) - Decimal(u["bid"])) / Decimal(u["ask"])).quantize(Decimal("0.0001"))
            idea = risk.Idea(
                kind="puts",
                max_loss=max_loss,
                collateral=collateral,
                days_to_earnings=self.practice._days_to_earnings(s.ticker),
                avg_volume=self.practice._avg_volume(s.ticker),
                open_interest=_q(s.evidence.get("best", {}).get("open_interest")),
                spread_share=spread,
                book=book,
            )
        elif entry and stop and entry > stop:
            risk_money = facts.equity * loss_share
            qty = (risk_money / (entry - stop)).quantize(Decimal("0.000001"))
            value = qty * entry
            if facts.equity and value > facts.equity * MAX_SIZE_SHARE:
                qty = (facts.equity * MAX_SIZE_SHARE / entry).quantize(Decimal("0.000001"))
            max_loss = ledger.money(qty * (entry - stop))
            size_share = ((qty * entry) / facts.equity).quantize(Decimal("0.0001")) if facts.equity else None
            u = self.practice.latest_quote(s.ticker) or {}
            spread = None
            if u.get("bid") is not None and u.get("ask") is not None and Decimal(u["ask"]) > 0:
                spread = ((Decimal(u["ask"]) - Decimal(u["bid"])) / Decimal(u["ask"])).quantize(Decimal("0.0001"))
            idea = risk.Idea(
                kind=lane,
                max_loss=max_loss,
                days_to_earnings=self.practice._days_to_earnings(s.ticker),
                about_earnings=s.module in ("pead", "earnings_trade"),
                avg_volume=self.practice._avg_volume(s.ticker),
                spread_share=spread,
                book=book,
            )
        else:
            idea = risk.Idea(kind=lane, max_loss=None, book=book)
        account = risk.Account(
            balance=facts.equity,
            cash_set_aside=facts.available,
            open_ideas=facts.open_positions + len(facts.short_legs) + self._open_ideas(),
            paper_drawdown_share=facts.paper_drawdown_share,
        )
        row = self.db.fetch_one(
            "INSERT INTO ideas (lane, ticker, signal_id, module, status, book, balance, entry, stop, target, quantity, "
            "size_share, "
            "max_loss, collateral, option_symbol, expires_at) VALUES (%s, %s, %s, %s, 'proposed', %s, %s, %s, %s, %s, "
            "%s, "
            "%s, %s, "
            "%s, %s, now() + make_interval(days => %s)) RETURNING id",
            (
                lane,
                s.ticker,
                signal_id,
                s.module,
                book,
                facts.equity,
                entry,
                stop,
                _q(s.target),
                qty,
                size_share,
                max_loss,
                collateral,
                s.option_symbol,
                s.horizon_days,
            ),
        )
        decision = risk_service.check_and_record(self.db, idea, account, "idea", int(row["id"]))
        did = self.db.fetch_one(
            "SELECT id FROM risk_decisions WHERE subject_kind = 'idea' AND subject_id = %s ORDER BY id DESC LIMIT 1",
            (row["id"],),
        )
        status = "open" if decision.passed else "refused"
        refusal = (
            None if decision.passed else (f"{decision.rule}: {decision.reason}" if decision.rule else decision.reason)
        )
        self.db.execute(
            "UPDATE ideas SET status = %s, refusal = %s, risk_decision_id = %s WHERE id = %s",
            (status, refusal, did["id"] if did else None, row["id"]),
        )
        return {"status": status, "id": int(row["id"]), "refusal": refusal}

    def _open_ideas(self) -> int:
        row = self.db.fetch_one("SELECT count(*) AS n FROM ideas WHERE status = 'open' AND expires_at > now()")
        return int(row["n"]) if row else 0

    def expire(self) -> int:
        return self.db.execute("UPDATE ideas SET status = 'expired' WHERE status = 'open' AND expires_at <= now()")
