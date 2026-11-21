# Z2-SQZ-NI70FE — SQZ on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Volatility contracts when both sides are passive. The first close above the prior bar after a contraction marks the side that blinked, and dealers must re-hedge in that direction.

## Rules (plain language)

- **Entry:** Buy the next open after a 5-bar range contraction below 0.6× the 40-bar average range, when close > prior high.
- **Exit:** Exit on a close below the prior 5-day low, the ATR stop, or the 10-bar time stop.
- **Risk:** Long only. Stop 1.2× ATR below entry; target 1.8R; time stop 10 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `base` | 40 |
| `exit_lookback` | 5 |
| `ratio` | 0.6 |
| `squeeze` | 5 |
| `stop_atr` | 1.2 |
| `target_r` | 1.8 |
| `time_stop` | 10 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 6 |
| Net P&L (₹) | -33,153 |
| Return | -3.32% |
| CAGR | -1.36% |
| Win rate | 16.7% |
| Profit factor | 0.20 |
| Expectancy (R) | -0.537 |
| Max drawdown | 4.13% |
| Sharpe (per-trade, sample-scaled) | -1.93 |
| Stop-out rate | 50.0% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='SQZ', index_key='NIFTY', params={'squeeze': 5, 'base': 40, 'ratio': 0.6, 'exit_lookback': 5, 'stop_atr': 1.2, 'target_r': 1.8, 'time_stop': 10})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-SQZ-NI70FE` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._