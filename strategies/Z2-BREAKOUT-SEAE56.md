# Z2-BREAKOUT-SEAE56 — BREAKOUT on SENSEX

**Gate:** PASSED  
**Validation:** VALIDATED  
**Generated:** 2026-11-22T03:28:02  
**Engine:** ZERO v2.1 Strategy Lab

## Why this edge should exist

Time-series momentum: an index that prints a new 15-30 day high while above a rising 200-day mean is being repriced, not exhausted. Trend persistence is one of the most replicated effects in index data, and it survives costs because the average winner is several times the ATR stop. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.

## Rules (plain language)

- **Entry:** Buy the next open when the close exceeds the prior 20-day high. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.
- **Exit:** Trail a 6× ATR chandelier stop under the highest high since entry; flatten on a close back below the regime mean or after 400 bars.
- **Risk:** Long only: measured shorts lost money on these indices. Initial stop 2.0× ATR, trailed 6× ATR once the trade is in profit; time stop 400 bars; 0.75% of equity risked per trade; 12 bps charged per leg.

## Parameters

| Parameter | Value |
|---|---|
| `confirm` | 0.55 |
| `direction_mode` | long |
| `exit_regime` | 1 |
| `lookback` | 20 |
| `regime` | 200 |
| `slope_len` | 20 |
| `stop_atr` | 2.0 |
| `time_stop` | 400 |
| `trail_atr` | 6.0 |

## Backtest result (full history)

| Metric | Value |
|---|---|
| Trades | 24 |
| Net P&L (₹) | 88,367 |
| Return | +8.84% |
| CAGR | +0.95% |
| Win rate | 33.3% |
| Profit factor | 1.77 |
| Expectancy (R) | +0.599 |
| Max drawdown | 4.50% |
| Stop-out rate | 100.0% |
| Long / short trades | 24 / 0 |

## Validation (never part of the gate)

| Check | Value |
|---|---|
| Holdout trades | 6 |
| Holdout profit factor | 2.67 |
| Holdout net P&L (₹) | 33,765 |
| Fold-consistency | 3/5 folds positive |
| Pooled fold profit factor | 1.80 |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame
spec = StrategySpec(family='BREAKOUT', index_key='SENSEX', params={'lookback': 20, 'stop_atr': 2.0, 'trail_atr': 6.0, 'time_stop': 400, 'regime': 200, 'slope_len': 20, 'confirm': 0.55, 'exit_regime': 1, 'direction_mode': 'long'})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
print(validate(df, spec))
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-BREAKOUT-SEAE56` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps per leg and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._