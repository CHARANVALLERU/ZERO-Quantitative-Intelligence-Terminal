"""
ZERO Terminal Backend API - Authentication Module
===================================================

Provides authentication and session management for the ZERO Terminal.
Uses JWT-based authentication with FastAPI.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uuid
import logging
import hashlib

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, status, APIKeyHeader
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger('ZERO_Auth')

# Configuration
SECRET_KEY = "your-secret-key-here"  # Change in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Password hashing context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)

# Token storage (in-memory for now, use Redis in production)
token_store: Dict[str, Dict[str, Any]] = {}

# API Key validation
valid_api_keys = {
    "terminal-read": {"description": "Read-only terminal access"},
    "terminal-write": {"description": "Full terminal access"},
    "admin": {"description": "Administrative access"},
}


# Pydantic models
class User(BaseModel):
    """User model."""
    id: str
    email: str
    username: str
    is_active: bool
    is_verified: bool
    role: str  # 'viewer', 'trader', 'admin'
    created_at: datetime


class UserCreate(BaseModel):
    """User creation model."""
    email: str
    username: str
    password: str
    role: str = "viewer"


class TokenResponse(BaseModel):
    """Token response model."""
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: Dict[str, Any]


class TokenData(BaseModel):
    """Token data model."""
    username: Optional[str] = None
    scope: Optional[str] = None


# Helper functions
def hash_password(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None, scope: str = "access") -> str:
    """Create a JWT token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(minutes=15)
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(),
        "scope": scope,
        "type": "access",
    })
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(username: str) -> str:
    """Create a refresh token."""
    data = {"sub": username, "type": "refresh", "scope": "refresh"}
    return create_token(data, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS), scope="refresh")


def get_user_from_token(token: str) -> Optional[User]:
    """Get user from JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return User(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
            email=username,
            username=username,
            is_active=True,
            is_verified=True,
            role="trader",
            created_at=datetime.now(),
        )
    except JWTError:
        return None


# Dependency functions
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())
) -> User:
    """Get current user from token."""
    token = credentials.credentials
    user = get_user_from_token(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="User is not active")
    return current_user


async def verify_scope(required_scope: str):
    """Dependency to verify user scope."""
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=""
    )
    token = credentials.credentials
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    
    user_scope = payload.get("scope", "")
    if required_scope not in user_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operation requires '{required_scope}' scope"
        )


# Authentication routes
@app.post("/api/auth/login", response_model=TokenResponse)
async def login(form_data: "OAuth2PasswordRequestForm" = Depends()):
    """Login endpoint."""
    # Authenticate user (placeholder - replace with DB lookup)
    # user = await authenticate_user(form_data.username, form_data.password)
    
    if not form_data.username or not form_data.password:
        raise HTTPException(
            status_code=400,
            detail="Username and password are required"
        )
    
    # Create tokens
    access_token = create_token(
        {"sub": form_data.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        scope="access"
    )
    
    refresh_token = create_refresh_token(form_data.username)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "username": form_data.username,
            "role": "trader",
        }
    )


@app.post("/api/auth/refresh")
async def refresh_token(token: str):
    """Refresh token endpoint."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=400,
                detail="Invalid token type"
            )
        
        username = payload.get("sub")
        new_access_token = create_token(
            {"sub": username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
            scope="access"
        )
        
        return {
            "access_token": new_access_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
    except JWTError:
        raise HTTPException(
            status_code=400,
            detail="Invalid refresh token"
        )


@app.post("/api/auth/logout")
async def logout(token: str):
    """Logout endpoint - invalidates token."""
    # Add token to blacklist in production
    # token_store[token] = {"blacklisted": True}
    
    return {"message": "Successfully logged out"}


@app.get("/api/auth/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Get current user info."""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_active": current_user.is_active,
        "is_verified": current_user.is_verified,
        "role": current_user.role,
        "created_at": current_user.created_at.isoformat(),
    }


@app.post("/api/auth/verify")
async def verify_token(token: str):
    """Verify a token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "valid": True,
            "payload": payload,
        }
    except JWTError as e:
        return {
            "valid": False,
            "error": str(e),
        }


# API Key authentication
def get_api_key(
    api_key: str = Depends(APIKeyHeader(name="X-API-Key", auto_error=False))
):
    """Get API key from request."""
    return api_key


async def verify_api_key(
    api_key: Optional[str] = Depends(get_api_key)
):
    """Verify API key."""
    if not api_key or api_key not in valid_api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key


# Protected routes with API key
@app.get("/api/protected")
async def protected_route(
    api_key: str = Depends(verify_api_key)
):
    """Example protected route with API key."""
    return {
        "message": "Access granted to protected route",
        "api_key_scope": valid_api_keys.get(api_key, {}).get("description"),
    }
