"""Claude in the worker: the runner's environment and command, grounding, the deep dive through a fake Claude (a
memo whose numbers come from the facts, a retry when they do not), chat, the brief and its push, reading a
confirmation, and the routes that queue and read them. No real `claude` runs here."""
import datetime as dt
import json
from decimal import Decimal

import pytest

from ghostline.claude import grounding, jobs, prompts, push, runner
from ghostline.claude.tracing import Tracer
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.storage import MemoryStore
from tests.conftest import settings as make_settings
from tests.conftest import sign_in
from tests.test_practice import bars, deposit, earnings, quote, set_rules

D = Decimal


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


# ── the runner ──────────────────────────────────────────────────────────────────────────────────────────────
def test_the_runner_keeps_only_the_allowed_variables_and_needs_the_token():
    env = runner.build_env({"PATH": "/usr/bin", "HOME": "/root", "FORGEJO_TOKEN": "x", "DATABASE_URL": "pg://",
                            "CLAUDE_CODE_OAUTH_TOKEN": "sub-token"})
    assert env == {"PATH": "/usr/bin", "HOME": "/root", "CLAUDE_CODE_OAUTH_TOKEN": "sub-token"}
    with pytest.raises(runner.ClaudeUnavailable):
        runner.build_env({"PATH": "/usr/bin"})
    with pytest.raises(runner.ClaudeUnavailable):
        runner.build_env({"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"})


def test_the_runner_builds_a_headless_json_call_with_no_tools_by_default():
    cmd = runner.argv("hello", "claude-sonnet-5-5", mcp_config="/tmp/m.json")
    assert cmd[:3] == ["claude", "-p", "hello"] and "--output-format" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd
    cmd = runner.argv("read", "m", tools="Read", allowed_tools="Read", max_turns=3, mcp_config="x")
    assert cmd[cmd.index("--tools") + 1] == "Read" and cmd[cmd.index("--max-turns") + 1] == "3"


