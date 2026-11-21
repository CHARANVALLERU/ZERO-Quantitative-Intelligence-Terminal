# Z2-SWEEP-SE441E — SWEEP on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

A wick below a well-known low that closes back inside is stop-running: resting sell stops became market sell orders, a buyer took them, and the level was reclaimed. The failed break is the signal.

## Rules (plain language)

- **Entry:** Buy the next open when the low pierces the prior 20-day low and the close is back above it.
- **Exit:** Exit on a close above the prior 20-day high, the ATR stop, or the 25-bar time stop.
- **Risk:** Long only. Stop 2.0× ATR below entry; target 2.5R; time stop 25 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `lookback` | 20 |
| `stop_atr` | 2.0 |
| `target_lookback` | 20 |
| `target_r` | 2.5 |
| `time_stop` | 25 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 17 |
| Net P&L (₹) | -4,663 |
| Return | -0.47% |
| CAGR | -0.19% |
| Win rate | 41.2% |
| Profit factor | 0.95 |
| Expectancy (R) | +0.082 |
| Max drawdown | 3.30% |
| Sharpe (per-trade, sample-scaled) | -0.11 |
| Stop-out rate | 58.8% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='SWEEP', index_key='SENSEX', params={'lookback': 20, 'target_lookback': 20, 'stop_atr': 2.0, 'target_r': 2.5, 'time_stop': 25})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-SWEEP-SE441E` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._