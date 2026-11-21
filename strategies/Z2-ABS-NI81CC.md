# Z2-ABS-NI81CC — ABS on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

A high-volume bar with a tight range is two-sided trade absorbed by a passive participant. If the close is up, the absorber is a buyer, and the path of least resistance is up.

## Rules (plain language)

- **Entry:** Buy the next open after a bar with volume z > 2 and range < 0.6× ATR that closes up.
- **Exit:** Exit on a close below the prior 7-day low, the ATR stop, or the 15-bar time stop.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 2.0R; time stop 15 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `compress` | 0.6 |
| `exit_lookback` | 7 |
| `stop_atr` | 1.5 |
| `target_r` | 2.0 |
| `time_stop` | 15 |
| `vol_z` | 2.0 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 4 |
| Net P&L (₹) | -31,042 |
| Return | -3.10% |
| CAGR | -1.27% |
| Win rate | 0.0% |
| Profit factor | 0.00 |
| Expectancy (R) | -0.863 |
| Max drawdown | 3.10% |
| Sharpe (per-trade, sample-scaled) | -5.88 |
| Stop-out rate | 75.0% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='ABS', index_key='NIFTY', params={'vol_z': 2.0, 'compress': 0.6, 'exit_lookback': 7, 'stop_atr': 1.5, 'target_r': 2.0, 'time_stop': 15})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-ABS-NI81CC` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._