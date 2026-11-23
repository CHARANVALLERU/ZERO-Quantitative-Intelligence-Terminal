"""
ZERO Terminal Backend API - Authentication Module
=================================================

Authentication *library* for the ZERO Terminal: password hashing, JWT issue
and verification, revocation, role checks and API-key checks.

This module deliberately contains **no FastAPI routes**. It used to decorate
handlers with ``@app.post(...)`` while never importing ``app``, so the module
raised ``NameError`` on import and ``backend.main``'s login handler blew up the
first time anyone tried to sign in. Routes live in ``backend.main``.

Security rules enforced here (2026-11-22):

* the signing key comes from ``ZERO_SECRET_KEY``; the old hard-coded
  ``"your-secret-key-here"`` is gone. If no key is configured in a non-dev
  environment the module refuses to sign tokens rather than silently using a
  public constant;
* an *access* token can never be minted from a *refresh* token (the old
  ``/auth/refresh`` endpoint signed whatever string it was given);
* every token carries a ``jti`` that can be revoked on logout;
* API keys come from the environment, not a source-code dictionary.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger("ZERO_Auth")

# ── Configuration ───────────────────────────────────────────────────────────

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ZERO_ACCESS_TOKEN_MINUTES", str(60 * 8)))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("ZERO_REFRESH_TOKEN_DAYS", "7"))

_DEV_ENV_VALUES = {"dev", "development", "local", "test", "testing"}
_INSECURE_KEYS = {
    "", "your-secret-key-here", "change-in-production", "changeme", "secret",
    "test", "development",
}

_ENV = (os.getenv("ZERO_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower()
IS_DEV = _ENV in _DEV_ENV_VALUES


def _load_secret_key() -> str:
    key = (os.getenv("ZERO_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
    if key.lower() in _INSECURE_KEYS or len(key) < 32:
        if IS_DEV:
            # Ephemeral per-process key: tokens stop working on restart, which
            # is the correct behaviour for an unset key in development.
            key = secrets.token_urlsafe(48)
            logger.warning(
                "ZERO_SECRET_KEY unset or too short - using an ephemeral development key. "
                "Tokens will not survive a restart. Set ZERO_SECRET_KEY (>=32 chars)."
            )
        else:
            raise RuntimeError(
                "ZERO_SECRET_KEY must be set to a strong value (>=32 chars) when "
                f"ZERO_ENV={_ENV!r}. Refusing to sign tokens with a default key."
            )
    return key


SECRET_KEY = _load_secret_key()

# Revoked token ids (jti) with their expiry, so the set can be pruned.
_revoked: Dict[str, float] = {}
# Refresh-token reuse tracking: username -> jti of the refresh token in force.
_refresh_in_force: Dict[str, str] = {}

_DEV_ADMIN = (os.getenv("ZERO_DEV_ADMIN_USERNAME") or "").strip()


# ── Models ──────────────────────────────────────────────────────────────────

class User(BaseModel):
    """Authenticated principal."""

    id: str
    email: str
    username: str
    is_active: bool = True
    is_verified: bool = True
    role: str = "viewer"          # viewer | trader | admin
    created_at: datetime = datetime.now(timezone.utc)


class UserCreate(BaseModel):
    email: str
    username: str
    password: str
    role: str = "viewer"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, Any]


class TokenData(BaseModel):
    username: Optional[str] = None
    scope: Optional[str] = None
    role: Optional[str] = None
    jti: Optional[str] = None


# ── Passwords ───────────────────────────────────────────────────────────────

# passlib 1.7.4 is broken against bcrypt >= 4.1 (it reads ``bcrypt.__about__``,
# which was removed) and raises RuntimeError/ValueError on every call. We talk to
# bcrypt directly instead: same ``$2b$`` hashes, no version coupling, and we
# truncate to bcrypt's 72-byte limit explicitly rather than letting the backend
# raise mid-login.
try:  # pragma: no cover - exercised via the API tests
    import bcrypt as _bcrypt
except Exception:  # noqa: BLE001
    _bcrypt = None

_BCRYPT_ROUNDS = 12
_DUMMY_HASH: Optional[str] = None


def _pw_bytes(password: str) -> bytes:
    return str(password).encode("utf-8")[:72]


def hash_password(password: str) -> str:
    if _bcrypt is None:
        raise RuntimeError("bcrypt is not installed; cannot hash passwords")
    return _bcrypt.hashpw(_pw_bytes(password), _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if _bcrypt is None or not hashed_password:
        return False
    try:
        return bool(_bcrypt.checkpw(_pw_bytes(plain_password), str(hashed_password).encode()))
    except Exception:
        return False


def _dummy_hash() -> str:
    """A constant-cost hash used when the username does not exist."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    return _DUMMY_HASH


