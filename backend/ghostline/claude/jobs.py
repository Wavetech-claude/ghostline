"""The Claude job queue: the api enqueues, the worker runs one job at a time. Deep dive (four analysts in parallel,
bull against bear, a judge → a grounded memo), chat, the morning brief with its push, and reading a confirmation.
Every call is traced to Langfuse; every memo and answer is grounded against the facts it was given."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ghostline.claude import facts as F
from ghostline.claude import grounding, prompts, push, runner
from ghostline.claude.tracing import Tracer
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.storage import ObjectStore

logger = logging.getLogger("ghostline.claude.jobs")

ANALYSTS = {
    "fundamentals": "valuation, growth, margins, balance sheet; what the business earns and what the market pays "
    "for it",
    "technical": "trend, the moving averages, the 52-week range, volume, momentum (RSI), the modules' signals",
    "news": "the headlines, filings and the earnings calendar: what is new, dated, and whether it is priced",
    "macro": "the regime, rates, the dollar and India–US backdrop; how the environment treats this kind of stock",
}


def enqueue(db: Database, kind: str, payload: dict[str, Any]) -> int:
    row = db.fetch_one(
        "INSERT INTO claude_jobs (kind, payload) VALUES (%s, %s::jsonb) RETURNING id",
        (kind, json.dumps(payload, default=str)),
    )
    with db.connection() as conn:
        conn.commit()
    return int(row["id"])


def job(db: Database, job_id: int) -> dict | None:
    row = db.fetch_one("SELECT * FROM claude_jobs WHERE id = %s", (job_id,))
    return _j(row) if row else None


def _j(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        elif k in ("payload", "result") and isinstance(v, str):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out


class Jobs:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        storage: ObjectStore | None = None,
        call=None,
        tracer: Tracer | None = None,
        pusher=None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage
        self.call = call or self._call
        self.tracer = tracer or Tracer(
            settings.langfuse_host, settings.langfuse_public_key, settings.langfuse_secret_key, settings.ghostline_env
        )
        self.pusher = pusher or push.send

    # ── the worker's tick ──
    def run_next(self) -> dict | None:
        row = self.db.fetch_one(
            "UPDATE claude_jobs SET status = 'running', started_at = now() WHERE id = "
            "(SELECT id FROM claude_jobs WHERE status = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING *"
        )
        if not row:
            return None
        with self.db.connection() as conn:
            conn.commit()
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
        trace_id = self.tracer.trace(f"ghostline:{row['kind']}", row["kind"], payload)
        try:
            result = {
                "deep_dive": self.deep_dive,
                "chat": self.chat,
                "brief": self.brief,
                "read_attachment": self.read_attachment,
            }[row["kind"]](row["id"], payload, trace_id)
            self.db.execute(
                "UPDATE claude_jobs SET status = 'done', result = %s::jsonb, trace_id = %s, finished_at = now() "
                "WHERE id = %s",
                (json.dumps(result, default=str), trace_id, row["id"]),
            )
            return {"id": row["id"], "kind": row["kind"], "status": "done"}
        except Exception as exc:  # noqa: BLE001 — the job fails loudly with its reason; the queue goes on
            logger.warning("claude job %s (%s) failed: %s: %s", row["id"], row["kind"], type(exc).__name__, exc)
            self.db.execute(
                "UPDATE claude_jobs SET status = 'failed', error = %s, trace_id = %s, finished_at = now() "
                "WHERE id = %s",
                (f"{type(exc).__name__}: {exc}"[:800], trace_id, row["id"]),
            )
            return {"id": row["id"], "kind": row["kind"], "status": "failed"}

    # ── one call ──
    def _call(self, prompt: str, model: str, trace_id: str, name: str, **kw) -> str:
        a = runner.run(prompt, model=model, **kw)
        self.tracer.generation(trace_id, name, model, prompt, a.text, a.usage, a.duration_ms, a.cost_usd)
        return a.text

    # ── deep dive ──
    def deep_dive(self, job_id: int, payload: dict, trace_id: str) -> dict:
        ticker = str(payload["ticker"]).upper()
        facts = F.ticker_facts(self.db, ticker)
        facts_json = json.dumps(facts, indent=1, default=str)
        model = self.settings.claude_model
        with ThreadPoolExecutor(max_workers=4) as pool:
            notes = dict(
                zip(
                    ANALYSTS,
                    pool.map(
                        lambda item: self.call(
                            prompts.load("analyst", role=item[0], lens=item[1], ticker=ticker, facts=facts_json),
                            model,
                            trace_id,
                            f"analyst:{item[0]}",
                        ),
                        ANALYSTS.items(),
                    ),
                    strict=True,
                )
            )
        analysts = "\n\n".join(f"### {k}\n{v}" for k, v in notes.items())
        with ThreadPoolExecutor(max_workers=2) as pool:
            bull, bear = pool.map(
                lambda side: self.call(
                    prompts.load("debate", side=side, ticker=ticker, facts=facts_json, analysts=analysts),
                    model,
                    trace_id,
                    f"debate:{side}",
                ),
                ("bull", "bear"),
            )
        memo, struck = self._judge(ticker, facts, facts_json, analysts, bull, bear, trace_id)
        idea = self.db.fetch_one(
            "SELECT id FROM ideas WHERE ticker = %s AND status IN ('open', 'refused') AND expires_at > now() "
            "ORDER BY status = 'open' DESC, id DESC LIMIT 1",
            (ticker,),
        )
        row = self.db.fetch_one(
            "INSERT INTO memos (idea_id, ticker, call, size_money, bull, base, bear, catalysts, invalidation, "
            "confidence_low, "
            "confidence_high, grounded, trace_id, job_id, struck, what_would_change, facts) "
            "VALUES (%s, %s, %s, %s, %s, "
            "%s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb) RETURNING id",
            (
                idea["id"] if idea else None,
                ticker,
                memo["call"],
                memo.get("size_money"),
                memo.get("bull"),
                memo.get("base"),
                memo.get("bear"),
                memo.get("catalysts"),
                memo.get("invalidation"),
                memo.get("confidence_low"),
                memo.get("confidence_high"),
                not struck,
                trace_id,
                job_id,
                json.dumps(struck),
                memo.get("what_would_change"),
                json.dumps(facts, default=str),
            ),
        )
        with self.db.connection() as conn:
            conn.commit()
        return {
            "memo_id": int(row["id"]),
            "ticker": ticker,
            "call": memo["call"],
            "grounded": not struck,
            "struck": struck,
            "analysts": notes,
            "bull": bull,
            "bear": bear,
            "memo": memo,
        }

    def _judge(self, ticker, facts, facts_json, analysts, bull, bear, trace_id) -> tuple[dict, list[str]]:
        prompt = prompts.load("judge", ticker=ticker, facts=facts_json, analysts=analysts, bull=bull, bear=bear)
        text = self.call(prompt, self.settings.claude_judge_model, trace_id, "judge")
        memo = _memo(text)
        memo, struck = _ground_memo(memo, facts)
        if struck:  # one retry naming the numbers that were not in the facts
            retry = prompt + (
                "\n\nYour previous answer used numbers that are not in FACTS: "
                + ", ".join(struck)
                + ". Answer again using only numbers from FACTS."
            )
            memo2 = _memo(self.call(retry, self.settings.claude_judge_model, trace_id, "judge:retry"))
            memo2, struck2 = _ground_memo(memo2, facts)
            if len(struck2) < len(struck):
                memo, struck = memo2, struck2
        return memo, struck

    # ── chat ──
    def chat(self, job_id: int, payload: dict, trace_id: str) -> dict:
        ticker = (payload.get("ticker") or "").upper() or None
        message = str(payload["message"])
        facts = (
            F.ticker_facts(self.db, ticker)
            if ticker
            else {"note": "no ticker in this thread", **F.brief_facts(self.db)}
        )
        memo = (
            self.db.fetch_one(
                "SELECT call, size_money, bull, base, bear, catalysts, invalidation, what_would_change, created_at "
                "FROM memos WHERE ticker = %s ORDER BY id DESC LIMIT 1",
                (ticker,),
            )
            if ticker
            else None
        )
        thread = self.db.fetch_all(
            "SELECT role, content FROM chat_messages WHERE ticker IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT 20",
            (ticker,),
        )
        thread_text = "\n".join(f"{m['role']}: {m['content']}" for m in reversed(thread)) or "(none)"
        prompt = prompts.load(
            "chat",
            about=f" The thread is about {ticker}." if ticker else "",
            facts=json.dumps(facts, indent=1, default=str),
            memo=json.dumps(memo, default=str) if memo else "(none)",
            thread=thread_text,
            message=message,
        )
        text = self.call(prompt, self.settings.claude_model, trace_id, "chat")
        answer, struck = grounding.ground(text, {"facts": facts, "memo": memo})
        row = self.db.fetch_one(
            "INSERT INTO chat_messages (ticker, role, content, job_id, grounded, struck, trace_id) "
            "VALUES (%s, 'ghostline', %s, %s, %s, %s::jsonb, %s) RETURNING id",
            (ticker, answer, job_id, not struck, json.dumps(struck), trace_id),
        )
        with self.db.connection() as conn:
            conn.commit()
        return {
            "message_id": int(row["id"]),
            "ticker": ticker,
            "answer": answer,
            "grounded": not struck,
            "struck": struck,
        }

    # ── the brief ──
    def brief(self, job_id: int, payload: dict, trace_id: str) -> dict:
        facts = F.brief_facts(self.db)
        text = self.call(
            prompts.load("brief", for_date=facts["for_date"], facts=json.dumps(facts, indent=1, default=str)),
            self.settings.claude_model,
            trace_id,
            "brief",
        )
        content, struck = grounding.ground(text, facts)
        row = self.db.fetch_one(
            "INSERT INTO briefs (for_date, content, facts, grounded, struck, trace_id) VALUES (%s, %s, %s::jsonb, %s, "
            "%s::jsonb, %s) "
            "ON CONFLICT (for_date) DO UPDATE SET content = EXCLUDED.content, facts = EXCLUDED.facts, "
            "grounded = EXCLUDED.grounded, "
            "struck = EXCLUDED.struck, trace_id = EXCLUDED.trace_id, created_at = now() RETURNING id",
            (facts["for_date"], content, json.dumps(facts, default=str), not struck, json.dumps(struck), trace_id),
        )
        with self.db.connection() as conn:
            conn.commit()
        pushed = None
        if payload.get("push", True):
            pushed = self.pusher(
                self.db,
                self.settings,
                "Your brief is ready",
                content.split("\n", 1)[0].lstrip("# ")[:120],
                {"kind": "brief", "for_date": facts["for_date"]},
            )
            if pushed.get("sent"):
                self.db.execute("UPDATE briefs SET pushed_at = now() WHERE id = %s", (row["id"],))
        return {
            "brief_id": int(row["id"]),
            "for_date": facts["for_date"],
            "grounded": not struck,
            "struck": struck,
            "push": pushed,
        }

    # ── a confirmation screenshot ──
    def read_attachment(self, job_id: int, payload: dict, trace_id: str) -> dict:
        if self.storage is None:
            raise RuntimeError("no object store")
        if payload.get("object_key"):        # a confirmation read before its trade exists (the form's pre-fill)
            att = {"object_key": payload["object_key"], "content_type": payload.get("content_type") or ""}
        else:
            att = self.db.fetch_one(
                "SELECT object_key, content_type FROM attachments WHERE id = %s", (int(payload["attachment_id"]),)
            )
            if not att:
                raise RuntimeError("no such attachment")
        data, content_type = self.storage.get(att["object_key"])
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/heic": ".heic", "application/pdf": ".pdf"}.get(
            content_type or att["content_type"], ""
        )
        name = f"confirmation{suffix}"
        with tempfile.TemporaryDirectory(prefix="ghostline-read-") as d:
            path = os.path.join(d, name)
            with open(path, "wb") as fh:
                fh.write(data)
            # Read is the only tool, approved for this one file: an injected instruction in the image cannot make
            # Claude read anything else (headless runs deny every tool use that is not pre-approved).
            text = self.call(
                prompts.load("read_attachment", path=path),
                self.settings.claude_model,
                trace_id,
                "read_attachment",
                tools="Read",
                allowed_tools=f"Read(./{name})",
                max_turns=3,
                cwd=d,
            )
        if payload.get("object_key"):        # the inbox copy is not kept: the form attaches the file on save
            try:
                self.storage.delete(payload["object_key"])
            except Exception as exc:  # noqa: BLE001 — a leftover inbox object is logged, never fatal
                logger.warning("inbox object %s not deleted: %s", payload["object_key"], type(exc).__name__)
        fields = _memo(text)
        return {"attachment_id": payload.get("attachment_id"), "object_key": payload.get("object_key"),
                "fields": fields}


def _memo(text: str) -> dict:
    a = runner.Answer(text=text, duration_ms=0)
    data = a.json()
    if not isinstance(data, dict):
        raise RuntimeError("the judge did not answer with a JSON object")
    return data


def _ground_memo(memo: dict, facts: dict) -> tuple[dict, list[str]]:
    struck: list[str] = []
    out = dict(memo)
    for key in ("bull", "base", "bear", "catalysts", "invalidation", "what_would_change"):
        if isinstance(out.get(key), str):
            out[key], s = grounding.ground(out[key], facts)
            struck += s
    for key in ("size_money", "stop"):
        v = out.get(key)
        if v is not None:
            txt, s = grounding.ground(str(v), facts.get("sizing") or {})
            if s:
                struck += s
                out[key] = None
    call = str(out.get("call") or "wait").lower()
    out["call"] = call if call in ("buy", "wait", "avoid") else "wait"
    if struck and out["call"] == "buy":
        out["call"] = "wait"  # a buy on a number that is not stored is not a buy
    return out, struck
