from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ghostline.auth import OwnerSub

router = APIRouter()


class Device(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    platform: str = Field(pattern="^(ios|macos)$")
    environment: str = Field(default="production", pattern="^(sandbox|production)$")


@router.post("/devices")
def register_device(body: Device, request: Request, sub: str = OwnerSub) -> dict:
    request.app.state.db.execute(
        "INSERT INTO devices (token, platform, environment, sub) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (token) DO UPDATE SET platform = EXCLUDED.platform, environment = EXCLUDED.environment, "
        "sub = EXCLUDED.sub, last_seen_at = now()", (body.token, body.platform, body.environment, sub))
    return {"ok": True}


@router.get("/devices")
def list_devices(request: Request, sub: str = OwnerSub) -> dict:
    rows = request.app.state.db.fetch_all(
        "SELECT platform, environment, registered_at, last_seen_at, right(token, 6) AS token_tail "
        "FROM devices WHERE sub = %s ORDER BY registered_at", (sub,))
    return {"devices": [{**r, "registered_at": r["registered_at"].isoformat(),
                         "last_seen_at": r["last_seen_at"].isoformat()} for r in rows]}


@router.delete("/devices/{token}")
def remove_device(token: str, request: Request, sub: str = OwnerSub) -> dict:
    n = request.app.state.db.execute("DELETE FROM devices WHERE token = %s AND sub = %s", (token, sub))
    return {"ok": True, "removed": n}