# ── Tokens ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    scope: str = "access",
    token_type: str = "access",
) -> str:
    """Issue a signed JWT. ``token_type`` must match what the caller will do."""
    to_encode = dict(data)
    expire = _now() + (expires_delta or timedelta(minutes=15))
    jti = uuid.uuid4().hex
    to_encode.update({
        "exp": expire,
        "iat": _now(),
        "nbf": _now(),
        "scope": scope,
        "type": token_type,
        "jti": jti,
        "iss": "zero-terminal",
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(username: str, role: str = "viewer") -> str:
    token = create_token(
        {"sub": username, "role": role},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        scope="refresh",
        token_type="refresh",
    )
    try:
        _refresh_in_force[username] = jwt.get_unverified_claims(token).get("jti", "")
    except JWTError:
        pass
    return token


def _prune_revoked() -> None:
    now = time.time()
    for jti, exp in list(_revoked.items()):
        if exp < now:
            _revoked.pop(jti, None)


def revoke_token(token: str) -> bool:
    """Add a token's jti to the revocation list (logout)."""
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return False
    jti = claims.get("jti")
    if not jti:
        return False
    _prune_revoked()
    _revoked[str(jti)] = float(claims.get("exp") or (time.time() + 3600))
    if claims.get("type") == "refresh":
        sub = str(claims.get("sub") or "")
        if sub and _refresh_in_force.get(sub) == jti:
            _refresh_in_force.pop(sub, None)
    return True


def decode_token(token: str, expected_type: str = "access") -> Dict[str, Any]:
    """
    Decode and validate a token.

    Raises ``HTTPException(401)`` on any problem, including a token whose
    ``type`` does not match what the caller needs (a refresh token presented as
    a bearer credential, or vice versa).
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                            options={"verify_aud": False})
    except JWTError as exc:
        logger.info("token rejected: %s", exc)
        raise cred_exc

    if str(claims.get("type") or "access") != expected_type:
        logger.warning("token type mismatch: got %r wanted %r",
                       claims.get("type"), expected_type)
        raise cred_exc
    jti = claims.get("jti")
    if jti and str(jti) in _revoked:
        raise cred_exc
    if not claims.get("sub"):
        raise cred_exc
    return claims


def get_user_from_token(token: str, expected_type: str = "access") -> Optional[User]:
    """Claims -> User, or None. Never raises (kept for backward compatibility)."""
    try:
        claims = decode_token(token, expected_type=expected_type)
    except HTTPException:
        return None
    sub = str(claims.get("sub"))
    role = str(claims.get("role") or "viewer")
    return User(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"zero:{sub}")),
        email=sub,
        username=sub,
        is_active=True,
        is_verified=True,
        role=role,
        created_at=datetime.now(timezone.utc),
    )


# ── Dependencies ────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> User:
    """FastAPI dependency: bearer access token -> User, else 401."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_token(credentials.credentials, expected_type="access")
    sub = str(claims.get("sub"))
    return User(
        id=str(claims.get("uid") or uuid.uuid5(uuid.NAMESPACE_URL, f"zero:{sub}")),
        email=str(claims.get("email") or sub),
        username=sub,
        is_active=True,
        is_verified=True,
        role=str(claims.get("role") or "viewer"),
        created_at=datetime.now(timezone.utc),
    )


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="User is not active")
    return current_user


def require_role(*roles: str):
    """
    Dependency factory: ``Depends(require_role("trader", "admin"))``.

    ``admin`` always passes. Unknown roles fail closed.
    """
    allowed = {r.lower() for r in roles if r}

    async def _dep(user: User = Depends(get_current_user)) -> User:
        role = (user.role or "").lower()
        if role == "admin" or role in allowed:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires role: {', '.join(sorted(allowed)) or 'admin'}",
        )

    return _dep


