# Z2-PULL-SE76C6 — PULL on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

In an established uptrend, execution algos work buy orders on dips to the short mean so they do not pay up. Buying the dip with the trend is buying where the patient bid actually sits.

## Rules (plain language)

- **Entry:** Buy the next open when the 10-day mean > 50-day mean and close is 0.015 below the 10-day mean.
- **Exit:** Exit on a close 0.025 above the 10-day mean, the ATR stop, or the 12-bar time stop.
- **Risk:** Long only. Stop 1.2× ATR below entry; target 2.0R; time stop 12 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `dip` | 0.015 |
| `fast` | 10 |
| `slow` | 50 |
| `stop_atr` | 1.2 |
| `target` | 0.025 |
| `target_r` | 2.0 |
| `time_stop` | 12 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 11 |
| Net P&L (₹) | 12,562 |
| Return | +1.26% |
| CAGR | +0.51% |
| Win rate | 45.5% |
| Profit factor | 1.23 |
| Expectancy (R) | +0.347 |
| Max drawdown | 3.51% |
| Sharpe (per-trade, sample-scaled) | 0.32 |
| Stop-out rate | 54.5% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='PULL', index_key='SENSEX', params={'fast': 10, 'slow': 50, 'dip': 0.015, 'target': 0.025, 'stop_atr': 1.2, 'target_r': 2.0, 'time_stop': 12})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-PULL-SE76C6` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._