# Z2-MOM-NI5A3F — MOM on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Institutions cannot hide size. When price clears a 20-day high while above the 100-day mean, the marginal seller is exhausted and the remaining supply is above the market — continuation is the cheaper path.

## Rules (plain language)

- **Entry:** Buy the next open after a close above the prior 55-day high with close > 100-day mean.
- **Exit:** Exit on a close below the prior 20-day low, the ATR stop, the 2.5R target, or after 30 bars.
- **Risk:** Long only. Stop 2.0× ATR below entry; target 2.5R; time stop 30 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `exit_lookback` | 20 |
| `lookback` | 55 |
| `stop_atr` | 2.0 |
| `target_r` | 2.5 |
| `time_stop` | 30 |
| `trend` | 100 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 11 |
| Net P&L (₹) | -23,542 |
| Return | -2.35% |
| CAGR | -0.96% |
| Win rate | 36.4% |
| Profit factor | 0.60 |
| Expectancy (R) | -0.164 |
| Max drawdown | 3.68% |
| Sharpe (per-trade, sample-scaled) | -0.79 |
| Stop-out rate | 63.6% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='MOM', index_key='NIFTY', params={'lookback': 55, 'trend': 100, 'exit_lookback': 20, 'stop_atr': 2.0, 'target_r': 2.5, 'time_stop': 30})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-MOM-NI5A3F` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._