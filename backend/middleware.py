"""
Rate limiting middleware for FastAPI.

The previous implementation wrapped ``rate_limiter.limiter(...)`` in a custom
``@app.middleware("http")`` and called the result as a coroutine. ``Limiter``
does not expose a callable ``limiter`` attribute (it has rate-limit strategies),
and the module also referenced an undefined ``logger``, so **every request that
hit the middleware raised** — ``/health`` returned 500 and the rate limiter had
never actually limited anything.

This module now wires slowapi the documented way (``app.state.limiter`` +
``SlowAPIMiddleware`` + an exception handler) and degrades to "no rate limiting"
with a loud warning when slowapi is not installed, instead of breaking the API.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request

logger = logging.getLogger("ZERO_RateLimit")

# Default limits per route family (slowapi syntax: "<n>/<period>").
RATE_LIMITS = {
    "all": "100/minute",
    "auth": "10/minute",
    "predictions": "60/minute",
    "news": "120/minute",
}

try:  # pragma: no cover - exercised only when slowapi is present
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address

    SLOWAPI_AVAILABLE = True
except Exception:  # noqa: BLE001
    Limiter = None  # type: ignore[assignment]
    RateLimitExceeded = Exception  # type: ignore[misc,assignment]
    SlowAPIMiddleware = None  # type: ignore[assignment]
    get_remote_address = None  # type: ignore[assignment]
    SLOWAPI_AVAILABLE = False


rate_limiter: Optional["Limiter"] = (
    Limiter(key_func=get_remote_address,
            default_limits=[RATE_LIMITS["all"]],
            headers_enabled=True)
    if SLOWAPI_AVAILABLE else None
)


def rate_limit_for_path(path: str) -> str:
    """Route-family limit for a request path."""
    if "/auth" in path:
        return RATE_LIMITS["auth"]
    if "/predictions" in path:
        return RATE_LIMITS["predictions"]
    if "/news" in path:
        return RATE_LIMITS["news"]
    return RATE_LIMITS["all"]


async def rate_limit_exceeded_handler(request: Request, exc):
    """429 with a Retry-After header."""
    retry_after = getattr(exc, "retry_after", None) or getattr(exc, "detail", "")
    raise HTTPException(
        status_code=429,
        detail="Rate limit exceeded. Please try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def configure_rate_limiter(app) -> bool:
    """
    Attach slowapi to ``app``. Returns whether limiting is active.

    A missing dependency is reported once at startup and never takes the API
    down: an unauthenticated-but-rate-limited API is better than one that
    cannot serve ``/health``.
    """
    if not SLOWAPI_AVAILABLE:
        logger.warning(
            "slowapi is not installed - request rate limiting is DISABLED. "
            "Install it with `pip install slowapi` before exposing the API."
        )
        return False

    app.state.limiter = rate_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    logger.info("Rate limiting active: %s", RATE_LIMITS)
    return True


def add_rate_limit_middleware(app) -> bool:
    """Backwards-compatible alias."""
    return configure_rate_limiter(app)
