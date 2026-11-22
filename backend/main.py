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
from typing import Optional, List, Dict, Any, Annotated
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
from backend.auth import (
    User as AuthUser,
    get_current_user,
    require_role,
    authenticate_user_record,
    issue_tokens,
    refresh_access_token,
    revoke_token,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
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

# Every /api/v1 route requires a bearer access token; mutations additionally
# require a trading role. Admin always passes require_role().
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
TraderUser = Annotated[AuthUser, Depends(require_role("trader", "admin"))]


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
# NOTE: `allow_origins=["*"]` together with `allow_credentials=True` is both
# unsafe and rejected by browsers (the wildcard is ignored for credentialed
# requests). Restrict origins through ZERO_CORS_ORIGINS; credentials are only
# enabled when an explicit allow-list is configured.
_cors_raw = (settings.CORS_ORIGINS or "").strip()
_cors_list = [o.strip() for o in _cors_raw.split(",") if o.strip() and o.strip() != "*"]
_cors_open = not _cors_list or "*" in _cors_raw
if _cors_open:
    logger.warning(
        "CORS accepts every origin. Set ZERO_CORS_ORIGINS (comma-separated) "
        "to restrict cross-origin access."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_open else _cors_list,
    allow_credentials=not _cors_open,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

_allowed_hosts = [h.strip() for h in (os.getenv("ZERO_ALLOWED_HOSTS") or "*").split(",") if h.strip()]
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_allowed_hosts or ["*"],
)

# Configure rate limiting
configure_rate_limiter(app)


# ==================== Health & Status ====================

def _cache_health() -> str:
    """Redis ping that never takes the health endpoint down."""
    try:
        return "connected" if get_redis_connection().ping() else "disconnected"
    except Exception:
        return "disconnected"


@app.get("/health")
async def health_check():
    """Health check endpoint (no auth: used by load balancers and uptime checks)."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.API_VERSION,
        "environment": settings.API_VERSION,
        "services": {
            "database": "connected",
            "cache": _cache_health(),
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
    user: CurrentUser,
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
async def get_all_predictions(user: CurrentUser, db = Depends(get_db)):
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
async def get_live_prices(user: CurrentUser, db = Depends(get_db)):
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
async def get_price(index: str, user: CurrentUser, db = Depends(get_db)):
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
    user: CurrentUser,
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
    user: TraderUser,
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
            user_id=user.id,  # never a client-supplied id
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
async def get_order(order_id: str, user: CurrentUser, db = Depends(get_db)):
    """Get order status (own orders only; admins see everything)."""
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if user.role != "admin" and order.user_id != user.id:
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
    user: CurrentUser,
    user_id: Optional[str] = None,
    db = Depends(get_db)
):
    """List orders. Scoped to the caller unless they are an admin."""
    try:
        query = db.query(Order)
        if user.role != "admin":
            query = query.filter(Order.user_id == user.id)
        elif user_id:
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
    user: TraderUser,
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
            user_id=user.id,
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
async def get_strategy(strategy_id: str, user: CurrentUser, db = Depends(get_db)):
    """Get strategy details (own strategies only; admins see everything)."""
    try:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        if user.role != "admin" and strategy.user_id != user.id:
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
    user: CurrentUser,
    user_id: Optional[str] = None,
    db = Depends(get_db)
):
    """List strategies. Scoped to the caller unless they are an admin."""
    try:
        query = db.query(Strategy)
        if user.role != "admin":
            query = query.filter(Strategy.user_id == user.id)
        elif user_id:
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
    request: Request,
    db = Depends(get_db)
):
    """
    Login endpoint.

    Credentials are checked against the users table with bcrypt. A successful
    login issues a typed access token (8h) plus a refresh token (7d); neither
    is derived from user input beyond the verified identity.
    """
    try:
        username = str(credentials.get("username", "") or "").strip()
        password = str(credentials.get("password", "") or "")

        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password required")

        user = db.query(User).filter(User.username == username).first()
        if not authenticate_user_record(user, password):
            logger.warning("failed login for %r from %s", username, request.client.host if request.client else "?")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account inactive")

        user.last_login = datetime.utcnow()
        db.commit()

        tokens = issue_tokens(user)
        return {"success": True, **tokens.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error logging in: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Authentication failed")


@app.post("/api/v1/auth/refresh")
async def refresh_token(payload: dict):
    """
    Refresh an access token.

    Requires a signed refresh token in the body (``{"refresh_token": "..."}``).
    The old version signed a fresh access token for whatever string it was
    handed, which let anyone mint credentials from a username.
    """
    token = ""
    if isinstance(payload, dict):
        token = str(payload.get("refresh_token") or payload.get("token") or "")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    return refresh_access_token(token)


@app.post("/api/v1/auth/logout")
async def logout(payload: Optional[dict] = None, user: CurrentUser = None):
    """Revoke the presented token (access and/or refresh)."""
    revoked = 0
    for key in ("access_token", "refresh_token", "token"):
        value = (payload or {}).get(key) if isinstance(payload, dict) else None
        if value and revoke_token(str(value)):
            revoked += 1
    return {"success": True, "revoked": revoked, "user": user.username if user else None}


@app.get("/api/v1/auth/me")
async def get_current_user(user: CurrentUser):
    """Return the authenticated principal (never a hard-coded admin)."""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
    }


# ==================== Portfolio & Trade Endpoints ====================

@app.post("/api/v1/portfolio")
async def create_portfolio(
    portfolio_data: dict,
    user: TraderUser
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
async def get_portfolio(portfolio_id: str, user: CurrentUser):
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
    trade_data: dict,
    user: TraderUser
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
    portfolio_id: str,
    user: CurrentUser
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
async def get_trade_blotter(user: CurrentUser):
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
