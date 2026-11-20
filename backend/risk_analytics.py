"""
Institutional Risk Analytics Module
=================================

Enterprise-grade risk management tools for hedge fund operations.
Features:
- Value at Risk (VaR) calculations (Historical, Parametric, Monte Carlo)
- Drawdown analysis with multiple methodologies
- Stress testing with custom scenarios
- Correlation matrix analysis
- Risk decomposition (factor risk, idiosyncratic risk)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger('ZERO_Risk')


@dataclass
class VaRConfig:
    """Configuration for VaR calculations."""
    confidence_level: float = 0.95  # 95% confidence
    var_method: str = "historical"  # historical, parametric, monte_carlo
    lookback_period: int = 252  # trading days
    output: str = "absolute"  # absolute or relative
    multiplier: float = 1.0  # Multiplier for VaR (e.g., 1.645 for 95%)


@dataclass
class StressScenario:
    """Stress testing scenario."""
    name: str
    description: str
    changes: Dict[str, float]  # {symbol: percent_change}
    time_period: str = "1d"  # 1d, 5d, 10d
    apply_to: str = "prices"  # prices or returns


@dataclass
class RiskMetrics:
    """Risk metrics for a portfolio."""
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    total_risk: float = 0.0
    idiosyncratic_risk: float = 0.0
    systematic_risk: float = 0.0
    correlation_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)


class RiskAnalytics:
    """
    Institutional risk analytics engine.
    
    Provides enterprise-grade risk management capabilities.
    """
    
    def __init__(self, config: VaRConfig = None):
        self.config = config or VaRConfig()
        self.scenarios: List[StressScenario] = []
    
    def calculate_var(
        self, returns: pd.Series, 
        config: VaRConfig = None
    ) -> Dict[str, float]:
        """
        Calculate Value at Risk.
        
        Methods:
        - historical: Empirical quantiles
        - parametric: Variance-covariance
        - monte_carlo: Simulation-based
        """
        cfg = config or self.config
        
        if len(returns) < 2:
            return {"error": "Insufficient data"}
        
        try:
            if cfg.var_method == "historical":
                return self._historical_var(returns, cfg)
            elif cfg.var_method == "parametric":
                return self._parametric_var(returns, cfg)
            elif cfg.var_method == "monte_carlo":
                return self._monte_carlo_var(returns, cfg)
            else:
                return {"error": f"Unknown method: {cfg.var_method}"}
        except Exception as e:
            logger.error(f"VaR calculation failed: {e}")
            return {"error": str(e)}
    
    def _historical_var(
        self, returns: pd.Series, 
        config: VaRConfig
    ) -> Dict[str, float]:
        """Historical simulation VaR."""
        try:
            # Sort returns
            sorted_returns = returns.sort_values()
            
            # Calculate VaR based on confidence level
            alpha = 1 - config.confidence_level
            idx = int(len(sorted_returns) * alpha)
            
            var = sorted_returns.iloc[idx]
            
            return {
                "var": var if config.output == "absolute" else abs(var),
                "confidence": config.confidence_level,
                "method": "historical",
                "lookback_days": len(returns)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _parametric_var(
        self, returns: pd.Series, 
        config: VaRConfig
    ) -> Dict[str, float]:
        """Parametric (variance-covariance) VaR."""
        try:
            mu = returns.mean()
            sigma = returns.std()
            
            # Use normal distribution multiplier
            z = self._normal_quantile(config.confidence_level)
            var = mu + z * sigma
            
            return {
                "var": var if config.output == "absolute" else abs(var),
                "confidence": config.confidence_level,
                "method": "parametric",
                "mean_return": float(mu),
                "std_dev": float(sigma)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _monte_carlo_var(
        self, returns: pd.Series, 
        config: VaRConfig
    ) -> Dict[str, float]:
        """Monte Carlo simulation VaR."""
        try:
            n_simulations = 100000
            
            # Fit distribution
            mu, sigma = returns.mean(), returns.std()
            
            # Generate simulations
            simulated_returns = np.random.normal(mu, sigma, n_simulations)
            
            # Calculate VaR
            var = np.percentile(simulated_returns, config.confidence_level * 100)
            
            return {
                "var": var if config.output == "absolute" else abs(var),
                "confidence": config.confidence_level,
                "method": "monte_carlo",
                "simulations": n_simulations
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _normal_quantile(self, confidence: float) -> float:
        """Get z-score for confidence level."""
        from scipy import stats
        return stats.norm.ppf(confidence)
    
    def calculate_cvar(
        self, returns: pd.Series, 
        var: float
    ) -> float:
        """
        Calculate Conditional VaR (Expected Shortfall).
        
        The average of returns in the worst var.percent cases.
        """
        try:
            worst_returns = returns[returns <= var]
            if len(worst_returns) == 0:
                return 0.0
            return float(worst_returns.mean())
        except Exception as e:
            logger.error(f"CVaR calculation failed: {e}")
            return 0.0
    
    def calculate_drawdown(
        self, prices: pd.Series
    ) -> Dict[str, float]:
        """
        Calculate maximum drawdown and related metrics.
        """
        try:
            if len(prices) < 2:
                return {"error": "Insufficient data"}
            
            # Calculate cumulative returns
            returns = prices.pct_change().dropna()
            cumulative = (1 + returns).cumprod()
            
            # Calculate running maximum
            running_max = cumulative.expanding().max()
            
            # Calculate drawdown
            drawdown = (cumulative - running_max) / running_max
            
            # Maximum drawdown
            max_dd = drawdown.min()
            
            # Current drawdown
            current_dd = drawdown.iloc[-1]
            
            # Drawdown duration (consecutive days below zero)
            dd_duration = (drawdown < 0).sum()
            
            # Recovery time (from max drawdown to recovery)
            recovery_idx = (drawdown == 0).argmax() if (drawdown == 0).any() else len(drawdown)
            recovery_time = recovery_idx - drawdown.idxmin()
            
            return {
                "max_drawdown": float(max_dd),
                "current_drawdown": float(current_dd),
                "drawdown_duration_days": int(dd_duration),
                "recovery_time_days": int(recovery_time),
                "recovery_time_hours": int(recovery_time * 24)
            }
        except Exception as e:
            logger.error(f"Drawdown calculation failed: {e}")
            return {"error": str(e)}
    
    def calculate_calmar_ratio(
        self, returns: pd.Series, 
        prices: pd.Series
    ) -> float:
        """
        Calculate Calmar ratio.
        
        Calmar = CAGR / Max Drawdown
        """
        try:
            if len(prices) < 2:
                return 0.0
            
            # CAGR (Compound Annual Growth Rate)
            n_years = len(prices) / 252.0
            cagr = (prices.iloc[-1] / prices.iloc[0]) ** (1 / n_years) - 1
            
            # Max drawdown
            drawdown = self.calculate_drawdown(prices)
            max_dd = drawdown.get("max_drawdown", 0)
            
            if max_dd == 0:
                return float('inf')
            
            return float(cagr / abs(max_dd))
        except Exception as e:
            logger.error(f"Calmar ratio calculation failed: {e}")
            return 0.0
    
    def calculate_sortino_ratio(
        self, returns: pd.Series
    ) -> float:
        """
        Calculate Sortino ratio.
        
        Sortino = (Return - Risk-Free) / Downside Deviation
        """
        try:
            if len(returns) < 2:
                return 0.0
            
            mean_return = returns.mean()
            target = 0  # Or use risk-free rate
            
            # Downside deviation (semi-deviation)
            downside_returns = returns[returns < target]
            if len(downside_returns) == 0:
                return float('inf')
            
            downside_deviation = downside_returns.std()
            
            if downside_deviation == 0:
                return float('inf')
            
            return float((mean_return - target) / downside_deviation)
        except Exception as e:
            logger.error(f"Sortino ratio calculation failed: {e}")
            return 0.0
    
    def stress_test(
        self, returns: pd.DataFrame, 
        scenarios: List[StressScenario]
    ) -> Dict[str, Dict]:
        """
        Run stress tests on portfolio returns.
        """
        results = {}
        
        for scenario in scenarios:
            try:
                # Apply scenario
                shocked_returns = returns.copy()
                for symbol, change_pct in scenario.changes.items():
                    if symbol in shocked_returns.columns:
                        shocked_returns[symbol] = shocked_returns[symbol] * (1 + change_pct / 100)
                
                # Calculate portfolio return under stress
                portfolio_returns = shocked_returns.mean(axis=1)
                
                # VaR under stress
                var_result = self.calculate_var(portfolio_returns)
                
                results[scenario.name] = {
                    "description": scenario.description,
                    "time_period": scenario.time_period,
                    "portfolio_impact_pct": float(portfolio_returns.mean() * 100),
                    "var_95_pct": var_result.get("var", 0),
                    "scenario_changes": scenario.changes
                }
            except Exception as e:
                logger.error(f"Stress test failed for {scenario.name}: {e}")
                results[scenario.name] = {
                    "error": str(e)
                }
        
        return results
    
    def calculate_correlation_matrix(
        self, returns: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Calculate correlation matrix for portfolio assets.
        """
        try:
            return returns.corr()
        except Exception as e:
            logger.error(f"Correlation calculation failed: {e}")
            return pd.DataFrame()
    
    def calculate_risk_decomposition(
        self, returns: pd.DataFrame,
        factor_returns: pd.DataFrame = None
    ) -> Dict[str, float]:
        """
        Decompose portfolio risk into systematic and idiosyncratic components.
        """
        try:
            if len(returns.columns) < 2:
                return {"error": "Need at least 2 assets"}
            
            # Total risk (standard deviation of equal-weighted portfolio)
            weights = np.ones(len(returns.columns)) / len(returns.columns)
            portfolio_returns = returns.dot(weights)
            total_risk = portfolio_returns.std()
            
            # Systematic risk (market factor)
            if factor_returns is not None and len(factor_returns.columns) > 0:
                beta = returns.cov(factor_returns) / factor_returns.var()
                systematic_risk = float(total_risk * beta)
            else:
                systematic_risk = 0
            
            # Idiosyncratic risk
            idiosyncratic_risk = total_risk - systematic_risk
            
            return {
                "total_risk": float(total_risk),
                "systematic_risk": float(systematic_risk),
                "idiosyncratic_risk": float(idiosyncratic_risk),
                "systematic_pct": float(systematic_risk / total_risk * 100) if total_risk > 0 else 0,
                "idiosyncratic_pct": float(idiosyncratic_risk / total_risk * 100) if total_risk > 0 else 0
            }
        except Exception as e:
            logger.error(f"Risk decomposition failed: {e}")
            return {"error": str(e)}


