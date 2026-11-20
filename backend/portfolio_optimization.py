"""
Portfolio Optimization Module
=================================

Institutional-grade portfolio construction algorithms.
Features:
- Modern Portfolio Theory (Markowitz) optimization
- Black-Litterman model for views-based allocation
- Risk parity allocation
- Maximum diversification portfolio
- Minimum variance portfolio
- Efficient frontier calculation
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging
from scipy.optimize import minimize
import cvxopt

logger = logging.getLogger('ZERO_Optimization')


@dataclass
class OptimizationConfig:
    """Configuration for portfolio optimization."""
    risk_free_rate: float = 0.02  # 2% risk-free rate
    target_return: float = None   # Target return for optimization
    max_weight: float = 0.4       # Maximum weight per asset
    min_weight: float = 0.0       # Minimum weight per asset
    leverage_limit: float = 1.0   # Maximum leverage (1.0 = no leverage)
    risk_aversion: float = 1.0    # Risk aversion parameter


@dataclass
class PortfolioWeights:
    """Container for portfolio weights."""
    weights: Dict[str, float]
    expected_return: float
    expected_risk: float
    sharpe_ratio: float
    optimization_method: str
    timestamp: datetime = field(default_factory=datetime.now)


class PortfolioOptimizer:
    """
    Institutional portfolio optimization engine.
    
    Implements multiple portfolio construction methodologies.
    """
    
    def __init__(self, config: OptimizationConfig = None):
        self.config = config or OptimizationConfig()
    
    def mean_variance_optimization(
        self, returns: pd.DataFrame, 
        target_return: float = None,
        risk_aversion: float = None
    ) -> Dict[str, Any]:
        """
        Modern Portfolio Theory optimization.
        
        Maximizes Sharpe ratio or minimizes variance for target return.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            # Calculate mean returns and covariance matrix
            mu = returns.mean().values
            sigma = returns.cov().values
            
            n_assets = len(mu)
            
            cfg_risk_aversion = risk_aversion or self.config.risk_aversion
            target_ret = target_return or self.config.target_return
            
            # Objective function: maximize utility = w'μ - λ/2 w'Σw
            def utility(weights):
                port_return = np.dot(weights, mu)
                port_variance = np.dot(weights.T, np.dot(sigma, weights))
                return -(port_return - 0.5 * cfg_risk_aversion * port_variance)
            
            # Constraints
            constraints = [
                {"type": "eq", "fun": lambda x: np.sum(x) - 1.0},  # weights sum to 1
            ]
            
            if target_ret is not None:
                constraints.append({
                    "type": "eq", 
                    "fun": lambda x: np.dot(x, mu) - target_ret
                })
            
            # Bounds
            bounds = tuple((self.config.min_weight, self.config.max_weight) 
                          for _ in range(n_assets))
            
            # Initial guess (equal weights)
            x0 = np.array([1.0 / n_assets] * n_assets)
            
            # Optimize
            result = minimize(
                utility, x0, method='SLSQP', 
                bounds=bounds, constraints=constraints
            )
            
            if not result.success:
                return {"error": f"Optimization failed: {result.message}"}
            
            weights = result.x
            port_return = np.dot(weights, mu)
            port_variance = np.dot(weights.T, np.dot(sigma, weights))
            port_volatility = np.sqrt(port_variance)
            
            sharpe = (port_return - self.config.risk_free_rate) / port_volatility if port_volatility > 0 else 0
            
            return {
                "weights": dict(zip(returns.columns, weights)),
                "expected_return": float(port_return),
                "expected_risk": float(port_volatility),
                "sharpe_ratio": float(sharpe),
                "method": "mean_variance",
                "target_return": target_ret,
                "risk_aversion": cfg_risk_aversion
            }
            
        except Exception as e:
            logger.error(f"Mean-variance optimization failed: {e}")
            return {"error": str(e)}
    
    def black_litterman_optimization(
        self, returns: pd.DataFrame, 
        market_caps: Dict[str, float] = None,
        views: Dict[str, Tuple[float, float]] = None,  # {asset: (view, confidence)}
        tau: float = 0.025
    ) -> Dict[str, Any]:
        """
        Black-Litterman model optimization.
        
        Combines market equilibrium with investor views.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            # Calculate historical moments
            mu = returns.mean().values
            sigma = returns.cov().values
            n_assets = len(mu)
            
            # Market cap weights (if not provided, use equal weights)
            if market_caps is None:
                market_weights = np.array([1.0 / n_assets] * n_assets)
            else:
                market_weights = np.array([market_caps.get(asset, 1.0) 
                                         for asset in returns.columns])
                market_weights = market_weights / np.sum(market_weights)
            
            # Implied equilibrium returns
            delta = self._impute_risk_aversion(sigma, market_weights)
            pi = delta * np.dot(sigma, market_weights)
            
            # Views matrix
            if views is None or len(views) == 0:
                # No views - return market portfolio
                omega = np.eye(n_assets) * tau * 0.01  # Small uncertainty
                mu_bl = pi
            else:
                # Build views matrices
                view_assets = list(views.keys())
                n_views = len(view_assets)
                
                P = np.zeros((n_views, n_assets))
                Q = np.zeros(n_views)
                omega_diag = []
                
                for i, (asset, (view, confidence)) in enumerate(views.items()):
                    asset_idx = list(returns.columns).index(asset)
                    P[i, asset_idx] = 1.0
                    Q[i] = view
                    # Confidence: higher = lower uncertainty
                    omega_diag.append(1.0 / max(confidence, 0.001))
                
                omega = np.diag(omega_diag)
                
                # Black-Litterman formula
                tau_sigma = tau * sigma
                inv_tau_sigma = np.linalg.inv(tau_sigma)
                inv_omega = np.linalg.inv(omega)
                
                # Posterior covariance
                m1 = np.linalg.inv(inv_tau_sigma + P.T @ inv_omega @ P)
                m2 = inv_tau_sigma @ pi + P.T @ inv_omega @ Q
                mu_bl = m1 @ m2
                
                # Posterior covariance (for risk calculation)
                sigma_bl = m1
            
            # Optimize portfolio with posterior returns
            def utility_bl(weights):
                port_return = np.dot(weights, mu_bl)
                port_variance = np.dot(weights.T, np.dot(sigma_bl, weights))
                return -(port_return - 0.5 * self.config.risk_aversion * port_variance)
            
            constraints = [{"type": "eq", "fun": lambda x: np.sum(x) - 1.0}]
            bounds = tuple((self.config.min_weight, self.config.max_weight) 
                          for _ in range(n_assets))
            x0 = np.array([1.0 / n_assets] * n_assets)
            
            result = minimize(
                utility_bl, x0, method='SLSQP', 
                bounds=bounds, constraints=constraints
            )
            
            if not result.success:
                return {"error": f"Black-Litterman optimization failed: {result.message}"}
            
            weights = result.x
            port_return = np.dot(weights, mu_bl)
            port_variance = np.dot(weights.T, np.dot(sigma_bl, weights))
            port_volatility = np.sqrt(port_variance)
            
            sharpe = (port_return - self.config.risk_free_rate) / port_volatility if port_volatility > 0 else 0
            
            return {
                "weights": dict(zip(returns.columns, weights)),
                "expected_return": float(port_return),
                "expected_risk": float(port_volatility),
                "sharpe_ratio": float(sharpe),
                "method": "black_litterman",
                "equilibrium_returns": dict(zip(returns.columns, pi)),
                "posterior_returns": dict(zip(returns.columns, mu_bl))
            }
            
        except Exception as e:
            logger.error(f"Black-Litterman optimization failed: {e}")
            return {"error": str(e)}
    
    def risk_parity_optimization(
        self, returns: pd.DataFrame,
        target_risk_contrib: np.ndarray = None
    ) -> Dict[str, Any]:
        """
        Risk parity (equal risk contribution) optimization.
        
        Each asset contributes equally to portfolio risk.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            # Calculate covariance matrix
            sigma = returns.cov().values
            n_assets = len(sigma)
            
            if target_risk_contrib is None:
                target_risk_contrib = np.array([1.0 / n_assets] * n_assets)
            
            # Risk contribution function
            def risk_contribution(weights):
                port_variance = np.dot(weights.T, np.dot(sigma, weights))
                if port_variance <= 0:
                    return np.zeros(n_assets)
                port_volatility = np.sqrt(port_variance)
                marginal_contrib = np.dot(sigma, weights) / port_volatility
                return weights * marginal_contrib / port_volatility
            
            # Objective: minimize sum of squared differences from target risk contribution
            def objective(weights):
                rc = risk_contribution(weights)
                return np.sum((rc - target_risk_contrib) ** 2)
            
            # Constraints
            constraints = [
                {"type": "eq", "fun": lambda x: np.sum(x) - 1.0},  # weights sum to 1
            ]
            
            # Bounds
            bounds = tuple((self.config.min_weight, self.config.max_weight) 
                          for _ in range(n_assets))
            
            # Initial guess (inverse volatility weights)
            vol = np.sqrt(np.diag(sigma))
            x0 = (1.0 / vol) / np.sum(1.0 / vol)
            x0 = np.clip(x0, self.config.min_weight, self.config.max_weight)
            x0 = x0 / np.sum(x0)  # Renormalize
            
            # Optimize
            result = minimize(
                objective, x0, method='SLSQP', 
                bounds=bounds, constraints=constraints
            )
            
            if not result.success:
                return {"error": f"Risk parity optimization failed: {result.message}"}
            
            weights = result.x
            port_variance = np.dot(weights.T, np.dot(sigma, weights))
            port_volatility = np.sqrt(port_variance)
            
            # Calculate expected return (using historical mean)
            mu = returns.mean().values
            port_return = np.dot(weights, mu)
            
            sharpe = (port_return - self.config.risk_free_rate) / port_volatility if port_volatility > 0 else 0
            
            # Calculate actual risk contributions
            actual_rc = risk_contribution(weights)
            
            return {
                "weights": dict(zip(returns.columns, weights)),
                "expected_return": float(port_return),
                "expected_risk": float(port_volatility),
                "sharpe_ratio": float(sharpe),
                "method": "risk_parity",
                "risk_contributions": dict(zip(returns.columns, actual_rc)),
                "target_risk_contributions": dict(zip(returns.columns, target_risk_contrib))
            }
            
        except Exception as e:
            logger.error(f"Risk parity optimization failed: {e}")
            return {"error": str(e)}
    
    def minimum_variance_optimization(
        self, returns: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Minimum variance portfolio optimization.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            sigma = returns.cov().values
            n_assets = len(sigma)
            
            # Minimize variance: w'Σw
            def objective(weights):
                return np.dot(weights.T, np.dot(sigma, weights))
            
            constraints = [{"type": "eq", "fun": lambda x: np.sum(x) - 1.0}]
            bounds = tuple((self.config.min_weight, self.config.max_weight) 
                          for _ in range(n_assets))
            x0 = np.array([1.0 / n_assets] * n_assets)
            
            result = minimize(
                objective, x0, method='SLSQP', 
                bounds=bounds, constraints=constraints
            )
            
            if not result.success:
                return {"error": f"Minimum variance optimization failed: {result.message}"}
            
            weights = result.x
            mu = returns.mean().values
            port_return = np.dot(weights, mu)
            port_variance = np.dot(weights.T, np.dot(sigma, weights))
            port_volatility = np.sqrt(port_variance)
            
            sharpe = (port_return - self.config.risk_free_rate) / port_volatility if port_volatility > 0 else 0
            
            return {
                "weights": dict(zip(returns.columns, weights)),
                "expected_return": float(port_return),
                "expected_risk": float(port_volatility),
                "sharpe_ratio": float(sharpe),
                "method": "minimum_variance"
            }
            
        except Exception as e:
            logger.error(f"Minimum variance optimization failed: {e}")
            return {"error": str(e)}
    
    def efficient_frontier(
        self, returns: pd.DataFrame,
        n_points: int = 50
    ) -> Dict[str, Any]:
        """
        Calculate efficient frontier.
        
        Returns portfolios across different risk levels.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            mu = returns.mean().values
            sigma = returns.cov().values
            n_assets = len(mu)
            
            # Range of target returns
            min_ret = mu.min()
            max_ret = mu.max()
            target_returns = np.linspace(min_ret, max_ret, n_points)
            
            frontier_portfolios = []
            
            for target_ret in target_returns:
                result = self.mean_variance_optimization(
                    returns, target_return=target_ret
                )
                if "error" not in result:
                    frontier_portfolios.append(result)
            
            return {
                "frontier": frontier_portfolios,
                "target_returns": target_returns.tolist(),
                "n_portfolios": len(frontier_portfolios)
            }
            
        except Exception as e:
            logger.error(f"Efficient frontier calculation failed: {e}")
            return {"error": str(e)}
    
    def _impute_risk_aversion(
        self, sigma: np.ndarray, 
        market_weights: np.ndarray
    ) -> float:
        """
        Impute risk aversion from market portfolio.
        
        λ = (μ - rf) / (w'Σw)
        """
        try:
            # Use historical market return as proxy
            market_return = np.dot(market_weights, returns.mean().values)
            market_variance = np.dot(market_weights.T, np.dot(sigma, market_weights))
            
            if market_variance <= 0:
                return self.config.risk_aversion
            
            lambda_ = (market_return - self.config.risk_free_rate) / market_variance
            return max(lambda_, 0.1)  # Floor at 0.1
        except:
            return self.config.risk_aversion


if __name__ == "__main__":
    # Demo
    print("Running Portfolio Optimization Demo...")
    
    # Generate sample data
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=252, freq="D")
    returns_data = pd.DataFrame({
        "NIFTY 50": np.random.normal(0.0008, 0.015, 252),
        "BANKNIFTY": np.random.normal(0.0010, 0.018, 252),
        "SENSEX": np.random.normal(0.0009, 0.016, 252)
    })
    
    # Initialize optimizer
    opt = PortfolioOptimizer()
    
    # MVO
    mvo = opt.mean_variance_optimization(returns_data)
    print(f"\nMean-Variance Optimization:")
    print(f"  Weights: {mvo.get('weights', {})}")
    print(f"  Return: {mvo.get('expected_return', 0):.4f}")
    print(f"  Risk: {mvo.get('expected_risk', 0):.4f}")
    print(f"  Sharpe: {mvo.get('sharpe_ratio', 0):.4f}")
    
    # Risk Parity
    rp = opt.risk_parity_optimization(returns_data)
    print(f"\nRisk Parity Optimization:")
    print(f"  Weights: {rp.get('weights', {})}")
    print(f"  Return: {rp.get('expected_return', 0):.4f}")
    print(f"  Risk: {rp.get('expected_risk', 0):.4f}")
    print(f"  Sharpe: {rp.get('sharpe_ratio', 0):.4f}")
    
    # Min Variance
    mv = opt.minimum_variance_optimization(returns_data)
    print(f"\nMinimum Variance Optimization:")
    print(f"  Weights: {mv.get('weights', {})}")
    print(f"  Return: {mv.get('expected_return', 0):.4f}")
    print(f"  Risk: {mv.get('expected_risk', 0):.4f}")
    print(f"  Sharpe: {mv.get('sharpe_ratio', 0):.4f}")