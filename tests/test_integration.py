#!/usr/bin/env python3
"""
Integration Test Suite
"""

import pytest
from backend.database import init_db, SessionLocal, User
from backend.cache import cache, CacheKeys
from datetime import datetime


@pytest.fixture(scope="session")
def db_session():
    """Create database session fixture."""
    init_db()
    session = SessionLocal()
    yield session
    session.close()


class TestDatabaseIntegration:
    """Test database integration."""
    
    def test_database_init(self, db_session):
        """Test that database initializes correctly."""
        from backend.database import Base
        tables = Base.metadata.tables.keys()
        assert "users" in tables
        assert "orders" in tables
        assert "strategies" in tables
    
    def test_user_creation(self, db_session):
        """Test user creation."""
        user = User(
            id="test-user-1",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed_password",
            role="trader"
        )
        db_session.add(user)
        db_session.commit()
        
        # Verify user was created
        retrieved = db_session.query(User).filter(User.username == "testuser").first()
        assert retrieved is not None
        assert retrieved.email == "test@example.com"


class TestCacheIntegration:
    """Test caching integration."""
    
    def test_cache_set_and_get(self):
        """Test cache set and get operations."""
        cache_key = "test_key"
        test_data = {"value": "test", "timestamp": datetime.now().isoformat()}
        
        cache.set(cache_key, test_data)
        retrieved = cache.get(cache_key)
        
        assert retrieved == test_data
    
    def test_cache_expiration(self):
        """Test cache expiration."""
        cache_key = "test_key_expiry"
        test_data = {"value": "test"}
        
        # Set with very short TTL
        cache.set(cache_key, test_data, ttl=1)
        
        # Should be available immediately
        assert cache.get(cache_key) == test_data
        
        # Should expire after 2 seconds
        import time
        time.sleep(2)
        assert cache.get(cache_key) is None


class TestCacheKeyGeneration:
    """Test cache key generation."""
    
    def test_predictions_key(self):
        """Test predictions cache key generation."""
        key = CacheKeys.predictions("NIFTY 50")
        assert "predictions:NIFTY 50" in key
    
    def test_live_prices_key(self):
        """Test live prices cache key generation."""
        key = CacheKeys.live_prices()
        assert key == "prices:live"
    
    def test_news_key(self):
        """Test news cache key generation."""
        key = CacheKeys.news(20)
        assert "news:limit=20" in key


class TestValidation:
    """Test data validation."""
    
    def test_validate_order_params(self):
        """Test order parameter validation."""
        from backend.validation import validate_order_params
        
        # Valid order
        result = validate_order_params("NIFTY", "BUY", 100, "MARKET")
        assert result["valid"] is True
        assert result["symbol"] == "NIFTY"
        
        # Invalid quantity
        result = validate_order_params("NIFTY", "BUY", 0, "MARKET")
        assert result["valid"] is False
        
        # Invalid side
        result = validate_order_params("NIFTY", "INVALID", 100, "MARKET")
        assert result["valid"] is False
    
    def test_validate_prediction_request(self):
        """Test prediction request validation."""
        from backend.validation import validate_prediction_request
        
        result = validate_prediction_request("NIFTY 50", 0.5)
        assert result["valid"] is True
        
        result = validate_prediction_request("INVALID", 0.5)
        assert result["valid"] is False


class TestConfig:
    """Test configuration."""
    
    def test_settings_loaded(self):
        """Test that settings load correctly."""
        from backend.config import settings
        
        assert settings.API_TITLE == "ZERO Terminal API"
        assert settings.API_VERSION == "1.0.0"
        assert settings.HOST == "0.0.0.0"
        assert settings.PORT == 8000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
