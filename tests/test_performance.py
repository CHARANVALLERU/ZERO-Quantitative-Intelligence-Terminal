#!/usr/bin/env python3
"""
Performance Test Suite
"""

import pytest
import time
import statistics
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


class TestAPIPerformance:
    """Test API performance."""
    
    def test_prediction_endpoint_latency(self):
        """Test prediction endpoint latency is under 500ms."""
        start = time.time()
        response = client.get("/api/v1/predictions/NIFTY 50")
        latency = (time.time() - start) * 1000
        
        assert response.status_code == 200
        assert latency < 500, f"Latency {latency:.2f}ms exceeds 500ms"
    
    def test_live_prices_latency(self):
        """Test live prices endpoint latency is under 200ms."""
        start = time.time()
        response = client.get("/api/v1/live-prices")
        latency = (time.time() - start) * 1000
        
        assert response.status_code == 200
        assert latency < 200, f"Latency {latency:.2f}ms exceeds 200ms"
    
    def test_news_endpoint_latency(self):
        """Test news endpoint latency is under 300ms."""
        start = time.time()
        response = client.get("/api/v1/news")
        latency = (time.time() - start) * 1000
        
        assert response.status_code == 200
        assert latency < 300, f"Latency {latency:.2f}ms exceeds 300ms"
    
    def test_concurrent_requests(self):
        """Test handling concurrent requests."""
        def make_request(i):
            response = client.get("/api/v1/predictions/NIFTY 50")
            return response.status_code
        
        # Make 10 concurrent requests
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(make_request, range(10)))
        
        assert all(r == 200 for r in results)


class TestCachePerformance:
    """Test cache performance."""
    
    def test_cache_hit_performance(self):
        """Test cache hit is fast."""
        from backend.cache import cache
        
        # Set a value
        cache.set("perf_test", {"data": "test"})
        
        # Measure cache hit
        start = time.time()
        for _ in range(1000):
            cache.get("perf_test")
        latency = (time.time() - start) / 1000 * 1000  # per request
        
        assert latency < 1, f"Cache hit latency {latency:.2f}µs exceeds 1µs"
    
    def test_cache_miss_performance(self):
        """Test cache miss is fast."""
        from backend.cache import cache
        
        # Measure cache miss
        start = time.time()
        for _ in range(1000):
            cache.get("non_existent_key")
        latency = (time.time() - start) / 1000 * 1000  # per request
        
        assert latency < 1, f"Cache miss latency {latency:.2f}µs exceeds 1µs"


class TestWebSocketPerformance:
    """Test WebSocket performance."""
    
    def test_websocket_connection_time(self):
        """Test WebSocket connection establishment time."""
        start = time.time()
        with client.websocket_connect("/ws/market-data") as ws:
            connection_time = (time.time() - start) * 1000
            ws.send_text("ping")
            response = ws.receive_text()
        
        assert connection_time < 100, f"Connection time {connection_time:.2f}ms exceeds 100ms"
        assert "pong" in response or "subscription" in response


class TestSystemMetrics:
    """Test system metrics."""
    
    def test_health_endpoint_metrics(self):
        """Test health endpoint returns metrics."""
        response = client.get("/health")
        data = response.json()
        
        assert "status" in data
        assert data["status"] == "healthy"
        assert "services" in data
        assert data["services"]["database"] == "connected"
    
    def test_status_endpoint_metrics(self):
        """Test status endpoint returns metrics."""
        response = client.get("/status")
        data = response.json()
        
        assert "metrics" in data
        assert "redis" in data
        assert isinstance(data["active_tasks"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
