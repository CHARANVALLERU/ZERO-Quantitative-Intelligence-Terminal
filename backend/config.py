"""
Configuration management for ZERO Terminal
"""
import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    # Database
    DATABASE_URL: str = "sqlite:///./zero_terminal.db"
    
    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    
    # JWT
    SECRET_KEY: str = os.getenv("ZERO_SECRET_KEY", "change-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # API
    API_V1_STR: str = "/api/v1"
    API_TITLE: str = "ZERO Terminal API"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Hedge Fund Manager Terminal API"
    
    # CORS
    CORS_ORIGINS: str = "*"  # Set specific origins in production
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_PERIOD: int = 60  # seconds
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Market Data
    DEFAULT_MARKET_HOURS: str = "09:15-15:30"  # IST market hours
    
    # Prediction
    PREDICTION_CACHE_TTL: int = 300  # 5 minutes
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()

# Convenience functions
get_db_url = lambda: settings.DATABASE_URL
get_redis_url = lambda: f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
