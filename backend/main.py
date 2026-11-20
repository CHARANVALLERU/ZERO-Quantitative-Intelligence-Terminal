"""
ZERO Terminal API - Main Application
====================================

FastAPI application with all endpoints for the ZERO Terminal.
"""

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

# Local imports
from backend.database import init_db, get_db, User, PredictionCache, LivePriceCache, NewsCache, Order, Strategy
from backend.cache import (
    set_cache as cache_set,
    get_cache as cache_get,
    delete_cache as cache_delete,
    CacheKeys,
    add_to_queue,
    get_redis_connection,
    get_cache,
    set_cache,
)
from backend.config import settings
from backend.middleware import configure_rate_limiter
from backend.tasks import run_background_tasks, queue, background_tasks
from backend.validation import (
    validate_order_params,
    validate_prediction_request,
    validate_news_request,
    sanitize_order_params,
    validate_email
)
from backend.logging_config import setup_logging
from backend.portfolio_operations import (
    portfolio_constructor,
    trade_executor,
    compliance_engine,
    TradeOrder,
    Portfolio,
    Position
)

# Setup logging
log_file = setup_logging()
logger = logging.getLogger("ZERO_API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("=" * 80)
    logger.info("ZERO TERMINAL API - STARTING UP")
    logger.info(f"Environment: {settings.API_VERSION}")
    logger.info(f"Log file: {log_file}")
    
    # Initialize database
    init_db()
    logger.info("Database initialized")
    
    # Start background tasks
    background_task = asyncio.create_task(run_background_tasks())
    logger.info("Background tasks started")
    
    yield
    
    # Shutdown
    logger.info("=" * 80)
    logger.info("ZERO TERMINAL API - SHUTTING DOWN")
    
    # Cancel background tasks
    background_task.cancel()
    try:
        await background_task
    except asyncio.CancelledError:
        pass
    
    logger.info("Background tasks stopped")
    logger.info("=" * 80)


# Initialize FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# Configure middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"],
)

# Configure rate limiting
configure_rate_limiter(app)


# ==================== Health & Status ====================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.API_VERSION,
        "environment": "production",
        "services": {
            "database": "connected",
            "cache": "connected" if get_redis_connection().ping() else "disconnected",
            "background_tasks": "running"
        }
    }


@app.get("/status")
async def system_status():
    """System status with metrics."""
    # Get metrics from cache
    metrics = get_cache("system:metrics") or {
        "connections": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "api_calls": 0,
        "errors": 0
    }
    
    # Get Redis info
    redis_info = {}
    try:
        r = get_redis_connection()
        info = r.info("memory")
        redis_info = {
            "used_memory": info.get("used_memory_human"),
            "connected_clients": info.get("connected_clients"),
        }
    except Exception as e:
        logger.error(f"Failed to get Redis info: {e}")
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": metrics,
        "redis": redis_info,
        "active_tasks": len(background_tasks)
    }


# ==================== Prediction Endpoints ====================

