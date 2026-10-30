"""Deep dives, chat, the brief and reading a confirmation: the api enqueues a job and answers with its id; the app
polls the job, then reads the memo, the thread or the brief."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

from ghostline.auth import OwnerSub
from ghostline.claude import jobs

router = APIRouter()


def _row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        elif k in ("struck", "facts") and isinstance(v, str):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out


class DeepDiveIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


@router.post("/deep-dive", status_code=202)
def deep_dive(body: DeepDiveIn, request: Request, sub: str = OwnerSub) -> dict:
    return {"job_id": jobs.enqueue(request.app.state.db, "deep_dive", {"ticker": body.ticker}), "ticker": body.ticker}


@router.get("/deep-dive/{ticker}")
def memos(ticker: str, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = ticker.strip().upper()
    rows = db.fetch_all(
        "SELECT id, idea_id, ticker, call, size_money, bull, base, bear, catalysts, invalidation, what_would_change, "
        "confidence_low, confidence_high, grounded, struck, trace_id, job_id, created_at FROM memos WHERE ticker = %s "
        "ORDER BY id DESC LIMIT 5",
        (t,),
    )
    running = db.fetch_one(
        "SELECT id, status, created_at FROM claude_jobs WHERE kind = 'deep_dive' AND payload->>'ticker' = %s "
        "AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
        (t,),
    )
    return {"ticker": t, "memos": [_row(r) for r in rows], "running": _row(running) if running else None}


@router.get("/jobs/{job_id}")
def job(job_id: int, request: Request, sub: str = OwnerSub) -> dict:
    row = jobs.job(request.app.state.db, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="no such job")
    return row


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    ticker: str | None = Field(default=None, max_length=12)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v and v.strip() else None


@router.post("/chat", status_code=202)
def chat(body: ChatIn, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    db.execute(
        "INSERT INTO chat_messages (ticker, role, content) VALUES (%s, 'owner', %s)", (body.ticker, body.message)
    )
    job_id = jobs.enqueue(db, "chat", {"ticker": body.ticker, "message": body.message})
    return {"job_id": job_id, "ticker": body.ticker}


@router.get("/chat")
def thread(
    request: Request, ticker: str | None = None, limit: int = Query(default=50, le=200), sub: str = OwnerSub
) -> dict:
    db = request.app.state.db
    t = ticker.strip().upper() if ticker and ticker.strip() else None
    rows = db.fetch_all(
        "SELECT id, ticker, role, content, grounded, struck, job_id, created_at FROM chat_messages "
        "WHERE ticker IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT %s",
        (t, limit),
    )
    pending = db.fetch_one(
        "SELECT id FROM claude_jobs WHERE kind = 'chat' AND status IN ('queued', 'running') "
        "AND payload->>'ticker' IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT 1",
        (t,),
    )
    return {
        "ticker": t,
        "messages": [_row(r) for r in reversed(rows)],
        "pending_job": pending["id"] if pending else None,
    }


@router.get("/brief")
def brief(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    row = db.fetch_one(
        "SELECT id, for_date, content, grounded, struck, trace_id, pushed_at, created_at FROM briefs "
        "ORDER BY for_date DESC LIMIT 1"
    )
    pending = db.fetch_one(
        "SELECT id FROM claude_jobs WHERE kind = 'brief' AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1"
    )
    return {"brief": _row(row) if row else None, "pending_job": pending["id"] if pending else None}


@router.post("/brief", status_code=202)
def brief_now(request: Request, sub: str = OwnerSub) -> dict:
    return {"job_id": jobs.enqueue(request.app.state.db, "brief", {"push": False})}


@router.post("/confirmations/read", status_code=202)
async def read_confirmation(request: Request, file: Annotated[UploadFile, File()], sub: str = OwnerSub) -> dict:
    """A confirmation read before its trade exists: stored in the inbox, read by Claude, the fields pre-fill the
    form."""
    content_type = (file.content_type or "").lower()
    if content_type not in ("image/png", "image/jpeg", "image/heic", "application/pdf"):
        raise HTTPException(status_code=415, detail=f"not a screenshot or PDF: {content_type or 'unknown type'}")
    data = await file.read()
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="the file is empty or over 15 MB")
    key = f"inbox/{uuid.uuid4()}"
    request.app.state.storage.put(key, data, content_type)
    payload = {"object_key": key, "content_type": content_type}
    return {"job_id": jobs.enqueue(request.app.state.db, "read_attachment", payload)}


@router.post("/attachments/{attachment_id}/read", status_code=202)
def read_attachment(attachment_id: int, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if not db.fetch_one("SELECT 1 AS ok FROM attachments WHERE id = %s", (attachment_id,)):
        raise HTTPException(status_code=404, detail="no such attachment")
    return {"job_id": jobs.enqueue(db, "read_attachment", {"attachment_id": attachment_id})}
