# Z2-TREND-SE94C8 — TREND on SENSEX

**Gate:** PASSED  
**Validation:** NOT VALIDATED  
**Generated:** 2026-11-22T03:28:04  
**Engine:** ZERO v2.1 Strategy Lab

## Why this edge should exist

The slowest expression of the same edge: be long while price holds above a rising 200-day mean, flat otherwise. Few trades, long holds, and it is the honest baseline any faster strategy has to beat. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.

## Rules (plain language)

- **Entry:** Buy the next open when the close crosses above the 200-day mean while that mean is rising. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.
- **Exit:** Exit on a close back below the 200-day mean, the 2× ATR stop, or after 400 bars.
- **Risk:** Long only: measured shorts lost money on these indices. Initial stop 2.0× ATR, trailed 6× ATR once the trade is in profit; time stop 400 bars; 0.75% of equity risked per trade; 12 bps charged per leg.

## Parameters

| Parameter | Value |
|---|---|
| `confirm` | 0.55 |
| `direction_mode` | long |
| `exit_regime` | 1 |
| `regime` | 200 |
| `slope_len` | 20 |
| `stop_atr` | 2.0 |
| `time_stop` | 400 |
| `trail_atr` | 6.0 |

## Backtest result (full history)

| Metric | Value |
|---|---|
| Trades | 25 |
| Net P&L (₹) | 24,555 |
| Return | +2.46% |
| CAGR | +0.27% |
| Win rate | 28.0% |
| Profit factor | 1.34 |
| Expectancy (R) | +0.226 |
| Max drawdown | 3.47% |
| Stop-out rate | 32.0% |
| Long / short trades | 25 / 0 |

## Validation (never part of the gate)

| Check | Value |
|---|---|
| Holdout trades | 6 |
| Holdout profit factor | 0.20 |
| Holdout net P&L (₹) | -14,329 |
| Fold-consistency | 3/5 folds positive |
| Pooled fold profit factor | 1.34 |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame
spec = StrategySpec(family='TREND', index_key='SENSEX', params={'stop_atr': 2.0, 'trail_atr': 6.0, 'time_stop': 400, 'regime': 200, 'slope_len': 20, 'confirm': 0.55, 'exit_regime': 1, 'direction_mode': 'long'})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
print(validate(df, spec))
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-TREND-SE94C8` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps per leg and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._