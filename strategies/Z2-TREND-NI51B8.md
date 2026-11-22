# Z2-TREND-NI51B8 — TREND on NIFTY

**Gate:** PASSED  
**Validation:** NOT VALIDATED  
**Generated:** 2026-11-22T03:28:01  
**Engine:** ZERO v2.1 Strategy Lab

## Why this edge should exist

The slowest expression of the same edge: be long while price holds above a rising 200-day mean, flat otherwise. Few trades, long holds, and it is the honest baseline any faster strategy has to beat. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.

## Rules (plain language)

- **Entry:** Buy the next open when the close crosses above the 200-day mean while that mean is rising. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.
- **Exit:** Exit on a close back below the 200-day mean, the 3× ATR stop, or after 400 bars.
- **Risk:** Long only: measured shorts lost money on these indices. Initial stop 3.0× ATR, not trailed; time stop 400 bars; 0.75% of equity risked per trade; 12 bps charged per leg.

## Parameters

| Parameter | Value |
|---|---|
| `confirm` | 0.55 |
| `direction_mode` | long |
| `exit_regime` | 1 |
| `regime` | 200 |
| `slope_len` | 20 |
| `stop_atr` | 3.0 |
| `time_stop` | 400 |
| `trail_atr` | 0.0 |

## Backtest result (full history)

| Metric | Value |
|---|---|
| Trades | 22 |
| Net P&L (₹) | 46,713 |
| Return | +4.67% |
| CAGR | +0.51% |
| Win rate | 27.3% |
| Profit factor | 2.01 |
| Expectancy (R) | +0.356 |
| Max drawdown | 1.99% |
| Stop-out rate | 0.0% |
| Long / short trades | 22 / 0 |

## Validation (never part of the gate)

| Check | Value |
|---|---|
| Holdout trades | 3 |
| Holdout profit factor | 0.00 |
| Holdout net P&L (₹) | -12,572 |
| Fold-consistency | 2/5 folds positive |
| Pooled fold profit factor | 2.17 |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame
spec = StrategySpec(family='TREND', index_key='NIFTY', params={'stop_atr': 3.0, 'trail_atr': 0.0, 'time_stop': 400, 'regime': 200, 'slope_len': 20, 'confirm': 0.55, 'exit_regime': 1, 'direction_mode': 'long'})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
print(validate(df, spec))
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-TREND-NI51B8` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps per leg and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._