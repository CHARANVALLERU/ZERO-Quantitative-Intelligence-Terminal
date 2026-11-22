# Z2-MAJOR-SE9E3C — MAJOR on SENSEX

**Gate:** NOT PASSED  
**Validation:** NOT VALIDATED  
**Generated:** 2026-11-22T03:28:03  
**Engine:** ZERO v2.1 Strategy Lab

## Why this edge should exist

The same momentum effect on a position horizon. A 40-55 day high is rarer, so the sample is smaller, but each signal marks a slower, better-established repricing and the trailing exit can hold it for months. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.

## Rules (plain language)

- **Entry:** Buy the next open when the close exceeds the prior 55-day high. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.
- **Exit:** Trail a 6× ATR chandelier stop; flatten on a close back below the regime mean or after 400 bars.
- **Risk:** Long only: measured shorts lost money on these indices. Initial stop 3.0× ATR, trailed 6× ATR once the trade is in profit; time stop 400 bars; 0.75% of equity risked per trade; 12 bps charged per leg.

## Parameters

| Parameter | Value |
|---|---|
| `confirm` | 0.55 |
| `direction_mode` | long |
| `exit_regime` | 1 |
| `lookback` | 55 |
| `regime` | 200 |
| `slope_len` | 20 |
| `stop_atr` | 3.0 |
| `time_stop` | 400 |
| `trail_atr` | 6.0 |

## Backtest result (full history)

| Metric | Value |
|---|---|
| Trades | 17 |
| Net P&L (₹) | 67,670 |
| Return | +6.77% |
| CAGR | +0.73% |
| Win rate | 47.1% |
| Profit factor | 2.49 |
| Expectancy (R) | +0.600 |
| Max drawdown | 2.64% |
| Stop-out rate | 94.1% |
| Long / short trades | 17 / 0 |

## Validation (never part of the gate)

| Check | Value |
|---|---|
| Holdout trades | 4 |
| Holdout profit factor | 12.84 |
| Holdout net P&L (₹) | 18,690 |
| Fold-consistency | 5/5 folds positive |
| Pooled fold profit factor | 2.64 |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame
spec = StrategySpec(family='MAJOR', index_key='SENSEX', params={'lookback': 55, 'stop_atr': 3.0, 'trail_atr': 6.0, 'time_stop': 400, 'regime': 200, 'slope_len': 20, 'confirm': 0.55, 'exit_regime': 1, 'direction_mode': 'long'})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
print(validate(df, spec))
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-MAJOR-SE9E3C` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps per leg and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._