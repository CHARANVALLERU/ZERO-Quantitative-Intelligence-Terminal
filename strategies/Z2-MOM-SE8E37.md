# Z2-MOM-SE8E37 — MOM on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Institutions cannot hide size. When price clears a 20-day high while above the 100-day mean, the marginal seller is exhausted and the remaining supply is above the market — continuation is the cheaper path.

## Rules (plain language)

- **Entry:** Buy the next open after a close above the prior 10-day high with close > 50-day mean.
- **Exit:** Exit on a close below the prior 7-day low, the ATR stop, the 1.8R target, or after 12 bars.
- **Risk:** Long only. Stop 1.2× ATR below entry; target 1.8R; time stop 12 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `exit_lookback` | 7 |
| `lookback` | 10 |
| `stop_atr` | 1.2 |
| `target_r` | 1.8 |
| `time_stop` | 12 |
| `trend` | 50 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 24 |
| Net P&L (₹) | -1,898 |
| Return | -0.19% |
| CAGR | -0.08% |
| Win rate | 45.8% |
| Profit factor | 0.98 |
| Expectancy (R) | +0.192 |
| Max drawdown | 5.33% |
| Sharpe (per-trade, sample-scaled) | -0.04 |
| Stop-out rate | 45.8% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='MOM', index_key='SENSEX', params={'lookback': 10, 'trend': 50, 'exit_lookback': 7, 'stop_atr': 1.2, 'target_r': 1.8, 'time_stop': 12})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-MOM-SE8E37` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._