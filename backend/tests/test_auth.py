"""Sign in with Apple for exactly one account; the backend's own session; the worker's token is not a session."""
import time

import jwt
from fastapi.testclient import TestClient

from ghostline.main import create_app
from tests.conftest import OTHER_KEY, OWNER_EMAIL, OWNER_SUB, SESSION_SECRET, SIGNER, identity_token, settings, sign_in


def test_the_owner_signs_in_and_gets_a_session(client):
    r = client.post("/auth/apple", json={"identity_token": identity_token(), "platform": "ios"})
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == OWNER_SUB and body["session_token"] and body["expires_at"]
    a = client.get("/account", headers={"Authorization": f"Bearer {body['session_token']}"})
    assert a.status_code == 200 and a.json()["sub"] == OWNER_SUB and a.json()["email_masked"] == "qa…@example.com"


def test_another_account_is_refused(client):
    stranger = identity_token(sub="001.stranger", email="qa+other@example.com")
    r = client.post("/auth/apple", json={"identity_token": stranger, "platform": "macos"})
    assert r.status_code == 401
    assert client.get("/account").status_code == 401


def test_the_owner_may_be_named_by_apple_user_id(fresh_db):
    app = create_app(settings(GHOSTLINE_OWNER_APPLE_ID=OWNER_SUB))
    with TestClient(app) as c:
        assert sign_in(c, email=None)          # a hidden email still signs in by sub
        other = identity_token(sub="001.other", email=OWNER_EMAIL)
        r = c.post("/auth/apple", json={"identity_token": other, "platform": "ios"})
        assert r.status_code == 401            # the owner's email on another Apple id is not the owner


def test_an_empty_owner_id_admits_nobody(fresh_db):
    app = create_app(settings(GHOSTLINE_OWNER_APPLE_ID=""))
    with TestClient(app) as c:
        r = c.post("/auth/apple", json={"identity_token": identity_token(), "platform": "ios"})
        assert r.status_code == 401


def test_an_expired_or_foreign_token_is_refused(client):
    r = client.post("/auth/apple", json={"identity_token": identity_token(exp_in=-10), "platform": "ios"})
    assert r.status_code == 401
    r = client.post("/auth/apple", json={"identity_token": identity_token(key=OTHER_KEY), "platform": "ios"})
    assert r.status_code == 401
    r = client.post("/auth/apple", json={"identity_token": identity_token(audience="com.other.app"), "platform": "ios"})
    assert r.status_code == 401


def test_the_test_signer_is_off_without_its_key(fresh_db):
    app = create_app(settings(GHOSTLINE_TEST_SIGNER_KEY=""))
    with TestClient(app) as c:
        r = c.post("/auth/apple", json={"identity_token": identity_token(), "platform": "ios"})
        assert r.status_code == 401


def test_a_session_can_be_signed_out_and_is_then_refused(client):
    token = sign_in(client)
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/sign-out", headers=h).status_code == 200
    assert client.get("/account", headers=h).status_code == 401


def test_a_forged_or_expired_session_is_refused(client):
    sign_in(client)
    forged = jwt.encode({"sub": OWNER_SUB, "jti": "00000000-0000-0000-0000-000000000000", "iss": "ghostline",
                         "exp": int(time.time()) + 100}, "wrong-secret-0123456789abcdef0123456789", algorithm="HS256")
    assert client.get("/account", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = jwt.encode({"sub": OWNER_SUB, "jti": "00000000-0000-0000-0000-000000000000", "iss": "ghostline",
                          "exp": int(time.time()) - 100}, SESSION_SECRET, algorithm="HS256")
    assert client.get("/account", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    unknown_jti = jwt.encode({"sub": OWNER_SUB, "jti": "00000000-0000-0000-0000-000000000000", "iss": "ghostline",
                              "exp": int(time.time()) + 100}, SESSION_SECRET, algorithm="HS256")
    assert client.get("/account", headers={"Authorization": f"Bearer {unknown_jti}"}).status_code == 401


def test_the_worker_token_is_not_the_owners_session(client):
    r = client.get("/account", headers={"Authorization": "Bearer worker-token-for-tests",
                                        "X-Worker-Token": "worker-token-for-tests"})
    assert r.status_code == 401


def test_devices_register_list_and_remove(client):
    token = sign_in(client)
    h = {"Authorization": f"Bearer {token}"}
    sandbox = {"token": "a" * 64, "platform": "ios", "environment": "sandbox"}
    assert client.post("/devices", json=sandbox, headers=h).status_code == 200
    assert client.post("/devices", json={**sandbox, "environment": "production"}, headers=h).status_code == 200
    assert client.post("/devices", json={"token": "b" * 64, "platform": "macos"}, headers=h).status_code == 200
    devices = client.get("/devices", headers=h).json()["devices"]
    assert [(d["platform"], d["environment"]) for d in devices] == [("ios", "production"), ("macos", "production")]
    assert devices[0]["token_tail"] == "aaaaaa" and "token" not in devices[0]
    assert client.delete(f"/devices/{'a' * 64}", headers=h).json()["removed"] == 1
    assert len(client.get("/devices", headers=h).json()["devices"]) == 1
    assert client.post("/devices", json={"token": "c" * 64, "platform": "ios"}).status_code == 401
    assert client.post("/devices", json={"token": "short", "platform": "ios"}, headers=h).status_code == 422


def test_sign_in_needs_a_real_token_shape(client):
    assert client.post("/auth/apple", json={"identity_token": "x", "platform": "ios"}).status_code == 422
    assert client.post("/auth/apple", json={"identity_token": "x" * 30, "platform": "tv"}).status_code == 422
    assert client.post("/auth/apple", json={"identity_token": "x" * 30, "platform": "ios"}).status_code == 401
    assert SIGNER  # the signer key is a fixture constant, never read from the repository's environment
