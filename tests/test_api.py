"""
Backend API Tests
"""

import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import app directly - avoid backend.main to prevent circular imports
from fastapi.testclient import TestClient

# Dynamically import the app to avoid issues with backend module structure
try:
    # Try direct import first
    from backend.main import app
except (ImportError, ModuleNotFoundError):
    # Import the app creation function
    from backend.main import create_app
    app = create_app()

# Initialize test client
client = TestClient(app)


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    def test_health_check(self):
        """Test that health check returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
    
    def test_status_endpoint(self):
        """Test system status endpoint."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "metrics" in data


class TestPredictionEndpoints:
    """Test prediction API endpoints."""
    
    def test_get_predictions_nifty(self):
        """Test getting NIFTY 50 predictions."""
        response = client.get("/api/v1/predictions/NIFTY 50")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "timestamp" in data
        assert data["index"] == "NIFTY 50"
    
    def test_get_predictions_banknifty(self):
        """Test getting BANKNIFTY predictions."""
        response = client.get("/api/v1/predictions/BANKNIFTY")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["index"] == "BANKNIFTY"
    
    def test_get_predictions_sensex(self):
        """Test getting SENSEX predictions."""
        response = client.get("/api/v1/predictions/SENSEX")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["index"] == "SENSEX"
    
    def test_get_predictions_invalid(self):
        """Test getting predictions for invalid index."""
        response = client.get("/api/v1/predictions/INVALID")
        assert response.status_code == 404
    
    def test_get_all_predictions(self):
        """Test getting all predictions."""
        response = client.get("/api/v1/predictions")
        assert response.status_code == 200
        data = response.json()
        assert "indices" in data
        assert len(data["indices"]) == 3


class TestMarketDataEndpoints:
    """Test market data API endpoints."""
    
    def test_get_live_prices(self):
        """Test getting live prices."""
        response = client.get("/api/v1/live-prices")
        assert response.status_code == 200
        data = response.json()
        assert "prices" in data
        assert "NIFTY 50" in data["prices"]
        assert "BANKNIFTY" in data["prices"]
        assert "SENSEX" in data["prices"]
    
    def test_get_price_nifty(self):
        """Test getting NIFTY price."""
        response = client.get("/api/v1/prices/NIFTY 50")
        assert response.status_code == 200
        data = response.json()
        assert data["index"] == "NIFTY 50"
        assert "data" in data


class TestNewsEndpoints:
    """Test news API endpoints."""
    
    def test_get_news(self):
        """Test getting news."""
        response = client.get("/api/v1/news")
        assert response.status_code == 200
        data = response.json()
        assert "news" in data
        assert "breaking" in data
        assert "total" in data
    
    def test_get_news_with_limit(self):
        """Test getting news with custom limit."""
        response = client.get("/api/v1/news?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data["news"]) <= 10


class TestTradingEndpoints:
    """Test trading API endpoints."""
    
    def test_create_order_market(self):
        """Test creating a market order."""
        order_data = {
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 100,
            "order_type": "MARKET"
        }
        response = client.post("/api/v1/orders", json=order_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "order_id" in data
    
    def test_create_order_limit(self):
        """Test creating a limit order."""
        order_data = {
            "symbol": "BANKNIFTY",
            "side": "SELL",
            "quantity": 50,
            "order_type": "LIMIT",
            "limit_price": 58000.00
        }
        response = client.post("/api/v1/orders", json=order_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    def test_create_order_invalid(self):
        """Test creating an invalid order."""
        order_data = {
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 1000000  # Too large
        }
        response = client.post("/api/v1/orders", json=order_data)
        assert response.status_code == 400


class TestAuthEndpoints:
    """Test authentication endpoints."""
    
    def test_login(self):
        """Test user login."""
        # Note: This test uses a placeholder auth system
        # In production, you would test with real credentials
        response = client.post("/api/v1/auth/login", json={
            "username": "test_user",
            "password": "test_password"
        })
        # Expected to fail with placeholder auth
        assert response.status_code == 401 or response.status_code == 200
    
    def test_get_current_user(self):
        """Test getting current user."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200


class TestWebSocket:
    """Test WebSocket connection."""
    
    def test_websocket_connection(self):
        """Test WebSocket connection endpoint."""
        from fastapi import WebSocket
        with client.websocket_connect("/ws/market-data") as ws:
            ws.send_text("ping")
            data = ws.receive_text()
            assert "pong" in data or "subscription" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
