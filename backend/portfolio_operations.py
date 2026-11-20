"""
Portfolio Construction & Operations Module
=========================================

Tools for portfolio construction, trade execution, and operations management.
"""

from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import logging
import uuid

logger = logging.getLogger('ZERO_Operations')


@dataclass
class TradeOrder:
    """Represents a trade order."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: str = ""  # BUY, SELL
    quantity: float = 0.0
    order_type: str = ""  # MARKET, LIMIT, STOP, STOP_LIMIT
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "DAY"  # DAY, GTC, IOC, FOK
    status: str = "PENDING"  # PENDING, FILLED, PARTIAL, CANCELLED, REJECTED
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    strategy_id: Optional[str] = None
    notes: str = ""


@dataclass
class Position:
    """Represents a portfolio position."""
    symbol: str = ""
    quantity: float = 0.0
    average_cost: float = 0.0
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    weight: float = 0.0  # Percentage of portfolio
    sector: str = ""
    currency: str = "INR"


@dataclass
class Portfolio:
    """Represents a complete portfolio."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    base_currency: str = "INR"
    total_value: float = 0.0
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    benchmark: str = "NIFTY 50"
    performance_1d: float = 0.0
    performance_1w: float = 0.0
    performance_1m: float = 0.0
    performance_ytd: float = 0.0
    performance_1y: float = 0.0


@dataclass
class TradeBlotterEntry:
    """Entry in the trade blotter."""
    timestamp: datetime = field(default_factory=datetime.now)
    trade_id: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    commission: float = 0.0
    gross_amount: float = 0.0
    net_amount: float = 0.0
    strategy: str = ""
    trader: str = ""
    status: str = ""


