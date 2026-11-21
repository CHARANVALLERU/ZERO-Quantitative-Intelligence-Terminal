# Z2-REV-NI899B — REV on NIFTY

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Short-term panic inside a long-term uptrend is liquidity demand, not information. A 3-period RSI below 15 while above the 100-day mean is a forced seller meeting a passive buyer.

## Rules (plain language)

- **Entry:** Buy the next open when RSI(5) < 25 and close > 50-day mean.
- **Exit:** Exit when RSI(5) > 70, or on the ATR stop / 1.5R target / 15-bar time stop.
- **Risk:** Long only. Stop 1.2× ATR below entry; target 1.5R; time stop 15 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `rsi_buy` | 25 |
| `rsi_len` | 5 |
| `rsi_sell` | 70 |
| `stop_atr` | 1.2 |
| `target_r` | 1.5 |
| `time_stop` | 15 |
| `trend` | 50 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 18 |
| Net P&L (₹) | -25,083 |
| Return | -2.51% |
| CAGR | -1.03% |
| Win rate | 44.4% |
| Profit factor | 0.74 |
| Expectancy (R) | +0.052 |
| Max drawdown | 5.79% |
| Sharpe (per-trade, sample-scaled) | -0.63 |
| Stop-out rate | 55.6% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='REV', index_key='NIFTY', params={'rsi_len': 5, 'rsi_buy': 25, 'rsi_sell': 70, 'trend': 50, 'stop_atr': 1.2, 'target_r': 1.5, 'time_stop': 15})
df = _frame('NIFTY')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-REV-NI899B` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._