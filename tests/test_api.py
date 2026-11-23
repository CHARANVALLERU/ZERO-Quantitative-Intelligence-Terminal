"""
Backend API tests — authenticated surface.

The previous version of this file targeted an unauthenticated API and could not
even be collected (it referenced ``create_app``, which never existed). This suite
tests the contract the terminal actually ships:

* public: ``/health`` (no auth, never 500s when Redis is down) and ``/status``
* protected: everything under ``/api/v1`` answers 401 without a bearer token
* the token lifecycle: access ≠ refresh, logout revokes, refresh rotates
* role gating on trader-only mutations

Tokens are minted with ``backend.auth`` rather than by logging in, so the suite
runs without a seeded users table; the login endpoint's rejection path is covered
separately.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ZERO_SECRET_KEY", "unit-test-secret-key-0123456789abcdef")

fastapi = pytest.importorskip("fastapi.testclient")
TestClient = fastapi.TestClient

from backend import auth as auth_mod  # noqa: E402
from backend import database as backend_db  # noqa: E402
from backend.main import app  # noqa: E402

# TestClient without a context manager does not run the lifespan hook, so create
# the schema explicitly. A missing users table used to surface as a 500 on login.
try:
    backend_db.init_db()
except Exception:  # pragma: no cover - sqlite may be locked elsewhere
    pass

client = TestClient(app)

PROTECTED_GET = [
    "/api/v1/predictions",
    "/api/v1/live-prices",
    "/api/v1/prices/NIFTY",
    "/api/v1/news",
    "/api/v1/orders",
    "/api/v1/strategies",
    "/api/v1/blotter",
    "/api/v1/auth/me",
    "/api/v1/compliance/check",
]


def _headers(role: str = "trader", user: str = "tester") -> dict:
    token = auth_mod.create_token({"sub": user, "uid": "u-" + user, "role": role})
    return {"Authorization": f"Bearer {token}"}


# ── public ─────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_is_public_and_healthy(self):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["services"]["database"] == "connected"
        # Redis may legitimately be absent on a dev box: reported, not raised.
        assert body["services"]["cache"] in ("connected", "disconnected")

    def test_status_endpoint(self):
        r = client.get("/status")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


# ── authentication ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_routes_reject_anonymous(path):
    r = client.get(path)
    assert r.status_code == 401, f"{path} leaked without a token ({r.status_code})"


def test_bearer_token_reaches_the_route():
    r = client.get("/api/v1/auth/me", headers=_headers("viewer", "alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["role"] == "viewer"


def test_refresh_token_is_not_an_access_token():
    refresh = auth_mod.create_refresh_token("alice")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


def test_raw_string_is_not_a_refresh_token():
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "alice"})
    assert r.status_code == 401


def test_access_token_cannot_be_used_to_refresh():
    access = auth_mod.create_token({"sub": "alice", "uid": "u1", "role": "viewer"})
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


def test_refresh_endpoint_rotates_tokens():
    refresh = auth_mod.create_refresh_token("alice")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["access_token"] != refresh


def test_logout_revokes_the_access_token():
    token = auth_mod.create_token({"sub": "bob", "uid": "u2", "role": "trader"})
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=hdr).status_code == 200
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 401


def test_login_rejects_bad_credentials():
    r = client.post("/api/v1/auth/login",
                    json={"username": "nobody", "password": "wrong-password"})
    assert r.status_code in (400, 401)
    assert "access_token" not in r.text


def test_trader_mutation_requires_trader_role():
    payload = {"symbol": "NIFTY", "side": "BUY", "quantity": 1, "order_type": "MARKET"}
    viewer = client.post("/api/v1/orders", json=payload, headers=_headers("viewer", "v1"))
    assert viewer.status_code in (403, 422), viewer.text
    trader = client.post("/api/v1/orders", json=payload, headers=_headers("trader", "t1"))
    assert trader.status_code not in (401, 403), trader.text


# ── token mechanics (library level) ────────────────────────────────────────


def test_tokens_are_typed_and_revocable():
    token = auth_mod.create_token({"sub": "carol"})
    claims = auth_mod.decode_token(token)
    assert claims["type"] == "access"
    assert claims["sub"] == "carol"
    assert auth_mod.revoke_token(token) is True
    with pytest.raises(Exception):
        auth_mod.decode_token(token)


def test_secret_key_is_not_hardcoded():
    src = (open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "backend", "auth.py"), encoding="utf-8").read())
    # The string may only appear in the list of *rejected* weak keys.
    assert 'SECRET_KEY = "your-secret-key-here"' not in src
    assert "_load_secret_key" in src
