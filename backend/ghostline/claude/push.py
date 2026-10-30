"""Apple push over APNs HTTP/2 with a p8 token (ES256), topic com.wavetech.ghostline. Production sends; staging logs
what it would have sent (APNS_ENABLED=false). A device Apple rejects as gone is removed."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx
import jwt

from ghostline.config import Settings
from ghostline.db import Database

logger = logging.getLogger("ghostline.push")
PROD = "https://api.push.apple.com"
SANDBOX = "https://api.sandbox.push.apple.com"


def token(settings: Settings, now: float | None = None) -> str:
    key = (
        base64.b64decode(settings.apns_key_p8).decode()
        if not settings.apns_key_p8.startswith("-----")
        else settings.apns_key_p8
    )
    return jwt.encode(
        {"iss": settings.apns_team_id, "iat": int(now or time.time())},
        key,
        algorithm="ES256",
        headers={"kid": settings.apns_key_id},
    )


def send(
    db: Database,
    settings: Settings,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    devices = db.fetch_all("SELECT token, platform, environment FROM devices ORDER BY registered_at")
    out = {"devices": len(devices), "sent": 0, "failed": 0, "removed": 0, "dry_run": not settings.apns_enabled}
    if not devices:
        return out
    if not settings.apns_enabled:
        logger.info("push (not sent, APNS_ENABLED=false): %s — %s → %d device(s)", title, body, len(devices))
        return out
    if not (settings.apns_key_id and settings.apns_team_id and settings.apns_key_p8):
        out["error"] = "APNs key not configured"
        return out
    bearer = token(settings)
    client = http or httpx.Client(http2=True, timeout=15)
    try:
        for d in devices:
            host = SANDBOX if d.get("environment") == "sandbox" else PROD
            payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}, **(data or {})}
            r = client.post(
                f"{host}/3/device/{d['token']}",
                json=payload,
                headers={
                    "authorization": f"bearer {bearer}",
                    "apns-topic": settings.apns_topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
            )
            if r.status_code == 200:
                out["sent"] += 1
            elif r.status_code in (400, 410) and ("BadDeviceToken" in r.text or "Unregistered" in r.text):
                db.execute("DELETE FROM devices WHERE token = %s", (d["token"],))
                out["removed"] += 1
            else:
                out["failed"] += 1
                logger.warning("apns %s for %s…: %s", r.status_code, d["token"][:8], r.text[:200])
    finally:
        if http is None:
            client.close()
    return out