class PortfolioConstructor:
    """
    Portfolio construction and management engine.
    """
    
    def __init__(self):
        self.portfolios: Dict[str, Portfolio] = {}
        self.blotter: List[TradeBlotterEntry] = []
    
    def create_portfolio(
        self, name: str, base_currency: str = "INR", 
        initial_cash: float = 0.0
    ) -> Portfolio:
        """Create a new portfolio."""
        portfolio = Portfolio(
            name=name,
            base_currency=base_currency,
            cash=initial_cash,
            total_value=initial_cash
        )
        self.portfolios[portfolio.id] = portfolio
        return portfolio
    
    def add_position(
        self, portfolio_id: str, symbol: str, 
        quantity: float, price: float
    ) -> bool:
        """Add or increase a position."""
        try:
            if portfolio_id not in self.portfolios:
                return False
            
            portfolio = self.portfolios[portfolio_id]
            
            # Calculate cost
            cost = quantity * price
            
            # Check if we have enough cash
            if portfolio.cash < cost:
                logger.warning(f"Insufficient cash for position: {symbol}")
                return False
            
            # Update or create position
            if symbol in portfolio.positions:
                pos = portfolio.positions[symbol]
                # Weighted average cost
                total_quantity = pos.quantity + quantity
                pos.average_cost = (
                    (pos.quantity * pos.average_cost + quantity * price) / total_quantity
                )
                pos.quantity = total_quantity
            else:
                portfolio.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=price
                )
            
            # Update cash and portfolio value
            portfolio.cash -= cost
            portfolio.last_updated = datetime.now()
            
            # Log to blotter
            self._add_to_blotter(
                symbol=symbol, side="BUY", quantity=quantity,
                price=price, commission=0.0
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to add position: {e}")
            return False
    
    def reduce_position(
        self, portfolio_id: str, symbol: str, 
        quantity: float, price: float
    ) -> bool:
        """Reduce or close a position."""
        try:
            if portfolio_id not in self.portfolios:
                return False
            
            portfolio = self.portfolios[portfolio_id]
            
            if symbol not in portfolio.positions:
                return False
            
            pos = portfolio.positions[symbol]
            
            if quantity > pos.quantity:
                logger.warning(f"Trying to sell more than owned: {symbol}")
                return False
            
            # Calculate proceeds
            proceeds = quantity * price
            
            # Calculate realized P&L
            realized_pnl = quantity * (price - pos.average_cost)
            pos.realized_pnl += realized_pnl
            
            # Update position
            pos.quantity -= quantity
            if pos.quantity <= 0:
                del portfolio.positions[symbol]
            
            # Update cash and portfolio value
            portfolio.cash += proceeds
            portfolio.last_updated = datetime.now()
            
            # Log to blotter
            self._add_to_blotter(
                symbol=symbol, side="SELL", quantity=quantity,
                price=price, commission=0.0
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to reduce position: {e}")
            return False
    
    def update_market_prices(
        self, portfolio_id: str, 
        price_updates: Dict[str, float]
    ) -> bool:
        """Update market prices for all positions."""
        try:
            if portfolio_id not in self.portfolios:
                return False
            
            portfolio = self.portfolios[portfolio_id]
            
            # Update positions
            total_value = portfolio.cash
            for symbol, position in portfolio.positions.items():
                if symbol in price_updates:
                    position.market_price = price_updates[symbol]
                    position.market_value = position.quantity * position.market_price
                    position.unrealized_pnl = position.quantity * (
                        position.market_price - position.average_cost
                    )
                    total_value += position.market_value
                else:
                    # Keep existing market value if no update
                    total_value += position.market_value
            
            portfolio.total_value = total_value
            portfolio.last_updated = datetime.now()
            
            # Update weights
            for position in portfolio.positions.values():
                if portfolio.total_value > 0:
                    position.weight = position.market_value / portfolio.total_value
                else:
                    position.weight = 0.0
            
            return True
        except Exception as e:
            logger.error(f"Failed to update market prices: {e}")
            return False
    
    def calculate_performance(
        self, portfolio_id: str, 
        benchmark_returns: Dict[str, float] = None
    ) -> Dict[str, float]:
        """Calculate portfolio performance metrics."""
        try:
            if portfolio_id not in self.portfolios:
                return {"error": "Portfolio not found"}
            
            portfolio = self.portfolios[portfolio_id]
            
            # Simple return calculation (would need historical data for real calculation)
            # This is a placeholder - in reality you'd track historical NAV
            performance = {
                "total_value": portfolio.total_value,
                "cash": portfolio.cash,
                "positions_value": portfolio.total_value - portfolio.cash,
                "return_1d": portfolio.performance_1d,
                "return_1w": portfolio.performance_1w,
                "return_1m": portfolio.performance_1m,
                "return_ytd": portfolio.performance_ytd,
                "return_1y": portfolio.performance_1y,
                "unrealized_pnl": sum(p.unrealized_pnl for p in portfolio.positions.values()),
                "realized_pnl": sum(p.realized_pnl for p in portfolio.positions.values()),
                "num_positions": len(portfolio.positions)
            }
            
            return performance
        except Exception as e:
            logger.error(f"Failed to calculate performance: {e}")
            return {"error": str(e)}
    
    def _add_to_blotter(
        self, symbol: str, side: str, quantity: float,
        price: float, commission: float = 0.0
    ):
        """Add entry to trade blotter."""
        gross_amount = quantity * price
        net_amount = gross_amount - commission if side == "SELL" else gross_amount + commission
        
        entry = TradeBlotterEntry(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            gross_amount=gross_amount,
            net_amount=net_amount
        )
        
        self.blotter.append(entry)
        
        # Keep blotter size manageable
        if len(self.blotter) > 10000:
            self.blotter = self.blotter[-5000:]  # Keep last 5000 entries


class TradeExecutor:
    """
    Trade execution engine with smart order routing.
    """
    
    def __init__(self):
        self.execution_algorithms = {
            "TWAP": self._execute_twap,
            "VWAP": self._execute_vwap,
            "POV": self._execute_pov,
            "IMPLEMENTATION_SHORTFALL": self._execute_is,
            "MARKET": self._execute_market,
            "LIMIT": self._execute_limit
        }
    
    def execute_order(
        self, order: TradeOrder, 
        algorithm: str = "MARKET",
        market_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Execute a trade order using specified algorithm.
        """
        try:
            if algorithm not in self.execution_algorithms:
                return {"error": f"Unknown algorithm: {algorithm}"}
            
            # Execute using selected algorithm
            result = self.execution_algorithms[algorithm](order, market_data or {})
            
            # Update order with execution details
            if "success" in result and result["success"]:
                order.status = "FILLED"
                order.filled_at = datetime.now()
                order.filled_price = result.get("avg_price", 0.0)
                order.filled_quantity = result.get("filled_quantity", 0.0)
                order.slippage = result.get("slippage", 0.0)
                order.commission = result.get("commission", 0.0)
            
            return result
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            return {"error": str(e), "success": False}
    
    def _execute_market(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute market order."""
        # Simple market order execution at current price
        symbol = order.symbol
        price = market_data.get(f"{symbol}_price", 0.0)
        
        if price <= 0:
            return {"error": "No valid market price available"}
        
        # Simple slippage model
        slippage = price * 0.0005  # 5 bps slippage
        if order.side == "BUY":
            execution_price = price + slippage
        else:
            execution_price = price - slippage
        
        return {
            "success": True,
            "filled_quantity": order.quantity,
            "avg_price": execution_price,
            "slippage": slippage * order.quantity,
            "commission": order.quantity * execution_price * 0.0003,  # 3 bps commission
            "market_impact": "low"
        }
    
    def _execute_limit(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute limit order."""
        symbol = order.symbol
        limit_price = order.limit_price or 0.0
        market_price = market_data.get(f"{symbol}_price", 0.0)
        
        if limit_price <= 0:
            return {"error": "Invalid limit price"}
        
        # Check if limit price is achievable
        if order.side == "BUY" and market_price <= limit_price:
            # Can buy at or below limit
            execution_price = min(market_price, limit_price)
            return {
                "success": True,
                "filled_quantity": order.quantity,
                "avg_price": execution_price,
                "slippage": 0.0,
                "commission": order.quantity * execution_price * 0.0003,
                "market_impact": "low"
            }
        elif order.side == "SELL" and market_price >= limit_price:
            # Can sell at or above limit
            execution_price = max(market_price, limit_price)
            return {
                "success": True,
                "filled_quantity": order.quantity,
                "avg_price": execution_price,
                "slippage": 0.0,
                "commission": order.quantity * execution_price * 0.0003,
                "market_impact": "low"
            }
        else:
            # Order not filled
            return {
                "success": False,
                "filled_quantity": 0.0,
                "reason": "Limit price not reached",
                "market_price": market_price,
                "limit_price": limit_price
            }
    
    def _execute_twap(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute TWAP (Time Weighted Average Price) algorithm."""
        # Simplified TWAP - split order into time slices
        slices = 10  # 10 time slices
        slice_size = order.quantity / slices
        
        # In reality, would execute slices over time
        # For now, simulate with average price
        symbol = order.symbol
        price = market_data.get(f"{symbol}_price", 0.0)
        
        if price <= 0:
            return {"error": "No valid market price"}
        
        # TWAP assumes we get the average price over the period
        execution_price = price  # Simplified
        slippage = price * 0.0003  # Lower slippage due to passive execution
        
        return {
            "success": True,
            "filled_quantity": order.quantity,
            "avg_price": execution_price,
            "slippage": slippage * order.quantity,
            "commission": order.quantity * execution_price * 0.0005,  # Higher commission for more trades
            "algorithm": "TWAP",
            "slices": slices
        }
    
    def _execute_vwap(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute VWAP (Volume Weighted Average Price) algorithm."""
        # Simplified VWAP
        symbol = order.symbol
        price = market_data.get(f"{symbol}_price", 0.0)
        volume = market_data.get(f"{symbol}_volume", 1000000)
        
        if price <= 0:
            return {"error": "No valid market price"}
        
        # VWAP aims to match volume-weighted average price
        execution_price = price  # Simplified
        slippage = price * 0.0002  # Even lower slippage
        
        return {
            "success": True,
            "filled_quantity": order.quantity,
            "avg_price": execution_price,
            "slippage": slippage * order.quantity,
            "commission": order.quantity * execution_price * 0.0004,
            "algorithm": "VWAP",
            "target_volume": volume
        }
    
    def _execute_pov(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute POV (Percentage of Volume) algorithm."""
        # Execute as percentage of market volume
        target_pov = 0.1  # 10% of volume
        symbol = order.symbol
        price = market_data.get(f"{symbol}_price", 0.0)
        volume = market_data.get(f"{symbol}_volume", 1000000)
        
        if price <= 0:
            return {"error": "No valid market price"}
        
        # Maximum order size based on volume
        max_order_value = volume * price * target_pov
        max_quantity = max_order_value / price if price > 0 else 0
        
        if order.quantity > max_quantity:
            return {
                "success": False,
                "filled_quantity": max_quantity,
                "reason": f"Order size exceeds POV limit ({target_pov*100}% of volume)",
                "max_allowed_quantity": max_quantity
            }
        
        execution_price = price
        slippage = price * 0.0001  # Very low slippage for passive execution
        
        return {
            "success": True,
            "filled_quantity": order.quantity,
            "avg_price": execution_price,
            "slippage": slippage * order.quantity,
            "commission": order.quantity * execution_price * 0.0003,
            "algorithm": "POV",
            "target_pov": target_pov
        }
    
    def _execute_is(
        self, order: TradeOrder, 
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Implementation Shortfall algorithm."""
        # Aim to minimize cost vs decision price
        symbol = order.symbol
        decision_price = market_data.get(f"{symbol}_decision_price", 0.0)
        current_price = market_data.get(f"{symbol}_price", 0.0)
        
        if decision_price <= 0 or current_price <= 0:
            return {"error": "Invalid prices for IS algorithm"}
        
        # Aggressive/passive based on urgency
        # For demo, use mid-price
        execution_price = (decision_price + current_price) / 2
        slippage = abs(execution_price - decision_price)
        
        return {
            "success": True,
            "filled_quantity": order.quantity,
            "avg_price": execution_price,
            "slippage": slippage * order.quantity,
            "commission": order.quantity * execution_price * 0.0003,
            "algorithm": "IMPLEMENTATION_SHORTFALL",
            "decision_price": decision_price,
            "current_price": current_price
        }


class ComplianceEngine:
    """
    Compliance and regulatory monitoring engine.
    """
    
    def __init__(self):
        self.rules = self._load_compliance_rules()
        self.trades_today: List[TradeOrder] = []
        self.positions_today: Dict[str, float] = {}
    
    def _load_compliance_rules(self) -> Dict[str, Any]:
        """Load compliance rules."""
        return {
            "max_position_size": 0.10,  # 10% of portfolio per position
            "max_sector_exposure": 0.25,  # 25% per sector
            "max_single_order": 1000000,  # ₹1M per order
            "daily_trade_limit": 50,  # Max 50 trades per day
            "restricted_symbols": [],  # List of restricted symbols
            "min_price": 1.0,  # Minimum price for trading
            "max_price": 100000.0,  # Maximum price for trading
        }
    
    def pre_trade_check(
        self, order: TradeOrder, 
        portfolio: Portfolio
    ) -> Tuple[bool, List[str]]:
        """
        Perform pre-trade compliance checks.
        Returns (allowed, list_of_violations).
        """
        violations = []
        
        try:
            # Check 1: Order size limits
            order_value = order.quantity * (order.limit_price or 0)
            if order_value > self.rules["max_single_order"]:
                violations.append(f"Order value {order_value:,.0f} exceeds limit {self.rules['max_single_order']:,.0f}")
            
            # Check 2: Restricted symbols
            if order.symbol in self.rules["restricted_symbols"]:
                violations.append(f"Symbol {order.symbol} is restricted for trading")
            
            # Check 3: Price limits
            price = order.limit_price or order.stop_price or 0
            if price > 0:
                if price < self.rules["min_price"]:
                    violations.append(f"Price {price} below minimum {self.rules['min_price']}")
                if price > self.rules["max_price"]:
                    violations.append(f"Price {price} above maximum {self.rules['max_price']}")
            
            # Check 4: Position limits
            if order.side == "BUY":
                current_position = portfolio.positions.get(order.symbol, Position(symbol=order.symbol)).quantity
                new_position = current_position + order.quantity
                portfolio_value = portfolio.total_value
                
                if portfolio_value > 0:
                    position_weight = (new_position * self._get_market_price(order.symbol, portfolio)) / portfolio_value
                    if position_weight > self.rules["max_position_size"]:
                        violations.append(f"Position weight {position_weight:.2%} exceeds limit {self.rules['max_position_size']:.2%}")
            
            # Check 5: Daily trade limit
            today_trades = len([t for t in self.trades_today if t.created_at.date() == datetime.now().date()])
            if today_trades >= self.rules["daily_trade_limit"]:
                violations.append(f"Daily trade limit exceeded: {today_trades}/{self.rules['daily_trade_limit']}")
            
            # Check 6: Sector limits (simplified)
            # Would need sector mapping in reality
            
            return len(violations) == 0, violations
            
        except Exception as e:
            logger.error(f"Compliance check failed: {e}")
            return False, [f"Compliance check error: {str(e)}"]
    
    def _get_market_price(self, symbol: str, portfolio: Portfolio) -> float:
        """Get current market price for a symbol."""
        if symbol in portfolio.positions:
            return portfolio.positions[symbol].market_price
        # In reality, would fetch from market data
        return 100.0  # Placeholder
    
    def post_trade_check(self, order: TradeOrder) -> Tuple[bool, List[str]]:
        """Perform post-trade compliance checks."""
        # Record trade for daily limits
        self.trades_today.append(order)
        
        # Additional checks could go here
        return True, []


# Initialize global instances
portfolio_constructor = PortfolioConstructor()
trade_executor = TradeExecutor()
compliance_engine = ComplianceEngine()

if __name__ == "__main__":
    # Demo
    print("Running Portfolio Construction & Operations Demo...")
    
    # Create portfolio
    portfolio = portfolio_constructor.create_portfolio("Demo Fund", "INR", 1000000.0)
    print(f"Created portfolio: {portfolio.name} (ID: {portfolio.id})")
    
    # Add position
    success = portfolio_constructor.add_position(
        portfolio.id, "NIFTY 50", 100, 18500.0
    )
    print(f"Added NIFTY 50 position: {success}")
    
    # Update price
    portfolio_constructor.update_market_prices(
        portfolio.id, {"NIFTY 50": 18700.0}
    )
    
    # Check performance
    perf = portfolio_constructor.calculate_performance(portfolio.id)
    print(f"Portfolio value: ₹{perf.get('total_value', 0):,.0f}")
    print(f"Cash: ₹{perf.get('cash', 0):,.0f}")
    print(f"Positions: {perf.get('num_positions', 0)}")
    
    # Create and execute order
    order = TradeOrder(
        symbol="NIFTY 50",
        side="BUY",
        quantity=50,
        order_type="MARKET"
    )
    
    # Compliance check
    allowed, violations = compliance_engine.pre_trade_check(order, portfolio)
    print(f"\nCompliance check: {'PASSED' if allowed else 'FAILED'}")
    if violations:
        for v in violations:
            print(f"  - {v}")
    
    if allowed:
        # Execute trade
        market_data = {"NIFTY 50_price": 18600.0, "NIFTY 50_volume": 500000}
        result = trade_executor.execute_order(order, "MARKET", market_data)
        print(f"Trade execution: {'SUCCESS' if result.get('success') else 'FAILED'}")
        if result.get("success"):
            print(f"  Filled: {result.get('filled_quantity')} @ ₹{result.get('avg_price'):.2f}")