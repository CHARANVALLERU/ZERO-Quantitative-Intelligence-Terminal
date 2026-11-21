# Z2-SQZ-SEBE3D — SQZ on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Volatility contracts when both sides are passive. The first close above the prior bar after a contraction marks the side that blinked, and dealers must re-hedge in that direction.

## Rules (plain language)

- **Entry:** Buy the next open after a 10-bar range contraction below 0.7× the 60-bar average range, when close > prior high.
- **Exit:** Exit on a close below the prior 7-day low, the ATR stop, or the 15-bar time stop.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 2.0R; time stop 15 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `base` | 60 |
| `exit_lookback` | 7 |
| `ratio` | 0.7 |
| `squeeze` | 10 |
| `stop_atr` | 1.5 |
| `target_r` | 2.0 |
| `time_stop` | 15 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 5 |
| Net P&L (₹) | -41,028 |
| Return | -4.10% |
| CAGR | -1.69% |
| Win rate | 0.0% |
| Profit factor | 0.00 |
| Expectancy (R) | -0.914 |
| Max drawdown | 4.10% |
| Sharpe (per-trade, sample-scaled) | -11.93 |
| Stop-out rate | 80.0% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='SQZ', index_key='SENSEX', params={'squeeze': 10, 'base': 60, 'ratio': 0.7, 'exit_lookback': 7, 'stop_atr': 1.5, 'target_r': 2.0, 'time_stop': 15})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-SQZ-SEBE3D` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._