async def verify_scope(token: str, required_scope: str) -> None:
    """Verify a raw token carries ``required_scope`` (used by tooling)."""
    claims = decode_token(token, expected_type="access")
    scope = str(claims.get("scope") or "")
    if required_scope not in scope.split():
        raise HTTPException(status_code=403, detail=f"Requires scope '{required_scope}'")


# ── API keys ────────────────────────────────────────────────────────────────

def _configured_api_keys() -> Set[str]:
    raw = os.getenv("ZERO_API_KEYS") or os.getenv("ZERO_API_KEY") or ""
    return {k.strip() for k in raw.replace(";", ",").split(",") if k.strip()}


def verify_api_key(api_key: Optional[str]) -> str:
    """
    Constant-time API-key check against ``ZERO_API_KEYS``.

    Returns the matched key's fingerprint (never the key itself) for audit
    logs. No keys configured means API-key auth is disabled, not open.
    """
    configured = _configured_api_keys()
    if not configured or not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    for candidate in configured:
        if secrets.compare_digest(api_key, candidate):
            return hashlib.sha256(candidate.encode()).hexdigest()[:12]
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
    )


def authenticate_user_record(user: Any, password: str) -> bool:
    """Check a password against a DB user row without leaking which part failed."""
    if user is None:
        # Burn comparable time so a missing user is not distinguishable.
        verify_password(password, _dummy_hash())
        return False
    hashed = getattr(user, "hashed_password", None)
    if not hashed:
        return False
    return verify_password(password, hashed)


def token_claims_for(user: Any) -> Dict[str, Any]:
    """Claims written into an access token for a DB user row."""
    username = str(getattr(user, "username", "") or "")
    return {
        "sub": username,
        "uid": str(getattr(user, "id", "") or ""),
        "email": str(getattr(user, "email", "") or username),
        "role": str(getattr(user, "role", "viewer") or "viewer"),
    }


def issue_tokens(user: Any) -> TokenResponse:
    """Access + refresh pair for a DB user row."""
    claims = token_claims_for(user)
    access = create_token(claims, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
                          scope="access", token_type="access")
    refresh = create_refresh_token(claims["sub"], claims.get("role", "viewer"))
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": claims.get("uid"),
            "username": claims["sub"],
            "email": claims.get("email"),
            "role": claims.get("role"),
        },
    )


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """
    Exchange a *refresh* token for a new access token.

    A raw username (what the old endpoint accepted) is rejected: refresh is a
    signed, typed, revocable credential or it does not happen.
    """
    claims = decode_token(refresh_token, expected_type="refresh")
    sub = str(claims.get("sub"))
    in_force = _refresh_in_force.get(sub)
    jti = str(claims.get("jti") or "")
    if in_force and jti and in_force != jti:
        raise HTTPException(status_code=401, detail="Refresh token superseded")
    access = create_token(
        {"sub": sub, "role": claims.get("role", "viewer")},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        scope="access",
        token_type="access",
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


__all__ = [
    "User", "UserCreate", "TokenResponse", "TokenData",
    "hash_password", "verify_password", "create_token", "create_refresh_token",
    "decode_token", "revoke_token", "get_user_from_token", "get_current_user",
    "get_current_active_user", "require_role", "verify_scope", "verify_api_key",
    "authenticate_user_record", "issue_tokens", "refresh_access_token",
    "token_claims_for", "ACCESS_TOKEN_EXPIRE_MINUTES", "IS_DEV",
]
