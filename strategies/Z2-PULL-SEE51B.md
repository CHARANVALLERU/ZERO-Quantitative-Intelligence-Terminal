# Z2-PULL-SEE51B — PULL on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

In an established uptrend, execution algos work buy orders on dips to the short mean so they do not pay up. Buying the dip with the trend is buying where the patient bid actually sits.

## Rules (plain language)

- **Entry:** Buy the next open when the 50-day mean > 200-day mean and close is 0.02 below the 50-day mean.
- **Exit:** Exit on a close 0.04 above the 50-day mean, the ATR stop, or the 30-bar time stop.
- **Risk:** Long only. Stop 2.0× ATR below entry; target 2.5R; time stop 30 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `dip` | 0.02 |
| `fast` | 50 |
| `slow` | 200 |
| `stop_atr` | 2.0 |
| `target` | 0.04 |
| `target_r` | 2.5 |
| `time_stop` | 30 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 8 |
| Net P&L (₹) | -13,014 |
| Return | -1.30% |
| CAGR | -0.53% |
| Win rate | 37.5% |
| Profit factor | 0.68 |
| Expectancy (R) | -0.109 |
| Max drawdown | 2.28% |
| Sharpe (per-trade, sample-scaled) | -0.46 |
| Stop-out rate | 62.5% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='PULL', index_key='SENSEX', params={'fast': 50, 'slow': 200, 'dip': 0.02, 'target': 0.04, 'stop_atr': 2.0, 'target_r': 2.5, 'time_stop': 30})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-PULL-SEE51B` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._