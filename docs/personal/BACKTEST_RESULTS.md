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