# Z2-ABS-SE1113 — ABS on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

A high-volume bar with a tight range is two-sided trade absorbed by a passive participant. If the close is up, the absorber is a buyer, and the path of least resistance is up.

## Rules (plain language)

- **Entry:** Buy the next open after a bar with volume z > 2.5 and range < 0.5× ATR that closes up.
- **Exit:** Exit on a close below the prior 5-day low, the ATR stop, or the 12-bar time stop.
- **Risk:** Long only. Stop 1.2× ATR below entry; target 2.0R; time stop 12 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `compress` | 0.5 |
| `exit_lookback` | 5 |
| `stop_atr` | 1.2 |
| `target_r` | 2.0 |
| `time_stop` | 12 |
| `vol_z` | 2.5 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 1 |
| Net P&L (₹) | -8,601 |
| Return | -0.86% |
| CAGR | -0.35% |
| Win rate | 0.0% |
| Profit factor | 0.00 |
| Expectancy (R) | -1.000 |
| Max drawdown | 0.86% |
| Sharpe (per-trade, sample-scaled) | 0.00 |
| Stop-out rate | 100.0% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='ABS', index_key='SENSEX', params={'vol_z': 2.5, 'compress': 0.5, 'exit_lookback': 5, 'stop_atr': 1.2, 'target_r': 2.0, 'time_stop': 12})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-ABS-SE1113` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._