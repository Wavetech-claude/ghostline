"""Headless Claude: `claude -p` on an allow-listed environment, JSON output, no tools unless a job names them, one
call at a time per worker. The subscription token is the only credential; an ANTHROPIC_* variable stops the worker
before this module is reached (worker/__main__). Modelled on the auto-fixer's runner (agents/autofix/claude_code.py)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ghostline.claude")

ALLOWED_ENV = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TMP", "TEMP", "CLAUDE_CODE_OAUTH_TOKEN"})
MAX_PARALLEL = 4          # the deep dive's four analysts run at once; no more claude processes than that
_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ClaudeUnavailable(Exception):
    """No subscription token, no CLI, or the call failed: the job fails loudly, nothing is guessed."""


@dataclass
class Answer:
    text: str
    duration_ms: int
    session_id: str | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """The answer as JSON, with or without a code fence."""
        body = _FENCE.sub("", self.text.strip())
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            body = body[start : end + 1]
        return json.loads(body)


def build_env(base: dict[str, str]) -> dict[str, str]:
    if any(k.upper().startswith("ANTHROPIC_") for k in base):
        raise ClaudeUnavailable("an ANTHROPIC_* variable is set: Claude runs on the subscription only")
    env = {k: v for k, v in base.items() if k in ALLOWED_ENV}
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise ClaudeUnavailable("CLAUDE_CODE_OAUTH_TOKEN is not set: Claude runs on the subscription only")
    return env


def _empty_mcp_config() -> str:
    fd, path = tempfile.mkstemp(prefix="ghostline-nomcp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write('{ "mcpServers": {} }')
    return path


def argv(
    prompt: str,
    model: str,
    *,
    max_turns: int = 1,
    tools: str = "",
    allowed_tools: str = "",
    mcp_config: str = "",
    effort: str | None = None,
) -> list[str]:
    out = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config,
        "--max-turns",
        str(max_turns),
        "--tools",
        tools,
    ]
    if allowed_tools:
        out += ["--allowedTools", allowed_tools]
    if effort:
        out += ["--effort", effort]
    return out


def run(
    prompt: str,
    *,
    model: str,
    max_turns: int = 1,
    tools: str = "",
    allowed_tools: str = "",
    timeout_s: int = 420,
    cwd: str | None = None,
    effort: str | None = None,
    environ: dict[str, str] | None = None,
    runner=None,
) -> Answer:
    """One call. `runner` (tests) replaces subprocess.run and receives (argv, env, timeout, cwd)."""
    env = build_env(environ if environ is not None else dict(os.environ))
    mcp = _empty_mcp_config()
    cmd = argv(
        prompt, model, max_turns=max_turns, tools=tools, allowed_tools=allowed_tools, mcp_config=mcp, effort=effort
    )
    started = time.monotonic()
    try:
        with _SLOTS:
            if runner is not None:
                completed = runner(cmd, env, timeout_s, cwd)
            else:
                completed = subprocess.run(
                    cmd,
                    env=env,
                    cwd=cwd,
                    capture_output=True,
                    text=True,  # noqa: S603
                    timeout=timeout_s,
                    check=False,
                )
    except FileNotFoundError as exc:
        raise ClaudeUnavailable("the claude CLI is not installed in this image") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeUnavailable(f"claude did not answer within {timeout_s}s") from exc
    finally:
        try:
            os.unlink(mcp)
        except OSError:
            pass
    ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        raise ClaudeUnavailable(f"claude exited {completed.returncode}: {(completed.stderr or '')[-400:]}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeUnavailable(f"claude answered without JSON: {(completed.stdout or '')[:200]}") from exc
    if isinstance(data, list):  # a stream of messages: the result is the last one
        data = next(
            (d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), data[-1] if data else {}
        )
    if data.get("is_error"):
        raise ClaudeUnavailable(f"claude reported an error: {str(data.get('result') or '')[:300]}")
    text = data.get("result") if isinstance(data.get("result"), str) else json.dumps(data.get("result"))
    logger.info("claude answered in %d ms (%s)", ms, model)
    return Answer(
        text=text or "",
        duration_ms=ms,
        session_id=data.get("session_id"),
        cost_usd=data.get("total_cost_usd"),
        usage=data.get("usage") or {},
        raw=data,
    )
