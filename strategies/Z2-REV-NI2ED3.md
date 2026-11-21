# Z2-REV-NI2ED3 — REV on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Short-term panic inside a long-term uptrend is liquidity demand, not information. A 3-period RSI below 15 while above the 100-day mean is a forced seller meeting a passive buyer.

## Rules (plain language)

- **Entry:** Buy the next open when RSI(2) < 10 and close > 100-day mean.
- **Exit:** Exit when RSI(2) > 60, or on the ATR stop / 2R target / 8-bar time stop.
- **Risk:** Long only. Stop 2.0× ATR below entry; target 2.0R; time stop 8 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `rsi_buy` | 10 |
| `rsi_len` | 2 |
| `rsi_sell` | 60 |
| `stop_atr` | 2.0 |
| `target_r` | 2.0 |
| `time_stop` | 8 |
| `trend` | 100 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 33 |
| Net P&L (₹) | -86,383 |
| Return | -8.64% |
| CAGR | -3.60% |
| Win rate | 36.4% |
| Profit factor | 0.30 |
| Expectancy (R) | -0.221 |
| Max drawdown | 8.92% |
| Sharpe (per-trade, sample-scaled) | -2.80 |
| Stop-out rate | 36.4% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='REV', index_key='NIFTY', params={'rsi_len': 2, 'rsi_buy': 10, 'rsi_sell': 60, 'trend': 100, 'stop_atr': 2.0, 'target_r': 2.0, 'time_stop': 8})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-REV-NI2ED3` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._