# Pre-defined stress scenarios
STANDARD_STRESS_SCENARIOS = [
    StressScenario(
        name="2008 Financial Crisis",
        description="Global financial crisis scenario",
        changes={
            "NIFTY 50": -50.0,
            "BANKNIFTY": -55.0,
            "SENSEX": -52.0
        }
    ),
    StressScenario(
        name="2020 COVID Crash",
        description="COVID-19 market crash",
        changes={
            "NIFTY 50": -35.0,
            "BANKNIFTY": -40.0,
            "SENSEX": -37.0
        }
    ),
    StressScenario(
        name="2016 Brexit",
        description="Brexit referendum impact",
        changes={
            "NIFTY 50": -8.0,
            "BANKNIFTY": -10.0,
            "SENSEX": -7.5
        }
    ),
    StressScenario(
        name="Interest Rate Shock",
        description="10% rate hike scenario",
        changes={
            "NIFTY 50": -15.0,
            "BANKNIFTY": -20.0,
            "SENSEX": -16.0
        }
    ),
    StressScenario(
        name="Commodity Price Shock",
        description="Crude oil spike +200%",
        changes={
            "NIFTY 50": -25.0,
            "BANKNIFTY": -30.0,
            "SENSEX": -27.0
        }
    )
]


if __name__ == "__main__":
    # Demo
    print("Running Risk Analytics Demo...")
    
    # Generate sample data
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=500, freq="D")
    prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.02, 500)))
    prices_df = pd.DataFrame({"Asset": prices})
    returns = prices_df.pct_change().dropna()
    
    # Calculate metrics
    risk = RiskAnalytics()
    
    # VaR
    var = risk.calculate_var(returns["Asset"])
    print(f"\nVaR (95%): {var.get('var', 0):.2f}")
    
    # Drawdown
    dd = risk.calculate_drawdown(prices_df["Asset"])
    print(f"Max Drawdown: {dd.get('max_drawdown', 0):.2%}")
    
    # Stress test
    scenarios = [STANDARD_STRESS_SCENARIOS[0], STANDARD_STRESS_SCENARIOS[1]]
    results = risk.stress_test(returns, scenarios)
    print(f"\nStress Test Results:")
    for name, result in results.items():
        print(f"  {name}: {result.get('portfolio_impact_pct', 0):.2f}% impact")