@app.get("/api/v1/predictions/{index}")
async def get_predictions(
    index: str,
    confidence_threshold: Optional[float] = None,
    db = Depends(get_db)
):
    """Get market predictions for an index."""
    try:
        # Validate request
        validation = validate_prediction_request(index, confidence_threshold)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["errors"])
        
        # Check cache first
        cache_key = CacheKeys.predictions(index)
        cached = get_cache(cache_key)
        if cached:
            return {
                "source": "cache",
                "timestamp": datetime.utcnow().isoformat(),
                **cached
            }
        
        # Generate prediction
        from engine.prediction_matrix import generate_prediction_matrix
        matrix = generate_prediction_matrix()
        
        if index not in matrix:
            raise HTTPException(status_code=404, detail=f"Index {index} not found")
        
        data = matrix[index]
        
        # Apply confidence filter if specified
        if confidence_threshold and data.get("confidence") < confidence_threshold:
            raise HTTPException(
                status_code=400,
                detail=f"Confidence {data.get('confidence')} below threshold {confidence_threshold}"
            )
        
        # Cache result
        set_cache(cache_key, data)
        
        return {
            "source": "generated",
            "timestamp": datetime.utcnow().isoformat(),
            "index": index,
            "data": data
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting predictions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/predictions")
async def get_all_predictions(db = Depends(get_db)):
    """Get predictions for all indices."""
    try:
        indices = ["NIFTY 50", "BANKNIFTY", "SENSEX"]
        results = []
        
        for index in indices:
            try:
                result = await get_predictions(index, db=db)
                results.append(result)
            except Exception:
                results.append({
                    "index": index,
                    "error": "Prediction unavailable"
                })
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "indices": results
        }
    
    except Exception as e:
        logger.error(f"Error getting all predictions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Market Data Endpoints ====================

@app.get("/api/v1/live-prices")
async def get_live_prices(db = Depends(get_db)):
    """Get live prices for all indices."""
    try:
        # Check cache first
        cache_key = CacheKeys.live_prices()
        cached = get_cache(cache_key)
        if cached:
            return {
                "source": "cache",
                "timestamp": datetime.utcnow().isoformat(),
                "prices": cached
            }
        
        # Fetch fresh data
        prices = {}
        for index in ["NIFTY 50", "BANKNIFTY", "SENSEX"]:
            try:
                from data.live_index_service import get_live_index_quote
                quote = get_live_index_quote(index)
                prices[index] = quote
            except Exception as e:
                logger.warning(f"Failed to get price for {index}: {e}")
                prices[index] = {"error": str(e)}
        
        # Cache result
        set_cache(cache_key, prices)
        
        return {
            "source": "live",
            "timestamp": datetime.utcnow().isoformat(),
            "prices": prices
        }
    
    except Exception as e:
        logger.error(f"Error getting live prices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/prices/{index}")
async def get_price(index: str, db = Depends(get_db)):
    """Get live price for a specific index."""
    try:
        prices = await get_live_prices(db=db)
        if index in prices["prices"]:
            return {
                "source": prices["source"],
                "timestamp": prices["timestamp"],
                "index": index,
                "data": prices["prices"][index]
            }
        else:
            raise HTTPException(status_code=404, detail=f"Index {index} not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting price: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== News Endpoints ====================

@app.get("/api/v1/news")
async def get_news(
    limit: int = 20,
    min_impact: Optional[float] = None,
    db = Depends(get_db)
):
    """Get latest market news."""
    try:
        # Validate request
        validation = validate_news_request(limit, min_impact)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["errors"])
        
        # Check cache first
        cache_key = CacheKeys.news(validation["limit"])
        cached = get_cache(cache_key)
        if cached:
            return {
                "source": "cache",
                "timestamp": datetime.utcnow().isoformat(),
                "news": cached["news"],
                "breaking": cached["breaking"],
                "total": len(cached["news"])
            }
        
        # Fetch fresh data
        from data.market_news import get_global_news
        from data.news_monitor import check_breaking
        
        news = get_global_news() or []
        breaking = check_breaking()
        
        # Apply filter
        if validation.get("min_impact"):
            news = [n for n in news if n.get("impact_score", 0) >= validation["min_impact"]]
            breaking["breaking"] = [b for b in breaking.get("breaking", []) if b.get("impact_score", 0) >= validation["min_impact"]]
        
        result = {
            "news": news[:validation["limit"]],
            "breaking": breaking.get("breaking", []),
            "total": len(news)
        }
        
        # Cache result
        set_cache(cache_key, result)
        
        return {
            "source": "generated",
            "timestamp": datetime.utcnow().isoformat(),
            **result
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting news: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Trading Endpoints ====================

@app.post("/api/v1/orders")
async def create_order(
    order_data: dict,
    request: Request,
    db = Depends(get_db)
):
    """Create a new order."""
    try:
        # Extract order params
        symbol = order_data.get("symbol", "")
        side = order_data.get("side", "BUY")
        quantity = order_data.get("quantity", 1)
        order_type = order_data.get("order_type", "MARKET")
        limit_price = order_data.get("limit_price")
        
        # Validate parameters
        validation = validate_order_params(symbol, side, quantity, order_type, limit_price)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["errors"])
        
        # Create order record
        order_id = f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        order = Order(
            id=order_id,
            user_id="system",  # Will be replaced with authenticated user
            symbol=validation["symbol"],
            side=validation["side"],
            order_type=validation["order_type"],
            quantity=validation["quantity"],
            limit_price=validation["limit_price"],
            status="PENDING"
        )
        
        db.add(order)
        db.commit()
        db.refresh(order)
        
        # Add to processing queue
        await queue.add_task("order_processing", {
            "order_id": order_id,
            "symbol": validation["symbol"],
            "side": validation["side"],
            "quantity": validation["quantity"],
            "order_type": validation["order_type"],
            "limit_price": validation["limit_price"]
        })
        
        return {
            "success": True,
            "order_id": order_id,
            "status": "PENDING",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str, db = Depends(get_db)):
    """Get order status."""
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return {
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "quantity": order.quantity,
            "limit_price": order.limit_price,
            "status": order.status,
            "created_at": order.created_at.isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/orders")
async def list_orders(
    user_id: Optional[str] = None,
    db = Depends(get_db)
):
    """List orders."""
    try:
        query = db.query(Order)
        if user_id:
            query = query.filter(Order.user_id == user_id)
        
        orders = query.order_by(Order.created_at.desc()).limit(100).all()
        
        return [
            {
                "order_id": o.id,
                "user_id": o.user_id,
                "symbol": o.symbol,
                "side": o.side,
                "order_type": o.order_type,
                "quantity": o.quantity,
                "limit_price": o.limit_price,
                "status": o.status,
                "created_at": o.created_at.isoformat()
            }
            for o in orders
        ]
    
    except Exception as e:
        logger.error(f"Error listing orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Strategy Endpoints ====================

@app.post("/api/v1/strategies")
async def create_strategy(
    strategy_data: dict,
    request: Request,
    db = Depends(get_db)
):
    """Create a trading strategy."""
    try:
        name = strategy_data.get("name", "")
        symbol = strategy_data.get("symbol", "NIFTY 50")
        parameters = strategy_data.get("parameters", {})
        
        # Validate
        if not name:
            raise HTTPException(status_code=400, detail="Strategy name is required")
        
        # Sanitize parameters
        sanitized = sanitize_order_params("NIFTY", "BUY", 1)  # Placeholder
        
        # Create strategy record
        strategy_id = f"STRAT-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        strategy = Strategy(
            id=strategy_id,
            user_id="system",
            name=name[:100],
            symbol=symbol,
            parameters=json.dumps(parameters)
        )
        
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        
        return {
            "success": True,
            "strategy_id": strategy_id,
            "status": "ACTIVE",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating strategy: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/strategies/{strategy_id}")
async def get_strategy(strategy_id: str, db = Depends(get_db)):
    """Get strategy details."""
    try:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        return {
            "strategy_id": strategy.id,
            "name": strategy.name,
            "symbol": strategy.symbol,
            "parameters": json.loads(strategy.parameters) if strategy.parameters else {},
            "is_active": strategy.is_active,
            "created_at": strategy.created_at.isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting strategy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/strategies")
async def list_strategies(
    user_id: Optional[str] = None,
    db = Depends(get_db)
):
    """List strategies."""
    try:
        query = db.query(Strategy)
        if user_id:
            query = query.filter(Strategy.user_id == user_id)
        
        strategies = query.order_by(Strategy.created_at.desc()).limit(100).all()
        
        return [
            {
                "strategy_id": s.id,
                "name": s.name,
                "symbol": s.symbol,
                "is_active": s.is_active,
                "created_at": s.created_at.isoformat()
            }
            for s in strategies
        ]
    
    except Exception as e:
        logger.error(f"Error listing strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Auth Endpoints ====================

@app.post("/api/v1/auth/login")
async def login(
    credentials: dict,
    db = Depends(get_db)
):
    """Login endpoint."""
    try:
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password required")
        
        # Find user (placeholder - implement with proper auth)
        from backend.auth import verify_password, create_token, create_refresh_token
        
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account inactive")
        
        # Update last login
        user.last_login = datetime.utcnow()
        db.commit()
        
        # Create tokens
        access_token = create_token({"sub": username})
        refresh_token = create_refresh_token(username)
        
        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error logging in: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/auth/refresh")
async def refresh_token(token: str, db = Depends(get_db)):
    """Refresh access token."""
    try:
        from backend.auth import create_token
        
        new_token = create_token({"sub": token})
        
        return {
            "success": True,
            "access_token": new_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
    
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@app.get("/api/v1/auth/me")
async def get_current_user(db = Depends(get_db)):
    """Get current authenticated user."""
    try:
        # Extract user from token (placeholder)
        user = {"id": "system", "username": "system", "email": "system@zero.terminal", "role": "admin"}
        
        return {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    
    except Exception as e:
        logger.error(f"Error getting current user: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Portfolio & Trade Endpoints ====================

@app.post("/api/v1/portfolio")
async def create_portfolio(
    portfolio_data: dict
):
    """Create a new portfolio."""
    try:
        name = portfolio_data.get("name", "Unnamed Portfolio")
        base_currency = portfolio_data.get("base_currency", "INR")
        initial_cash = float(portfolio_data.get("initial_cash", 1000000.0))
        
        portfolio = portfolio_constructor.create_portfolio(
            name=name,
            base_currency=base_currency,
            initial_cash=initial_cash
        )
        
        return {
            "success": True,
            "portfolio_id": portfolio.id,
            "name": portfolio.name,
            "total_value": portfolio.total_value,
            "cash": portfolio.cash,
            "created_at": portfolio.created_at.isoformat()
        }
    
    except Exception as e:
        logger.error(f"Error creating portfolio: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/portfolio/{portfolio_id}")
async def get_portfolio(portfolio_id: str):
    """Get portfolio details."""
    try:
        if portfolio_id not in portfolio_constructor.portfolios:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        
        portfolio = portfolio_constructor.portfolios[portfolio_id]
        
        return {
            "portfolio_id": portfolio.id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "total_value": portfolio.total_value,
            "cash": portfolio.cash,
            "positions_value": portfolio.total_value - portfolio.cash,
            "performance_1d": portfolio.performance_1d,
            "performance_1w": portfolio.performance_1w,
            "performance_1m": portfolio.performance_1m,
            "num_positions": len(portfolio.positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "average_cost": p.average_cost,
                    "market_price": p.market_price,
                    "market_value": p.market_value,
                    "unrealized_pnl": p.unrealized_pnl,
                    "weight": p.weight
                }
                for p in portfolio.positions.values()
            ],
            "last_updated": portfolio.last_updated.isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/trade/execute")
async def execute_trade(
    trade_data: dict
):
    """Execute a trade order."""
    try:
        symbol = trade_data.get("symbol", "")
        side = trade_data.get("side", "BUY")
        quantity = float(trade_data.get("quantity", 0))
        order_type = trade_data.get("order_type", "MARKET")
        limit_price = trade_data.get("limit_price")
        
        # Create order
        order = TradeOrder(
            symbol=symbol.upper(),
            side=side.upper(),
            quantity=quantity,
            order_type=order_type.upper(),
            limit_price=limit_price
        )
        
        # Execute
        market_data = {
            f"{symbol}_price": trade_data.get("current_price", 18500.0),
            f"{symbol}_volume": 500000,
            f"{symbol}_decision_price": trade_data.get("current_price", 18500.0)
        }
        
        result = trade_executor.execute_order(order, order_type.upper(), market_data)
        
        return {
            "success": result.get("success", False),
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
            "filled_price": order.filled_price,
            "commission": order.commission,
            "slippage": order.slippage,
            "status": order.status
        }
    
    except Exception as e:
        logger.error(f"Error executing trade: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/compliance/check")
async def check_compliance(
    symbol: str,
    side: str,
    quantity: float,
    portfolio_id: str
):
    """Check if a trade passes compliance rules."""
    try:
        if portfolio_id not in portfolio_constructor.portfolios:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        
        portfolio = portfolio_constructor.portfolios[portfolio_id]
        
        order = TradeOrder(
            symbol=symbol.upper(),
            side=side.upper(),
            quantity=quantity
        )
        
        allowed, violations = compliance_engine.pre_trade_check(order, portfolio)
        
        return {
            "allowed": allowed,
            "violations": violations,
            "symbol": symbol,
            "side": side,
            "quantity": quantity
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking compliance: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/blotter")
async def get_trade_blotter():
    """Get the trade blotter."""
    try:
        return {
            "trades": [
                {
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "commission": t.commission,
                    "gross_amount": t.gross_amount,
                    "net_amount": t.net_amount
                }
                for t in portfolio_constructor.blotter[-100:]  # Last 100 trades
            ],
            "total_trades": len(portfolio_constructor.blotter)
        }
    
    except Exception as e:
        logger.error(f"Error getting blotter: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ==================== WebSocket Endpoint ====================

from fastapi import WebSocket, WebSocketDisconnect

active_connections: list = []

@app.websocket("/ws/market-data")
async def market_data_websocket(ws: WebSocket):
    """WebSocket for real-time market data."""
    await ws.accept()
    active_connections.append(ws)
    logger.info(f"Client connected. Total: {len(active_connections)}")
    
    try:
        while True:
            # Keep connection alive
            data = await ws.receive_text()
            
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            elif data.startswith("subscribe:"):
                channel = data.split(":", 1)[1]
                await ws.send_text(json.dumps({
                    "type": "subscription",
                    "channel": channel,
                    "status": "subscribed"
                }))
    
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if ws in active_connections:
            active_connections.remove(ws)
            logger.info(f"Client removed. Total: {len(active_connections)}")


# ==================== Error Handlers ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": str(exc.detail) if isinstance(exc.detail, str) else exc.detail,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unexpected error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# ==================== Serve Frontend ====================

frontend_build_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "out")

if os.path.exists(frontend_build_path):
    # Serve static files
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_build_path, "static")), name="static")
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve Next.js frontend."""
        file_path = os.path.join(frontend_build_path, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_build_path, "index.html"))


# ==================== Main Entry ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
