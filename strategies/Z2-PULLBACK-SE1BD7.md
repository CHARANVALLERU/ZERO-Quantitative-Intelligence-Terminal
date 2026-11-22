# Z2-PULLBACK-SE1BD7 — PULLBACK on SENSEX

**Gate:** NOT PASSED  
**Validation:** NOT VALIDATED  
**Generated:** 2026-11-22T03:28:04  
**Engine:** ZERO v2.1 Strategy Lab

## Why this edge should exist

A breakout is only tradeable while price is still near the short mean. Once the move is extended, the same breakout is the crowd's entry. Requiring the close to sit within 0.01 of the 20-day mean keeps the entry on the first thrust out of a pullback and skips the chase. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.

## Rules (plain language)

- **Entry:** Buy the next open when the close exceeds the prior 10-day high and is no more than 0.01 above the 20-day mean. The regime gate requires price above a rising 200-day mean (shorts: below a falling one), measured over 20 bars, and the signal bar must close in the trade's half of its range.
- **Exit:** Trail a 6× ATR chandelier stop; flatten on a close back below the regime mean or after 400 bars.
- **Risk:** Long only: measured shorts lost money on these indices. Initial stop 2.0× ATR, trailed 6× ATR once the trade is in profit; time stop 400 bars; 0.75% of equity risked per trade; 12 bps charged per leg.

## Parameters

| Parameter | Value |
|---|---|
| `confirm` | 0.55 |
| `direction_mode` | long |
| `exit_regime` | 1 |
| `ext` | 0.01 |
| `fast` | 20 |
| `lookback` | 10 |
| `regime` | 200 |
| `slope_len` | 20 |
| `stop_atr` | 2.0 |
| `time_stop` | 400 |
| `trail_atr` | 6.0 |

## Backtest result (full history)

| Metric | Value |
|---|---|
| Trades | 11 |
| Net P&L (₹) | 18,784 |
| Return | +1.88% |
| CAGR | +0.21% |
| Win rate | 45.5% |
| Profit factor | 1.37 |
| Expectancy (R) | +0.355 |
| Max drawdown | 2.59% |
| Stop-out rate | 90.9% |
| Long / short trades | 11 / 0 |

## Validation (never part of the gate)

| Check | Value |
|---|---|
| Holdout trades | 3 |
| Holdout profit factor | 1.04 |
| Holdout net P&L (₹) | 357 |
| Fold-consistency | 3/5 folds positive |
| Pooled fold profit factor | 1.38 |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame
spec = StrategySpec(family='PULLBACK', index_key='SENSEX', params={'lookback': 10, 'fast': 20, 'ext': 0.01, 'stop_atr': 2.0, 'trail_atr': 6.0, 'time_stop': 400, 'regime': 200, 'slope_len': 20, 'confirm': 0.55, 'exit_regime': 1, 'direction_mode': 'long'})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
print(validate(df, spec))
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-PULLBACK-SE1BD7` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps per leg and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._