class _Completed:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_the_runner_parses_the_json_answer_and_fails_loudly():
    env = {"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    seen = {}

    def fake(cmd, e, timeout, cwd):
        seen["env"] = e
        return _Completed(json.dumps({"type": "result", "result": "the answer", "session_id": "s1",
                                      "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}))
    a = runner.run("q", model="m", environ=env, runner=fake)
    assert a.text == "the answer" and a.usage["input_tokens"] == 10 and seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    with pytest.raises(runner.ClaudeUnavailable):
        runner.run("q", model="m", environ=env, runner=lambda *a: _Completed("", returncode=1, stderr="boom"))
    with pytest.raises(runner.ClaudeUnavailable):
        runner.run("q", model="m", environ=env, runner=lambda *a: _Completed(json.dumps({"is_error": True, "result": "no"})))
    fenced = runner.Answer(text='```json\n{"call": "wait"}\n```', duration_ms=1)
    assert fenced.json() == {"call": "wait"}


# ── grounding ───────────────────────────────────────────────────────────────────────────────────────────────
def test_grounding_strikes_numbers_that_are_not_in_the_facts():
    facts = {"price_stats": {"last_close": 187.45, "sma50": 180.1}, "sizing": {"stop_8pct": {"shares": "12.3456", "position_value": "2,314.00"}},
             "earnings": [{"at": "2026-07-30", "actual": "1.57"}]}
    text = "AAPL closed at 187.45, above its 50-day 180.10; buy 12.35 shares for about $2,314. EPS was 1.57 on 2026-07-30. The 2022 peak was 199.62 and I expect 210."
    out, struck = grounding.ground(text, facts)
    assert struck == ["199.62", "210"]
    assert "[n/a]" in out and "187.45" in out and "180.10" in out and "12.35" in out and "$2,314" in out and "2026-07-30" in out
    # shares as percents, signs, and small counts pass
    out, struck = grounding.ground("down -3% on 3 of 7 points; the 0.015 share is 1.5%", {"a": 0.03, "b": "0.015"})
    assert struck == []


# ── the jobs, with a fake Claude ────────────────────────────────────────────────────────────────────────────
def _facts_ready(client, h, ticker="AAPL"):
    deposit(client, h, 10000)
    quote(ticker, "99.95", "100.05", "100.00")
    bars(ticker, n=30)
    earnings(ticker, 40)
    set_rules(client, h)


def fake_claude(answers):
    """answers: name → text or a function(prompt) → text; records every call."""
    calls = []

    def call(prompt, model, trace_id, name, **kw):
        calls.append({"name": name, "model": model, "prompt": prompt, "kw": kw})
        a = answers.get(name.split(":")[0], answers.get(name))
        return a(prompt) if callable(a) else (a or f"{name} says nothing")
    return call, calls


def test_a_deep_dive_runs_four_analysts_a_debate_and_a_grounded_judge(client):
    h = _h(client)
    _facts_ready(client, h)
    db: Database = client.app.state.db
    memo_ok = json.dumps({"call": "buy", "size_money": None, "stop": None, "bull": "Trades at 100.00 with rules set.",
                          "base": "Holds 100.00.", "bear": "Falls to the 92.00 stop.", "catalysts": "Earnings in 40 days.",
                          "invalidation": "A close under 92.00.", "what_would_change": "A miss.", "confidence_low": 40,
                          "confidence_high": 60})
    call, calls = fake_claude({"analyst": "notes", "debate": "case", "judge": memo_ok})
    j = jobs.Jobs(db, make_settings(), MemoryStore(), call=call, tracer=Tracer("", "", "", "test"))
    jid = jobs.enqueue(db, "deep_dive", {"ticker": "aapl"})
    r = client.post("/deep-dive", json={"ticker": "aapl"}, headers=h)
    assert r.status_code == 202 and r.json()["ticker"] == "AAPL"
    out = j.run_next()
    assert out == {"id": jid, "kind": "deep_dive", "status": "done"}
    names = [c["name"] for c in calls]          # the analysts and the two sides run in parallel: order within a stage varies
    assert set(names[:4]) == {"analyst:fundamentals", "analyst:technical", "analyst:news", "analyst:macro"}
    assert set(names[4:6]) == {"debate:bull", "debate:bear"} and names[6] == "judge"
    assert calls[6]["model"] == "claude-opus-5-5" and "FACTS" in calls[6]["prompt"] and '"sizing"' in calls[6]["prompt"]
    memos = client.get("/deep-dive/AAPL", headers=h).json()
    m = memos["memos"][0]
    assert m["call"] == "buy" and m["grounded"] is True and m["struck"] == [] and memos["running"]["id"] == jid + 1
    job = client.get(f"/jobs/{jid}", headers=h).json()
    assert job["status"] == "done" and job["result"]["memo"]["call"] == "buy"


def test_a_judge_that_invents_a_number_is_retried_then_struck_and_never_a_buy(client):
    h = _h(client)
    _facts_ready(client, h)
    db = client.app.state.db
    bad = json.dumps({"call": "buy", "bull": "Worth 250.00 by June.", "base": "x", "bear": "y", "catalysts": "c",
                      "invalidation": "i", "what_would_change": "w", "confidence_low": 50, "confidence_high": 70})
    call, calls = fake_claude({"analyst": "n", "debate": "d", "judge": bad})
    j = jobs.Jobs(db, make_settings(), MemoryStore(), call=call, tracer=Tracer("", "", "", "test"))
    jobs.enqueue(db, "deep_dive", {"ticker": "AAPL"})
    assert j.run_next()["status"] == "done"
    assert [c["name"] for c in calls][-2:] == ["judge", "judge:retry"] and "250.00" in calls[-1]["prompt"]
    m = client.get("/deep-dive/AAPL", headers=h).json()["memos"][0]
    assert m["grounded"] is False and m["struck"] == ["250.00"] and m["call"] == "wait" and "[n/a]" in m["bull"]


def test_chat_keeps_the_thread_and_grounds_the_answer(client):
    h = _h(client)
    _facts_ready(client, h)
    db = client.app.state.db
    call, calls = fake_claude({"chat": "At 100.00 it sits on its average; I would wait. Some say 177."})
    j = jobs.Jobs(db, make_settings(), MemoryStore(), call=call, tracer=Tracer("", "", "", "test"))
    r = client.post("/chat", json={"message": "Should I add here?", "ticker": "aapl"}, headers=h)
    assert r.status_code == 202
    assert client.get("/chat?ticker=AAPL", headers=h).json()["pending_job"] == r.json()["job_id"]
    assert j.run_next()["status"] == "done"
    assert "Should I add here?" in calls[0]["prompt"] and "The thread is about AAPL" in calls[0]["prompt"]
    t = client.get("/chat?ticker=AAPL", headers=h).json()
    assert [m["role"] for m in t["messages"]] == ["owner", "ghostline"] and t["pending_job"] is None
    assert t["messages"][1]["grounded"] is False and t["messages"][1]["struck"] == ["177"] and "[n/a]" in t["messages"][1]["content"]
    assert client.get("/chat", headers=h).json()["messages"] == []


def test_the_brief_is_written_grounded_and_pushed_only_in_production(client):
    h = _h(client)
    _facts_ready(client, h)
    client.post("/watchlist", json={"ticker": "AAPL"}, headers=h)
    db = client.app.state.db
    call, _ = fake_claude({"brief": "## Top line\nNothing material overnight. AAPL closed 100.00.\n\n## One thing\nSet the keys."})
    pushes = []

    def pusher(db_, settings, title, body, data):
        pushes.append((title, body, data))
        return {"devices": 1, "sent": 1 if settings.apns_enabled else 0, "dry_run": not settings.apns_enabled}
    j = jobs.Jobs(db, make_settings(), MemoryStore(), call=call, tracer=Tracer("", "", "", "test"), pusher=pusher)
    jobs.enqueue(db, "brief", {"push": True})
    assert j.run_next()["status"] == "done"
    b = client.get("/brief", headers=h).json()["brief"]
    assert b["for_date"] == dt.date.today().isoformat() and b["grounded"] is True and "Nothing material" in b["content"]
    assert pushes[0][0] == "Your brief is ready" and pushes[0][2]["kind"] == "brief" and b["pushed_at"] is None
    j2 = jobs.Jobs(db, make_settings(APNS_ENABLED="true"), MemoryStore(), call=call, tracer=Tracer("", "", "", "test"), pusher=pusher)
    jobs.enqueue(db, "brief", {"push": True})
    assert j2.run_next()["status"] == "done"
    assert client.get("/brief", headers=h).json()["brief"]["pushed_at"] is not None
    assert client.post("/brief", headers=h).status_code == 202


def test_a_failed_job_records_its_reason_and_the_queue_goes_on(client):
    h = _h(client)
    db = client.app.state.db

    def boom(prompt, model, trace_id, name, **kw):
        raise runner.ClaudeUnavailable("CLAUDE_CODE_OAUTH_TOKEN is not set")
    j = jobs.Jobs(db, make_settings(), MemoryStore(), call=boom, tracer=Tracer("", "", "", "test"))
    jid = jobs.enqueue(db, "chat", {"ticker": None, "message": "hi"})
    assert j.run_next()["status"] == "failed"
    job = client.get(f"/jobs/{jid}", headers=h).json()
    assert job["status"] == "failed" and "CLAUDE_CODE_OAUTH_TOKEN" in job["error"]
    assert j.run_next() is None
    assert client.get("/jobs/999", headers=h).status_code == 404


def test_reading_a_confirmation_gives_claude_the_file_and_only_the_read_tool(client):
    h = _h(client)
    deposit(client, h, 100)
    t = client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "buy", "quantity": "1", "fill": "100",
                                     "at": "2026-10-03T14:30:00Z"}, headers=h).json()
    up = client.post(f"/attachments?trade_id={t['id']}", files={"file": ("c.png", b"\x89PNG....", "image/png")}, headers=h)
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    db = client.app.state.db
    seen = {}

    def call(prompt, model, trace_id, name, **kw):
        seen.update(kw)
        seen["prompt"] = prompt
        return json.dumps({"ticker": "AAPL", "side": "buy", "quantity": "1", "fill": "100.00", "at": None, "fees": "0",
                           "broker": "Robinhood", "asset": "stock", "option_symbol": None, "notes": "n"})
    j = jobs.Jobs(db, make_settings(), client.app.state.storage, call=call, tracer=Tracer("", "", "", "test"))
    r = client.post(f"/attachments/{aid}/read", headers=h)
    assert r.status_code == 202
    assert j.run_next()["status"] == "done"
    assert seen["tools"] == "Read" and seen["allowed_tools"] == "Read(./confirmation.png)" and "confirmation.png" in seen["prompt"]
    job = client.get(f"/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["result"]["fields"]["broker"] == "Robinhood"
    assert client.post("/attachments/999/read", headers=h).status_code == 404


# ── push and tracing ────────────────────────────────────────────────────────────────────────────────────────
def test_push_is_a_dry_run_on_staging_and_needs_the_key_in_production(client):
    h = _h(client)
    db = client.app.state.db
    assert client.post("/devices", json={"platform": "ios", "token": "abc123def456ghi789jkl", "environment": "production"}, headers=h).status_code in (200, 201)
    out = push.send(db, make_settings(), "t", "b")
    assert out == {"devices": 1, "sent": 0, "failed": 0, "removed": 0, "dry_run": True}
    out = push.send(db, make_settings(APNS_ENABLED="true"), "t", "b")
    assert out["error"] == "APNs key not configured"


def test_tracing_is_silent_without_keys_and_posts_a_batch_with_them():
    import httpx
    assert Tracer("", "", "", "test").enabled is False
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(207, json={"successes": [], "errors": []})
    t = Tracer("https://langfuse.test", "pk", "sk", "test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    tid = t.trace("ghostline:deep_dive", "deep_dive", {"ticker": "AAPL"})
    t.generation(tid, "judge", "claude-opus-5-5", "p", "o", {"input_tokens": 3, "output_tokens": 2}, 1200, 0.02)
    assert sent[0]["batch"][0]["type"] == "trace-create" and "ghostline" in sent[0]["batch"][0]["body"]["tags"]
    assert sent[1]["batch"][0]["body"]["usage"] == {"input": 3, "output": 2, "total": 5}


def test_prompts_render_with_the_rules():
    p = prompts.load("analyst", role="technical", lens="trend", ticker="AAPL", facts="{}")
    assert "Use only the numbers in FACTS" in p and "technical analyst" in p
    assert "{" in prompts.load("judge", ticker="A", facts="{}", analysts="", bull="", bear="")


def test_the_worker_registers_the_claude_jobs(client):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    s = BackgroundScheduler(timezone="America/Los_Angeles")
    ids = schedule.register(s, client.app.state.db, settings=make_settings())
    assert {"claude_poll", "morning_brief"} <= set(ids)


def test_settings_carry_the_claude_defaults():
    s = Settings(DATABASE_URL="x")
    assert s.claude_model == "claude-sonnet-5-5" and s.claude_judge_model == "claude-opus-5-5" and s.langfuse_host == ""


def test_calls_run_in_parallel_but_never_more_than_four_at_once():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    env = {"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    now, peak, lock = [0], [0], threading.Lock()

    def slow(cmd, e, timeout, cwd):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.15)
        with lock:
            now[0] -= 1
        return _Completed(json.dumps({"type": "result", "result": "ok"}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: runner.run("q", model="m", environ=env, runner=slow), range(8)))
    assert peak[0] == runner.MAX_PARALLEL == 4


def test_a_confirmation_read_before_its_trade_exists_is_read_then_removed(client):
    h = _h(client)
    db = client.app.state.db
    store = client.app.state.storage
    assert client.post("/confirmations/read", files={"file": ("c.txt", b"hi", "text/plain")}, headers=h).status_code == 415
    r = client.post("/confirmations/read", files={"file": ("c.jpg", b"\xff\xd8jpeg", "image/jpeg")}, headers=h)
    assert r.status_code == 202, r.text
    key = db.fetch_one("SELECT payload->>'object_key' AS k FROM claude_jobs WHERE id = %s", (r.json()["job_id"],))["k"]
    assert key.startswith("inbox/") and store.get(key)[0] == b"\xff\xd8jpeg"
    seen = {}

    def call(prompt, model, trace_id, name, **kw):
        seen.update(kw)
        return json.dumps({"ticker": "MSFT", "side": "sell", "quantity": "2", "fill": "410.10", "asset": "stock"})
    j = jobs.Jobs(db, make_settings(), store, call=call, tracer=Tracer("", "", "", "test"))
    assert j.run_next()["status"] == "done"
    assert seen["allowed_tools"] == "Read(./confirmation.jpg)"
    with pytest.raises(KeyError):
        store.get(key)
    job = client.get(f"/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["result"]["fields"]["ticker"] == "MSFT" and job["result"]["object_key"] == key
    assert client.post("/confirmations/read", files={"file": ("c.png", b"x", "image/png")}).status_code == 401
