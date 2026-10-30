from __future__ import annotations

import logging

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ghostline.auth import Auth, OwnerSub

logger = logging.getLogger("ghostline.auth")
router = APIRouter()


class AppleSignIn(BaseModel):
    identity_token: str = Field(min_length=20)
    platform: str = Field(default="ios", pattern="^(ios|macos)$")


class Session(BaseModel):
    session_token: str
    expires_at: str
    sub: str


@router.post("/auth/apple", response_model=Session)
def sign_in_with_apple(body: AppleSignIn, request: Request) -> Session:
    auth: Auth = request.app.state.auth
    try:
        identity = auth.verify_identity_token(body.identity_token)
    except jwt.PyJWTError as exc:
        logger.info("sign-in refused: token not accepted (%s)", type(exc).__name__)
        raise HTTPException(status_code=401, detail="sign-in refused") from None
    if not auth.allowed(identity):
        # the sub is logged so the owner can pin the account by its id if the email is hidden
        logger.warning("sign-in refused: apple user %s is not the owner", identity.sub)
        raise HTTPException(status_code=401, detail="sign-in refused")
    db = request.app.state.db
    db.execute("INSERT INTO owner_account (sub, email) VALUES (%s, %s) "
               "ON CONFLICT (sub) DO UPDATE SET email = COALESCE(EXCLUDED.email, owner_account.email), "
               "last_sign_in_at = now()", (identity.sub, identity.email))
    token, jti, exp = auth.issue_session(identity.sub)
    db.execute("INSERT INTO sessions (jti, sub, platform, expires_at) VALUES (%s, %s, %s, %s)",
               (jti, identity.sub, body.platform, exp))
    return Session(session_token=token, expires_at=exp.isoformat(), sub=identity.sub)


@router.post("/auth/sign-out")
def sign_out(request: Request, sub: str = OwnerSub) -> dict:
    token = request.headers.get("authorization", "")[7:].strip()
    auth: Auth = request.app.state.auth
    claims = auth.read_session(token)
    request.app.state.db.execute("UPDATE sessions SET revoked = true WHERE jti = %s", (claims["jti"],))
    return {"ok": True}


@router.get("/account")
def account(request: Request, sub: str = OwnerSub) -> dict:
    row = request.app.state.db.fetch_one(
        "SELECT sub, email, created_at, last_sign_in_at FROM owner_account WHERE sub = %s", (sub,))
    if not row:
        raise HTTPException(status_code=404, detail="no account")
    email = row.get("email") or ""
    masked = (email[:2] + "…" + email[email.index("@"):]) if "@" in email else ""
    return {"sub": row["sub"], "email_masked": masked, "created_at": row["created_at"].isoformat(),
            "last_sign_in_at": row["last_sign_in_at"].isoformat() if row.get("last_sign_in_at") else None}
