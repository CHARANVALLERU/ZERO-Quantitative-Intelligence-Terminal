# Z2-SWEEP-NI12A6 — SWEEP on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

A wick below a well-known low that closes back inside is stop-running: resting sell stops became market sell orders, a buyer took them, and the level was reclaimed. The failed break is the signal.

## Rules (plain language)

- **Entry:** Buy the next open when the low pierces the prior 10-day low and the close is back above it.
- **Exit:** Exit on a close above the prior 10-day high, the ATR stop, or the 15-bar time stop.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 2.0R; time stop 15 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `lookback` | 10 |
| `stop_atr` | 1.5 |
| `target_lookback` | 10 |
| `target_r` | 2.0 |
| `time_stop` | 15 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 27 |
| Net P&L (₹) | 15,055 |
| Return | +1.51% |
| CAGR | +0.61% |
| Win rate | 48.1% |
| Profit factor | 1.12 |
| Expectancy (R) | +0.246 |
| Max drawdown | 3.58% |
| Sharpe (per-trade, sample-scaled) | 0.28 |
| Stop-out rate | 51.9% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='SWEEP', index_key='NIFTY', params={'lookback': 10, 'target_lookback': 10, 'stop_atr': 1.5, 'target_r': 2.0, 'time_stop': 15})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-SWEEP-NI12A6` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._