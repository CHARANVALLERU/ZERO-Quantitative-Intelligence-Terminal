# Z2-MOM-SEA3E4 — MOM on SENSEX

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
| Trades | 9 |
| Net P&L (₹) | -4,521 |
| Return | -0.45% |
| CAGR | -0.18% |
| Win rate | 44.4% |
| Profit factor | 0.89 |
| Expectancy (R) | +0.048 |
| Max drawdown | 2.83% |
| Sharpe (per-trade, sample-scaled) | -0.15 |
| Stop-out rate | 55.6% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='MOM', index_key='SENSEX', params={'lookback': 55, 'trend': 100, 'exit_lookback': 20, 'stop_atr': 2.0, 'target_r': 2.5, 'time_stop': 30})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-MOM-SEA3E4` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._