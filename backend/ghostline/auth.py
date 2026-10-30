"""Sign in with Apple for exactly one account, then the backend's own session token.

- The app sends Apple's identity token (a JWT signed by Apple). It is verified against Apple's public keys
  (https://appleid.apple.com/auth/keys, cached), issuer https://appleid.apple.com, audience = the bundle id.
- The one allowed account is GHOSTLINE_OWNER_APPLE_ID: the Apple ID email, or the Apple user id (`sub`). Empty =
  nobody gets in. There is no sign-up route. A refused attempt logs the `sub` so the owner can pin it.
- A session is an HS256 JWT signed with GHOSTLINE_SESSION_SECRET, with a `jti` stored in `sessions` (revocable).
- Tests and CI use a test-only signer: with GHOSTLINE_TEST_SIGNER_KEY set, an HS256 token with issuer
  `ghostline-test` is accepted in place of Apple's. Production never sets it.
- The worker's credential (X-Worker-Token) is never the owner's: it opens the worker's routes only.
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request

from ghostline.config import Settings

logger = logging.getLogger("ghostline.auth")

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
TEST_ISSUER = "ghostline-test"
_JWKS_TTL_S = 3600


@dataclass(frozen=True)
class AppleIdentity:
    sub: str
    email: str | None


class AppleKeys:
    """Apple's signing keys, fetched once an hour."""

    def __init__(self, url: str = APPLE_KEYS_URL) -> None:
        self.url = url
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    def key(self, kid: str) -> Any:
        if kid not in self._keys or time.time() - self._fetched_at > _JWKS_TTL_S:
            r = httpx.get(self.url, timeout=10)
            r.raise_for_status()
            self._keys = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(k) for k in r.json().get("keys", [])}
            self._fetched_at = time.time()
        if kid not in self._keys:
            raise jwt.InvalidKeyError(f"unknown Apple key {kid}")
        return self._keys[kid]


class Auth:
    def __init__(self, settings: Settings, apple_keys: AppleKeys | None = None) -> None:
        self.settings = settings
        self.apple_keys = apple_keys or AppleKeys()

    # ── Apple's identity token ────────────────────────────────────────────────────────────────────────────
    def verify_identity_token(self, token: str) -> AppleIdentity:
        """Raises jwt.PyJWTError (401 for the route) when the token is not Apple's for this app, or not the test
        signer's in a test environment."""
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
        if unverified.get("iss") == TEST_ISSUER:
            if not self.settings.test_signer_key:
                raise jwt.InvalidIssuerError("the test signer is not enabled here")
            claims = jwt.decode(token, self.settings.test_signer_key, algorithms=["HS256"],
                                audience=self.settings.apple_audience, issuer=TEST_ISSUER)
        else:
            key = self.apple_keys.key(header.get("kid", ""))
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.settings.apple_audience,
                                issuer=APPLE_ISSUER)
        sub = str(claims.get("sub") or "")
        if not sub:
            raise jwt.MissingRequiredClaimError("sub")
        return AppleIdentity(sub=sub, email=(claims.get("email") or None))

    def allowed(self, identity: AppleIdentity) -> bool:
        owner = self.settings.owner_apple_id.strip().lower()
        if not owner:
            return False
        return identity.sub.lower() == owner or (identity.email or "").strip().lower() == owner

    # ── sessions ──────────────────────────────────────────────────────────────────────────────────────────
    def issue_session(self, sub: str) -> tuple[str, str, dt.datetime]:
        """(token, jti, expires_at)."""
        if not self.settings.session_secret:
            raise RuntimeError("GHOSTLINE_SESSION_SECRET is not set")
        now = dt.datetime.now(dt.UTC)
        exp = now + dt.timedelta(days=self.settings.session_days)
        jti = str(uuid.uuid4())
        token = jwt.encode({"sub": sub, "jti": jti, "iat": int(now.timestamp()), "exp": int(exp.timestamp()),
                            "iss": "ghostline"}, self.settings.session_secret, algorithm="HS256")
        return token, jti, exp

    def read_session(self, token: str) -> dict[str, Any]:
        """The session's claims; raises jwt.PyJWTError when the token is not ours or expired."""
        if not self.settings.session_secret:
            raise jwt.InvalidKeyError("no session secret")
        return jwt.decode(token, self.settings.session_secret, algorithms=["HS256"], issuer="ghostline")

    def worker_ok(self, presented: str | None) -> bool:
        expected = self.settings.worker_token
        return bool(expected and presented) and hmac.compare_digest(expected, presented or "")


# ── FastAPI dependencies ──────────────────────────────────────────────────────────────────────────────────
def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


def require_owner(request: Request, authorization: str | None = Header(default=None)) -> str:
    """The owner's `sub`, from a live session token. 401 otherwise (a worker token is not a session)."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="sign in")
    auth: Auth = request.app.state.auth
    try:
        claims = auth.read_session(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="session not valid") from None
    row = request.app.state.db.fetch_one("SELECT revoked, expires_at FROM sessions WHERE jti = %s", (claims["jti"],))
    if not row or row["revoked"] or row["expires_at"] < dt.datetime.now(dt.UTC):
        raise HTTPException(status_code=401, detail="session not valid")
    return str(claims["sub"])


def require_worker(request: Request, x_worker_token: str | None = Header(default=None)) -> str:
    """The worker's routes: 403 for anything but the worker's token (the owner's session is not the worker)."""
    auth: Auth = request.app.state.auth
    if not auth.worker_ok(x_worker_token):
        raise HTTPException(status_code=403, detail="worker only")
    return "worker"


OwnerSub = Depends(require_owner)
WorkerCaller = Depends(require_worker)
