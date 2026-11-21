# Z2-REV-SEF2BA — REV on SENSEX

**Status:** NOT PASSED  
**Generated:** 2026-11-22T00:25:05  
**Engine:** ZERO v2.0 Strategy Lab  
**Improved from:** —

## Why this edge should exist

Short-term panic inside a long-term uptrend is liquidity demand, not information. A 3-period RSI below 15 while above the 100-day mean is a forced seller meeting a passive buyer.

## Rules (plain language)

- **Entry:** Buy the next open when RSI(3) < 15 and close > 100-day mean.
- **Exit:** Exit when RSI(3) > 65, or on the ATR stop / 1.5R target / 10-bar time stop.
- **Risk:** Long only. Stop 1.5× ATR below entry; target 1.5R; time stop 10 bars; 0.75% of equity risked per trade; 12 bps round-trip cost charged.

## Parameters

| Parameter | Value |
|---|---|
| `rsi_buy` | 15 |
| `rsi_len` | 3 |
| `rsi_sell` | 65 |
| `stop_atr` | 1.5 |
| `target_r` | 1.5 |
| `time_stop` | 10 |
| `trend` | 100 |

## Backtest result

| Metric | Value |
|---|---|
| Trades | 29 |
| Net P&L (₹) | -26,484 |
| Return | -2.65% |
| CAGR | -1.09% |
| Win rate | 51.7% |
| Profit factor | 0.72 |
| Expectancy (R) | +0.067 |
| Max drawdown | 3.46% |
| Sharpe (per-trade, sample-scaled) | -0.76 |
| Stop-out rate | 34.5% |

## How to reproduce

```python
from engine.v2.strategy_lab import StrategySpec, backtest, _frame
spec = StrategySpec(family='REV', index_key='SENSEX', params={'rsi_len': 3, 'rsi_buy': 15, 'rsi_sell': 65, 'trend': 100, 'stop_atr': 1.5, 'target_r': 1.5, 'time_stop': 10})
df = _frame('SENSEX')
print(backtest(df, spec)['metrics'])
```

## Reuse

Load the spec from `strategies/index.json` under `Z2-REV-SEF2BA` and pass it to
`backtest()` or the v2.0 panel. The id is stable for the same parameter set.

---
_Backtests are simulations. Costs are approximated at 12 bps round trip and do not model gap slippage beyond the next-open fill. Past performance is not a forecast._