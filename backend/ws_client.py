#!/usr/bin/env python3
"""
ZerO Terminal WebSocket Client for Backend Communication
=========================================================

Provides async WebSocket client functionality for connecting to
the ZERO backend API.
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

logger = logging.getLogger('ZERO_WS_Client')


@dataclass
class MarketData:
    """Container for market prediction data."""
    index: str
    data: Dict[str, Any]
    timestamp: datetime


@dataclass
class LivePrice:
    """Container for live price data."""
    index: str
    data: Dict[str, Any]
    timestamp: datetime


class ZEROWebSocketClient:
    """
    Async WebSocket client for ZERO backend API.
    
    Provides methods for:
    - Connecting to WebSocket endpoints
    - Subscribing to real-time data channels
    - Making REST API calls
    - Event-driven data handling
    """
    
    def __init__(self, url: str):
        """
        Initialize the WebSocket client.
        
        Args:
            url: Backend API URL (e.g., ws://localhost:8000/ws/market-data)
        """
        self.url = url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._callbacks: Dict[str, list] = {
            "market_data": [],
            "prices": [],
            "news": [],
            "predictions": [],
        }
        self._running = False
        
    async def connect(self) -> bool:
        """Establish connection to WebSocket server."""
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(
                self.url,
                heartbeat=25,
                ping_interval=20,
                ping_timeout=10
            )
            self._running = True
            logger.info(f"Connected to WebSocket server at {self.url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("Disconnected from WebSocket server")
    
    def on(self, channel: str, callback: Callable[[Any], None]) -> None:
        """
        Register callback for specific channel events.
        
        Args:
            channel: Event channel name (market_data, prices, news, predictions)
            callback: Function to call with event data
        """
        if channel in self._callbacks:
            self._callbacks[channel].append(callback)
            logger.debug(f"Registered callback for channel: {channel}")
        else:
            logger.warning(f"Unknown channel: {channel}")
    
    async def _handle_message(self, raw_message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(raw_message)
            channel = data.get("channel", "unknown")
            
            # Parse data based on channel
            parsed_data = self._parse_data(channel, data)
            
            # Trigger callbacks
            for callback in self._callbacks.get(channel, []):
                try:
                    callback(parsed_data)
                except Exception as e:
                    logger.error(f"Error in callback: {e}")
            
            # Also trigger generic callbacks
            for callback in self._callbacks.get("all", []):
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in generic callback: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    def _parse_data(self, channel: str, data: Dict) -> Any:
        """Parse data based on channel type."""
        if channel == "market_data":
            return MarketData(
                index=data.get("index", "unknown"),
                data=data.get("data", {}),
                timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat()))
            )
        elif channel == "prices":
            prices = data.get("prices", {})
            return {index: LivePrice(index=index, data=quote, timestamp=datetime.now())
                   for index, quote in prices.items()}
        elif channel == "news":
            return data.get("news", [])
        elif channel == "predictions":
            return data.get("data", {})
        return data
    
    async def run(self) -> None:
        """Main message processing loop."""
        while self._running and self._ws:
            try:
                msg = await self._ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed by server")
                    break
                    
            except Exception as e:
                logger.error(f"Error in message loop: {e}")
                await asyncio.sleep(1)  # Brief pause before retry
    
    async def subscribe_market_data(self) -> bool:
        """Send subscription request for market data."""
        if not self._ws:
            return False
        try:
            await self._ws.send_str(json.dumps({"action": "subscribe", "channel": "market_data"}))
            return True
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")
            return False
    
    async def subscribe_prices(self) -> bool:
        """Send subscription request for live prices."""
        if not self._ws:
            return False
        try:
            await self._ws.send_str(json.dumps({"action": "subscribe", "channel": "prices"}))
            return True
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")
            return False
    
    async def get_health(self) -> Optional[Dict]:
        """Get backend health status."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.url.rsplit('/', 1)[0]}/api/health") as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
        return None
    
    async def get_predictions(self, index: str = "NIFTY 50") -> Optional[Dict]:
        """Get predictions for a specific index via REST API."""
        try:
            base_url = self.url.rsplit("/ws/", 1)[0]
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}/api/predictions/{index}") as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 404:
                        logger.warning(f"Index {index} not found")
                    else:
                        logger.error(f"Failed to get predictions: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting predictions: {e}")
        return None
    
    async def get_live_prices(self) -> Optional[Dict]:
        """Get live prices via REST API."""
        try:
            base_url = self.url.rsplit("/ws/", 1)[0]
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}/api/live-prices") as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Failed to get live prices: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting live prices: {e}")
        return None


# Global client instance
_client: Optional[ZEROWebSocketClient] = None


async def get_client() -> Optional[ZEROWebSocketClient]:
    """Get or create global WebSocket client instance."""
    global _client
    if _client is None:
        _client = ZEROWebSocketClient("ws://localhost:8000/ws/market-data")
    return _client


async def connect_client() -> Optional[ZEROWebSocketClient]:
    """Connect to WebSocket server."""
    client = await get_client()
    if client and await client.connect():
        return client
    return None


async def disconnect_client() -> None:
    """Disconnect from WebSocket server."""
    global _client
    if _client:
        await _client.disconnect()
        _client = None
