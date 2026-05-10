# Backtest Results Log

Append-only log of backtest runs with full reproducibility metadata.

---

## 2026-05-10 — First faithful live MomentumStrategy run

**Result:** +0.01% return, 2 trades, near-zero equity movement
**Status:** Diagnostic — sizing pipeline produced 0.12 and 0.24 share positions on $100k account, suppressing strategy signal expression

**Reproducibility:**
- Git SHA: dda8bae93667a1313e4d857384cb8c130ad502bc
- Command: `uv run python main.py backtest --strategy MomentumStrategy --symbols AAPL,MSFT,AMZN,META,TSLA --start-date 2024-01-01 --end-date 2024-12-31 --capital 100000`
- Log: `results/baseline_runs/momentum_strategy_2024_*.log`
- Date run: 2026-05-10

**Key observations:**
- Bot ran cleanly end-to-end (parity PR #6 + bracket-qty fix working as intended)
- Generated 2 buy signals: MSFT @ $425.22 and AAPL @ $207.49, both as bracket orders
- Position sizes: 0.1176 MSFT, 0.2409 AAPL — far below expected ~50 share positions for $100k account at 5% risk
- RiskManager error fired before AAPL trade: "Error calculating portfolio risk: unsupported operand type(s) for *: 'float' and 'NoneType'"
- Likely fallback to degraded sizing path

**Comparison to MomentumStrategyBacktest (2026-01-18 baseline):**
| Metric | Backtest variant | Live variant (today) |
|---|---|---|
| Total return | +42.68% | +0.01% |
| Trades | 9 | 2 |
| Position sizes | 18-61 shares | 0.12-0.24 shares |
| Short trades | 4 of 9 | 0 (disabled by default in live) |

**Conclusion:** Number is not interpretable as strategy performance — sizing pipeline is broken. Investigate RiskManager NoneType error before treating any subsequent backtest as a real measurement.

**Next:** Fix RiskManager NoneType error, re-run with same command, compare.

---

## 2026-05-10 (post-B3) — First interpretable live MomentumStrategy baseline

**Result:** +1.11% return, 2 trades, sensible position sizing
**Status:** Honest — sizing pipeline working as documented post-PR #19

**Reproducibility:**
- Git SHA: 854944e
- Command: `uv run python main.py backtest --strategy MomentumStrategy --symbols AAPL,MSFT,AMZN,META,TSLA --start-date 2024-01-01 --end-date 2024-12-31 --capital 100000`
- Log: `results/baseline_runs/momentum_strategy_2024_postB3.log`
- Date run: 2026-05-10

**Key observations:**
- Position sizes: 11.7586 MSFT @ $425.22, 22.9176 AAPL @ $207.49 (both ~$5000 notional, 5% of account each)
- 100× and 95× larger than pre-B3 sizes — confirms the unit-mismatch hypothesis from issue #11
- 2 trades, both buys, both held to year-end without TP/SL trigger (TP +5%, SL -3% from entry)
- Sharpe -0.60 is not load-bearing (low N, dominated by entry timing not strategy logic)
- Win rate 0% reflects "no exits triggered," not signal quality
- 0 risk-manager errors in the log (compare pre-B3: 1 None error from issue #18)

**Comparison context:**

| Run | Return | Trades | Position sizes | Comparable? |
|---|---|---|---|---|
| MomentumStrategyBacktest 2024 (2026-01-18) | +42.68% | 9 | 18-61 shares | NO — bypasses risk pipeline (cash × 0.10), see issue #21 |
| MomentumStrategy 2024 pre-B3 (2026-05-10) | +0.01% | 2 | 0.12-0.24 shares | NO — sizing broken, see issue #11 (now closed) |
| MomentumStrategy 2024 post-B3 (2026-05-10) | +1.11% | 2 | 11.76-22.92 shares | First honest baseline |

**Conclusion:** This is internal calibration only. Two trades is too few to draw signal-quality conclusions. The +1.11% reflects price drift on $10k of long-held exposure, not strategy performance. Future Phase 1 work: vary inputs (years, baskets, strategy parameters) to build a real picture of strategy behavior across regimes.

---

## 2026-05-10 — Cross-year regime sweep on tech basket

**Setup:** Same basket (AAPL,MSFT,AMZN,META,TSLA), $100k capital, default MomentumStrategy parameters (post-B3). Varied year only to characterize regime sensitivity.

**Reproducibility:**
- Git SHA: 15fdbd7
- Command pattern: `uv run python main.py backtest --strategy MomentumStrategy --symbols AAPL,MSFT,AMZN,META,TSLA --start-date YYYY-01-01 --end-date YYYY-12-31 --capital 100000`
- Logs: `results/baseline_runs/momentum_strategy_<year>_*.log`

**Results:**

| Year | Regime | Return | Trades | Sharpe | Max DD | Equity Shape |
|---|---|---|---|---|---|---|
| 2020 | COVID crash + recovery | -0.29% | 4 | -0.07 | 16.17% | flat → peak Day 150 ($108.9k) → drawdown to $97.8k → recovery to $99.7k |
| 2021 | Frothy bull | +0.07% | 4 | -0.89 | 1.62% | flat oscillation around $100k all year, late tick to $100.07k |
| 2022 | Bear market | 0.00% | 0 | 0.00 | 0.00% | perfectly flat — strategy held cash all year, no signals fired |
| 2023 | Recovery | +5.68% | 4 | +1.36 | 1.79% | step up to $102.6k by Day 100, gradual climb to $105.7k year-end |
| 2024 | Tech-led bull | +1.11% | 2 | -0.60 | 0.01% | held to year-end (recorded above) |

**First-trade position sizes (B3 sanity check):**
- 2020: 2.0210 shares of AMZN @ $2474.00 ($5000.01 notional)
- 2021: 1.4404 shares of AMZN @ $3471.31 ($4999.94 notional)
- 2022: no trades (no first trade)
- 2023: 32.2581 shares of AAPL @ $155.00 ($5000.00 notional)

First-trade position sizes locked to the $5000 cap in all four years. With Kelly disabled and a binding cap, later trades hold by construction (verified by code, not empirically across all trades in the runs). B3 fix holds across all years measured.

**Errors / warnings observed:**
- **2020:** "Largest gap was 79.9%" warning at end-of-run. Almost certainly TSLA's 5-for-1 split on 2020-08-31 manifesting as a non-split-adjusted 80% price drop in the data feed. Worth investigating data hygiene separately; not a strategy concern. 2020 max drawdown 16.17% reflects the COVID-era position held through the Day 150 → Day 200 drawdown, not the gap event itself.
- **2023:** "Largest gap was 14.6%" warning. Below the >15% flag threshold but worth noting.
- **2021, 2022:** No notable warnings.
- **All four years:** Zero risk_manager errors. The post-B3 fix continues to hold.

**Observations:**
- Trade count is consistent across non-bear years: 4 trades each in 2020/2021/2023, 2 in 2024. This is a low-frequency strategy on a 5-symbol basket; daily-bar momentum entries are rare.
- 2022 produced zero trades. With shorts disabled (default), the strategy correctly sat out the bear market — the basket never produced a buy-side composite score above threshold.
- Returns range from 0.00% to +5.68% across regimes. None of the four years showed losses beyond the small −0.29% in 2020, which itself recovered most of a 9% drawdown from peak.
- 2023 was the only year with a realized exit (MSFT trailing-stop at $343.77). Win rate 25% there reflects "1 of 4 trades exited," not signal quality across 4 closed trades. The other 3 trades were held to year-end like in 2024.
- Position sizing is identical across regimes for the first-trade: $5000 notional, locking to the 5%-of-equity cap. This holds whether the basket is in COVID-crash 2020, frothy-bull 2021, recovery 2023, or quiet-tech-bull 2024.
- 2020 reaching a peak equity of $108.9k mid-year (a +8.9% paper gain) before drawing back to $97.8k illustrates the strategy's lack of trailing-stop sensitivity to large drawdowns — the trailing-stop kicks in at +2% profit but the late-2020 reversal was steep enough that it could not lock in the peak gains. Sharpe of −0.07 reflects the round-trip.

**Conclusion:** Five regimes characterized; one realized exit across all of them. Position sizing locks to the documented $5000 cap in every measured year. Returns span 0.00% to +5.68%, with one small loss (−0.29% in 2020). The strategy is producing low-frequency, low-magnitude results across this basket — not catastrophic in any regime, but also not exhibiting strong signal generation. The 2020 result also surfaces a trailing-stop behavior worth investigating: an 8.9% paper gain was given back to a -2.2% trough, suggesting the 2% trail width may be too tight for the strategy's intended hold period — or that trail mechanics aren't activating as intended. These remain small-N results on one basket; firmer conclusions would require: a different basket (broad index members, sector ETFs, mid-cap names), parameter sensitivity sweeps (RSI thresholds, ADX threshold, position_size), longer windows that span multiple regimes within one run, or a walk-forward validation methodology.