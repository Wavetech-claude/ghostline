"""Langfuse tracing over its public ingestion API (no SDK, no new dependency): one trace per job, one generation per
Claude call, project `ghostline`. Without keys nothing is sent and the job still runs; a failed send is logged."""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger("ghostline.tracing")


class Tracer:
    def __init__(self, host: str, public_key: str, secret_key: str, env: str, http: httpx.Client | None = None) -> None:
        self.enabled = bool(host and public_key and secret_key)
        self.host = host.rstrip("/")
        self.auth = (public_key, secret_key)
        self.env = env
        self.http = http

    def trace(self, name: str, job_kind: str, metadata: dict[str, Any] | None = None) -> str:
        trace_id = str(uuid.uuid4())
        self._send(
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "trace-create",
                    "timestamp": _now(),
                    "body": {
                        "id": trace_id,
                        "name": name,
                        "sessionId": f"ghostline-{job_kind}-{trace_id[:8]}",
                        "tags": ["ghostline", job_kind, self.env],
                        "metadata": metadata or {},
                    },
                }
            ]
        )
        return trace_id

    def generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        prompt: str,
        output: str,
        usage: dict[str, Any],
        duration_ms: int,
        cost_usd: float | None,
    ) -> None:
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(milliseconds=duration_ms)
        body: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "traceId": trace_id,
            "name": name,
            "model": model,
            "input": prompt[-20000:],
            "output": output[-20000:],
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "metadata": {"cost_usd": cost_usd},
        }
        if usage:
            prompt_tokens = prompt_input_tokens(usage)
            output_tokens = usage.get("output_tokens") or 0
            body["usage"] = {"input": prompt_tokens, "output": output_tokens, "total": prompt_tokens + output_tokens}
            body["metadata"].update({
                "input_uncached": usage.get("input_tokens") or 0,
                "input_cache_read": usage.get("cache_read_input_tokens") or 0,
                "input_cache_write": usage.get("cache_creation_input_tokens") or 0,
            })
        self._send([{"id": str(uuid.uuid4()), "type": "generation-create", "timestamp": _now(), "body": body}])

    def _send(self, batch: list[dict]) -> None:
        if not self.enabled:
            return
        try:
            client = self.http or httpx.Client(timeout=10)
            r = client.post(f"{self.host}/api/public/ingestion", json={"batch": batch}, auth=self.auth)
            if r.status_code >= 300:
                logger.warning("langfuse refused a batch: HTTP %s %s", r.status_code, r.text[:200])
            if self.http is None:
                client.close()
        except Exception as exc:  # noqa: BLE001 — tracing never stops a job
            logger.warning("langfuse send failed: %s: %s", type(exc).__name__, exc)


def prompt_input_tokens(usage: dict[str, Any]) -> int:
    """Every prompt token Claude read. The CLI's `input_tokens` counts only the part after the last cache breakpoint:
    the cached part is in `cache_read_input_tokens` and `cache_creation_input_tokens`. Sending `input_tokens` alone made
    Langfuse show 2 input tokens for a whole brief (found 2026-11-01)."""
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return sum(int(usage.get(k) or 0) for k in keys)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()
