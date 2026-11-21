# Z2-MOM-NI79BC — MOM on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Institutions cannot hide size. When price clears a 20-day high while above the 100-day mean, the marginal seller is exhausted and the remaining supply is above the market — continuation is the cheaper path.

## Rules (plain language)

- **Entry:** Buy the next open after a close above the prior 20-day high with close > 100-day mean.
- **Exit:** Exit on a close below the prior 10-day low, the ATR stop, the 2R target, or after 20 bars.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 2.0R; time stop 20 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `exit_lookback` | 10 |
| `lookback` | 20 |
| `stop_atr` | 1.5 |
| `target_r` | 2.0 |
| `time_stop` | 20 |
| `trend` | 100 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 18 |
| Net P&L (₹) | -52,459 |
| Return | -5.25% |
| CAGR | -2.16% |
| Win rate | 27.8% |
| Profit factor | 0.49 |
| Expectancy (R) | -0.231 |
| Max drawdown | 7.12% |
| Sharpe (per-trade, sample-scaled) | -1.40 |
| Stop-out rate | 66.7% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='MOM', index_key='NIFTY', params={'lookback': 20, 'trend': 100, 'exit_lookback': 10, 'stop_atr': 1.5, 'target_r': 2.0, 'time_stop': 20})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-MOM-NI79BC` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._