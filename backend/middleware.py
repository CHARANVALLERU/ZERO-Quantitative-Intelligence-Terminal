"""
Rate limiting middleware for FastAPI
"""
from fastapi import Request, HTTPException
from fastapi.middleware import Middleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Rate limiter configuration
rate_limiter = Limiter(key_func=get_remote_address)

# Rate limit settings
RATE_LIMITS = {
    "all": "100/minute",
    "auth": "10/minute",
    "predictions": "60/minute",
    "news": "120/minute",
}


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    raise HTTPException(
        status_code=429,
        detail="Rate limit exceeded. Please try again later.",
        headers={"Retry-After": str(exc.retry_after)}
    )


def add_rate_limit_middleware(app):
    """Add rate limiting middleware to FastAPI app."""
    
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Middleware to apply rate limits."""
        # Get rate limit for this endpoint
        endpoint = request.url.path
        rate_limit = "100/minute"  # default
        
        # More specific rate limits
        if "/auth" in endpoint:
            rate_limit = "10/minute"
        elif "/predictions" in endpoint:
            rate_limit = "60/minute"
        elif "/news" in endpoint:
            rate_limit = "120/minute"
        
        # Use middleware to limit
        try:
            limiter = rate_limiter.limiter(endpoint, key_func=get_remote_address)
            response = await limiter(request, call_next, rate_limit)
            return response
        except Exception as e:
            logger.error(f"Rate limit middleware error: {e}")
            # Fall back to calling next
            response = await call_next(request)
            return response


def configure_rate_limiter(app):
    """Configure rate limiter for the app."""
    from backend.config import settings
    
    # Add exception handler
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
            headers={"Retry-After": str(exc.retry_after)}
        )
    
    # Add middleware
    add_rate_limit_middleware(app)
    
    # Middleware to add rate limit headers
    @app.middleware("http")
    async def add_rate_limit_headers(request: Request, call_next):
        """Add rate limit headers to responses."""
        response = await call_next(request)
        
        # Get rate limit info
        endpoint = request.url.path
        rate_limit = "100/minute"
        if "/auth" in endpoint:
            rate_limit = "10/minute"
        elif "/predictions" in endpoint:
            rate_limit = "60/minute"
        elif "/news" in endpoint:
            rate_limit = "120/minute"
        
        try:
            limiter = rate_limiter.limiter(endpoint, key_func=get_remote_address)
            limit_info = limiter.get_limit_info(request)
            response.headers["X-RateLimit-Limit"] = str(limit_info.limit)
            response.headers["X-RateLimit-Remaining"] = str(limit_info.remaining)
            response.headers["X-RateLimit-Reset"] = str(int(limit_info.reset_time.timestamp()))
        except Exception:
            pass
        
        return response
