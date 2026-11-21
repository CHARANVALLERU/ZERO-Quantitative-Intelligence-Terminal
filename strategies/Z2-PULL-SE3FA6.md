# Z2-PULL-SE3FA6 — PULL on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

In an established uptrend, execution algos work buy orders on dips to the short mean so they do not pay up. Buying the dip with the trend is buying where the patient bid actually sits.

## Rules (plain language)

- **Entry:** Buy the next open when the 20-day mean > 100-day mean and close is 0.01 below the 20-day mean.
- **Exit:** Exit on a close 0.02 above the 20-day mean, the ATR stop, or the 15-bar time stop.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 2.0R; time stop 15 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `dip` | 0.01 |
| `fast` | 20 |
| `slow` | 100 |
| `stop_atr` | 1.5 |
| `target` | 0.02 |
| `target_r` | 2.0 |
| `time_stop` | 15 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 18 |
| Net P&L (₹) | -67,414 |
| Return | -6.74% |
| CAGR | -2.80% |
| Win rate | 22.2% |
| Profit factor | 0.45 |
| Expectancy (R) | -0.333 |
| Max drawdown | 9.32% |
| Sharpe (per-trade, sample-scaled) | -1.66 |
| Stop-out rate | 77.8% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='PULL', index_key='SENSEX', params={'fast': 20, 'slow': 100, 'dip': 0.01, 'target': 0.02, 'stop_atr': 1.5, 'target_r': 2.0, 'time_stop': 15})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-PULL-SE3FA6` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._