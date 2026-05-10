# RiskManager Sizing — Design Investigation (Bug 2)

**Status:** Plan-only, awaiting review. No code changes.
**Author:** Claude Code (under Achyuth's direction)
**Date:** 2026-05-10
**Scope:** Issue #11 — `MomentumStrategy._apply_risk_adjustments` produces sub-1 quantities for $100k accounts. Today's baseline (PR #18 context): MSFT 0.118 shares @ $425, AAPL 0.241 shares @ $207.
**Related:** PR #18 (Fix A — None tolerance, correctness only). This document drives the still-open structural fix.

> The minimal-fix PR (#18) demonstrably does NOT change position sizes. The cause is in a different code path that this document analyzes and recommends the fix for.

---

## 1. What is `adjust_position_size` actually doing?

### The pipeline today (live `MomentumStrategy`)

`MomentumStrategy._execute_buy_signal` → three sequential transformations on dollar-position-value:

| Step | Method | Where | What it does |
|---|---|---|---|
| 1 | `_calculate_position_value` | `momentum_strategy.py:741` | Kelly fraction OR `buying_power * position_size` (default 0.10). |
| 2 | `_apply_risk_adjustments` | `momentum_strategy.py:771` | Calls `RiskManager.adjust_position_size`. |
| 3 | `enforce_position_size_limit` | `base_strategy.py:258` | Caps at `account.equity * max_position_size` (default 0.05). |

Then the order goes through OrderGateway, which calls `_check_risk_limits` → `RiskManager.enforce_limits` for hard-gate violations (VaR/ES/vol/correlation/drawdown/portfolio_risk thresholds). If any threshold is breached, the order is rejected outright.

So there are already **two** risk gates after step 1: a soft multiplier (step 2) and a hard gate (gateway). Step 2 is what's broken.

### What `adjust_position_size` claims to do

`risk_manager.py:545–628`. Five concerns, applied as multiplicative shrinks:

1. **Position risk shrink** — `if risk > max_position_risk: scale = max_position_risk / risk`.
2. **Correlation shrink** (soft mode) or **rejection** (strict mode, default).
3. **Portfolio risk shrink** — `if portfolio_risk > max_portfolio_risk: scale = max_portfolio_risk / portfolio_risk`.
4. (none of the above is a "VaR shrink" or "vol shrink" — those are absent here, present only in `enforce_limits`)
5. Returns `desired_size * min(risk_adjustment, correlation_adjustment, portfolio_adjustment)`.

### Why concern (1) is broken

The two operands compared have different units:

| Symbol | Definition | Typical value |
|---|---|---|
| `calculate_position_risk()` return | Composite score: `0.3 · vol/vol_threshold + 0.3 · |VaR|/var_threshold + 0.2 · |ES|/es_threshold + 0.2 · |DD|/dd_threshold`, capped at 1.0 | 0.3–1.0; **frequently 1.0** because each term saturates. |
| `max_position_risk` | Configured as fraction-of-portfolio risk budget per position | 0.01 (1%) default |

Comparing `risk > max_position_risk` is `0.3..1.0 > 0.01` → **virtually always true**. The shrink factor `0.01 / risk` lands in `[0.01, 0.033]` → **30–100× shrink** on every order.

Today's baseline confirms it numerically: $100k × 5% (the cap) / $207 ≈ 50 shares; observed 0.241 shares = $50, which is exactly $5000 × 0.01. The 0.01 = `max_position_risk / risk` (`risk = 1.0` for AAPL on the synthetic prices).

### Why concern (3) is broken

Same units mismatch. `calculate_portfolio_risk` sums weighted-composite-scores. `max_portfolio_risk = 0.02` is meant as fraction-of-portfolio. They don't share units. The check `portfolio_risk > max_portfolio_risk` would fire on virtually any non-empty portfolio (`1 × 1.0 = 1.0 > 0.02`) — except in our specific scenario it doesn't actually shrink positions because:
- For the FIRST trade, `current_positions = {}` → calculate_portfolio_risk returns 0 → no violation.
- For SUBSEQUENT trades, the bug-1 None-coercion path raised TypeError → fallback returned `max_portfolio_risk` itself → `0.02 > 0.02` is False (strict comparison) → no shrink.

So in the current configuration concern (3) is effectively a no-op due to a chain of unrelated bugs canceling out. Brittle.

### Why concern (2) is fine (but duplicated)

The correlation rejection is a hard reject (`return 0`) at high correlation. Sound logic, but **identical** to the correlation check in `enforce_limits` (lines 704–726), which fires at the gateway. **The strategy-side check is redundant.**

### Verdict on Q1

> Real work or redundant scaffolding?

**Redundant scaffolding from an older sizing model.**

- The position-risk shrink (concern 1) is structurally broken — units mismatch. It does not produce risk-aware sizing; it produces uniformly tiny sizes.
- The correlation rejection (concern 2) is duplicated in `enforce_limits` at the gateway.
- The portfolio-risk shrink (concern 3) is structurally broken — same units mismatch. Currently a no-op only by accident (bug interactions cancel).
- The "real" risk-aware sizing in the codebase comes from **Kelly Criterion** (when enabled, scales position by historical win-rate × profit-factor) and **`enforce_position_size_limit`** (5%-of-equity hard cap), which are mathematically well-grounded and unit-coherent.

`adjust_position_size` was likely written before `enforce_limits` and `OrderGateway._check_risk_limits` were added, when the risk manager was the only enforcer. It's now duplicated and broken. Removing it doesn't strip a risk control — it strips a buggy duplicate.

---

## 2. Does `MomentumStrategyBacktest` bypass `adjust_position_size`?

**Yes, completely.** `strategies/momentum_strategy_backtest.py:117–118`:

```python
# Calculate position size (10% of cash)
position_value = cash * 0.10
qty = int(position_value / price)
```

Then at line 147–168, `_place_backtest_order` builds an `OrderBuilder(...).market().day().build()` — no Kelly, no `_apply_risk_adjustments`, no `enforce_position_size_limit`, no `RiskManager.adjust_position_size`. Just `int(0.10 × cash / price)`.

This is a fundamentally different sizing pipeline from live `MomentumStrategy`. The +42.68% / +21.26% baselines from `MomentumStrategyBacktest` are NOT comparable to any live-`MomentumStrategy` backtest number, because:

| | `MomentumStrategyBacktest` | Live `MomentumStrategy` |
|---|---|---|
| Sizing rule | `int(cash × 0.10 / price)` | Kelly OR `buying_power × 0.10` |
| Strategy-side risk multiplier | none | `RiskManager.adjust_position_size` (currently 30–100× shrink) |
| Portfolio-cap | none (only cash check) | `enforce_position_size_limit` (5%-of-equity) |
| Gateway risk gates | passes through (`submit_entry_order`) | passes through (`submit_entry_order`) |

Even if Bug 2 didn't exist, the two strategies would differ on `int(...)` vs fractional, on cash-vs-equity basis, and on the 5% cap. With Bug 2, the divergence is dramatic.

**Action item for `BACKTEST_RESULTS.md`:** flag the +42.68% / +21.26% rows with a "sizing-bypass" note. Future live-`MomentumStrategy` backtests should be recorded as a separate baseline series, not compared to the bypass numbers.

(Also: the `int()` truncation in `_place_backtest_order:150` is issue #13. Independently broken, also bypassing.)

---

## 3. B1 vs B2 vs B3 — recommendation

**B3: delete the strategy-side multiplier in `adjust_position_size`.** Specifically: keep `RiskManager` as a class for `calculate_position_risk`, `calculate_portfolio_risk`, `enforce_limits`, and the underlying VaR/ES/vol/correlation/drawdown helpers. **Remove `adjust_position_size` entirely** and remove `_apply_risk_adjustments` from each strategy's pipeline.

### Why B3 over B1 (rescale risk metric)

B1 would re-define `calculate_position_risk` to return a fraction-of-capital value (e.g., `volatility * sqrt(holding_period_days/252)` ≈ 0.005–0.03). This is plausible mathematically but:
- It changes the meaning of a public method that other code reads (`enforce_limits` line 731; `OrderGateway._check_risk_limits` fallback line 713). Cascading audits required across at least 3 callers.
- The "right" formula depends on holding-period assumption, which is policy not math. Encoding a holding-period in `calculate_position_risk` couples it to a specific strategy's intent. Wrong abstraction layer.
- Even after rescaling, the multiplier `desired_size * min(adjustments)` remains a soft scaler living next to a hard gate (`enforce_limits`). Two layers doing the same job, with the soft layer producing arbitrary intermediate values that depend on threshold tuning. **High ongoing maintenance burden** — every threshold change requires re-validating that the soft multiplier doesn't produce nonsense.

### Why B3 over B2 (reinterpret threshold)

B2 would change the *meaning* of `max_position_risk` from "fraction-of-portfolio budget" to "max composite risk score allowed". Default would jump from `0.01` to e.g. `0.5` or `0.7`.
- All call sites need re-validation. Strategies all hard-code `max_portfolio_risk: 0.02` and `max_position_risk: 0.01` per the grep — touching the meaning of these requires editing several strategy default-parameters dicts and reasoning about each.
- The semantic "max acceptable composite risk score" is fuzzy. A score of 0.7 means "this stock is in the 70th percentile of bad on a weighted average of vol/VaR/ES/DD" — it doesn't have a clean operational meaning ("at most 1% of portfolio" does).
- Same maintenance trap as B1: tuning thresholds becomes a coupled multi-knob problem, and the multiplier still shadows the gateway hard gate.

### Why B3 wins

B3 acknowledges what the codebase has actually grown into:

- **Kelly Criterion** (when active) is the principled fractional sizing tool — it consumes win-rate and profit-factor and outputs a fraction. Risk-aware by construction.
- **`enforce_position_size_limit`** is the principled hard cap — fraction-of-equity, single-knob, intuitive operational meaning ("never let one position exceed 5% of equity").
- **`RiskManager.enforce_limits`** is the principled hard gate — reject if VaR/ES/vol/correlation/drawdown thresholds exceeded. Each comparison is unit-coherent (vol vs vol_threshold, both annualized; VaR vs var_threshold, both daily fractions). Sound.
- The soft multiplier in `adjust_position_size` is the only piece in this set that isn't unit-coherent and that doesn't have a single principled job. Deleting it removes complexity without removing capability.

After B3, the live-`MomentumStrategy` pipeline is:

1. `_calculate_position_value` — Kelly OR `buying_power × position_size`.
2. ~~`_apply_risk_adjustments`~~ — **removed.**
3. `enforce_position_size_limit` — 5%-of-equity cap.
4. Order goes to gateway. `_check_risk_limits` → `enforce_limits` → reject on hard threshold breaches.

Three layers, each doing one well-defined job, each unit-coherent. No multiplier interplay.

### Edge case: what about correlation shrinkage?

In current code, `adjust_position_size` rejects (strict mode) on correlation > 0.7. After B3 removes that block, the equivalent check in `enforce_limits` (line 718) takes over and triggers `correlation_exceeded`, which the gateway converts to a rejection. **Same outcome, no regression.** Already verified the codepaths.

### What about the broken portfolio_risk check in `enforce_limits`?

Important caveat that B3 alone doesn't address: `enforce_limits:737` calls the same `calculate_portfolio_risk` and compares `portfolio_risk > max_portfolio_risk`, which has the same units mismatch. With single-position portfolios it would trigger `portfolio_risk_exceeded` virtually always.

Why doesn't it currently fire in production? Two reasons that should not be relied on:
1. In today's MSFT-then-AAPL run, the gateway path was reached, but `_check_risk_limits` fired through `enforce_limits`, which has its own try/except — and the test_positions construction at line 730–736 stores `risk = calculate_position_risk(...)` (which returns ~1.0) only for the symbol being checked, while existing positions (held MSFT) have `"risk": None` from `_build_current_positions_dict`. The same None bug from PR #18 likely fires in this path too, returning `0.0` (post-fix) or `max_portfolio_risk` (pre-fix), making the check a no-op.
2. With `current_positions = {}` (single new position), the portfolio_risk math actually runs cleanly, returning ~1.0, which **should** trigger violation. Need to verify why it doesn't in practice.

**Recommended scope for B3's PR:** also delete the portfolio-risk gate from `enforce_limits` (keep VaR/ES/vol/correlation/drawdown — those are unit-coherent). The broken portfolio-risk check is a latent landmine that today happens to be defused by bug-2's None-cancellation; we should remove the landmine, not the cancellation.

### Counter-argument considered: "the multiplier was someone's intent — don't delete intent"

The intent (combine multiple risk dimensions into a single sizing scalar) is reasonable in theory. In practice, the implementation:
- Compares incompatible units.
- Duplicates checks already done elsewhere as hard gates.
- Has been silently broken since `f65dfd3` ("feat: add institutional-grade 9/10 features") with no test coverage that would have caught it (tests only assert `0 ≤ adjusted ≤ desired_size`, which 30–100× shrinkage trivially satisfies).

If a future need arises to add a soft-multiplier sizing tier (e.g., "scale down by 50% in high-VIX regimes"), it should be implemented as a single-purpose method in the strategy or in `enforce_position_size_limit`, not as a re-resurrection of the broken multiplicative pipeline.

---

## 4. Test strategy

### The current test gap

`tests/unit/test_risk_manager.py::test_adjust_size_with_no_positions` (line 454) asserts `0 <= adjusted <= desired_size`. **30–100× shrink passes this test.** Every existing test in `TestAdjustPositionSize` has the same shape — they only verify directional behavior (zero on rejection, non-negative output) without pinning any numeric magnitude.

After B3, those tests are deleted with the method.

### What we need to assert post-B3

| Assertion | Goal |
|---|---|
| Live `MomentumStrategy` with $100k capital, default params, single trade for AAPL @ $200 produces a quantity ≥ $4500 / $200 = 22.5 shares (90% of the 5% cap, allowing for slippage modeling). | Lock in that the 5%-of-equity cap is the binding constraint, not a hidden multiplier. |
| Same setup, after MSFT is held, sizing AAPL produces a quantity within ±10% of the empty-portfolio AAPL size. | Holding existing positions doesn't suddenly shrink new entries via portfolio-risk math (regression for both bug-1 and bug-2). |
| Kelly-enabled path (Kelly fraction = 0.05 e.g.) with $100k capital produces 5% × $100k / price shares. | Kelly path is unaffected by RiskManager. |
| Correlated-position rejection still fires via `enforce_limits` at the gateway. | The deleted-from-strategy correlation check is preserved at the gateway. |

### How to test without coupling to live data

Use synthetic fixtures with **known statistical properties** so the assertion bounds are computable, not measured:

- `stable_price_series`: 200 days, log-return mean 0%, std 1% (15.9% annualized). Below `volatility_threshold = 0.4`. VaR-95 ≈ -1.65%, below `var_threshold = 0.03`. No threshold trips. Expected size = full 5% cap.
- `volatile_price_series`: 200 days, std 4% (63% annualized). Above `volatility_threshold`. Expected outcome: hard rejection in `enforce_limits`, quantity = 0.
- `flat_price_series`: 200 days, all $100. Tests divide-by-zero / zero-vol guards.
- `two_correlated_series`: same generator, different seed, correlation > 0.95. Expected: AAPL rejected when MSFT held under strict correlation enforcement.

These fixtures live in `tests/unit/conftest.py` (already structured for this), each producing a `List[float]` of close prices. Tests assert explicit numeric bounds derived from the generator parameters, not measured against external data.

### Integration-level coverage

- Strengthen `tests/unit/test_momentum_strategy_backtest_parity.py::test_momentum_strategy_runs_in_backtest_engine_and_places_orders` (the regression test from PR #6, already strengthened by PR #14) with another tier:
  ```python
  assert first_trade["quantity"] >= 1, (
      f"$100k account producing sub-1 share quantity ({first_trade['quantity']}) — "
      "sizing pipeline is shrinking positions unexpectedly."
  )
  ```
- Add a similar smoke test for `MeanReversionStrategy` and `EnsembleStrategy` if they have corresponding regression tests.

### What NOT to test

Don't test `adjust_position_size` after B3 — it doesn't exist.
Don't test that `calculate_position_risk` returns specific values for specific synthetic series — that's pinning implementation detail.
Don't compare backtest returns against `MomentumStrategyBacktest`'s baselines — they're not comparable (Q2).

---

## 5. Rollout plan for the eventual Bug 2 PR

### Phase 0 — alignment (this document)
Achyuth reviews this doc. Decision: B3 confirmed, B1 chosen, or B2 chosen. If B3, proceed.

### Phase 1 — analysis (already mostly done)
This doc is the artifact. PR's Phase 1 report can summarize and link.

### Phase 2 — implementation
- Branch: `fix/risk-manager-remove-broken-sizing-multiplier`
- Diff (proposed):
  - `strategies/risk_manager.py` — delete `adjust_position_size` (lines 545–628). Delete `calculate_portfolio_risk` portion of `enforce_limits` (lines 728–746). Keep `_position_risk` helper from PR #18 only if still used after deletion (likely not — recheck).
  - `strategies/momentum_strategy.py` — delete `_apply_risk_adjustments` method and its call site at line 791.
  - `strategies/mean_reversion_strategy.py` — delete the `adjust_position_size` calls at lines 394, 483.
  - `strategies/ensemble_strategy.py` — delete the call at line 523.
  - `tests/unit/test_risk_manager.py` — delete `TestAdjustPositionSize` class (lines 451–524). Update `TestCalculatePortfolioRisk` if `calculate_portfolio_risk` is also deleted.
  - `tests/unit/test_momentum_strategy_backtest_parity.py` — strengthen `quantity >= 1` assertion as above.
  - New: `tests/unit/test_risk_manager.py::TestSizingPostMultiplierRemoval` with the four assertions from §4.
- Run full suite. Same SHA-based pre-existing-failure verification as PRs #14 and #18.
- Re-run the baseline backtest: `uv run python main.py backtest --strategy MomentumStrategy --symbols AAPL,MSFT,AMZN,META,TSLA --start-date 2024-01-01 --end-date 2024-12-31 --capital 100000`. Compare to `BACKTEST_RESULTS.md`'s current entry (2 trades, +0.01% return, sub-1 sizes).

Expected new behavior:
- Position sizes near the 5%-of-equity cap (~$5000 each at default params).
- More trades (the cooldown gate may release more often when actual capital is being deployed; need to verify).
- Materially different return number — could be positive or negative, but should NOT be sub-1-share noise.
- Still no `RiskManager` errors in the log.

### Phase 3 — PR
Standard 3-phase. Title: "fix: remove broken sizing multiplier; rely on Kelly + cap + gateway gate". Body includes:
- Diagnosis from this doc (link).
- Before/after baseline backtest numbers (this is what makes this a structural change worth merging — the run with B3 applied IS the BACKTEST_RESULTS.md "fixed" entry).
- "Closes #11."
- Risk-critical files section: `strategies/risk_manager.py`, all three strategy files.
- Explicit call-out that `MomentumStrategyBacktest` still bypasses (issue #13 remains open) — do not assume it benefits from this change.
- Test results, pre-existing-failure verification, no auto-merge.

### Coupling to PR #18
PR #18 (None tolerance) is independent of this. It can merge first or not — the structural fix doesn't depend on it. After B3 merges, PR #18's `_position_risk` helper may become unused (if `calculate_portfolio_risk` is deleted along with the multiplier). That's a clean follow-up; no rebase risk.

---

## Open questions for Achyuth before scoping

1. **Confirm B3.** Should the structural PR delete `adjust_position_size` outright, or leave the method in place but make it a pass-through (`return desired_size`) for some interim period?
2. **Scope of `enforce_limits` cleanup.** Should the same PR also remove the broken portfolio-risk gate from `enforce_limits`, or split that into a follow-up to keep the diff minimal?
3. **`calculate_portfolio_risk`'s fate.** After step (2) decides whether `enforce_limits` keeps using it, this method might have no remaining callers. Delete or keep dormant?
4. **`MomentumStrategyBacktest` future.** Per `AI_CONTEXT.md`'s lessons-learned: this class exists primarily as the workaround for the empty `execute_trade` in the live class, which PR #6 fixed. Now that bypass-vs-pipeline divergence is documented (Q2 above), is it time to delete `MomentumStrategyBacktest` and reduce parameter overrides into `MomentumStrategy.default_parameters` via a flag? Separate question, but related — and a clean opportunity now that the old workaround's reason is gone.
5. **Baseline numerical expectations.** With B3 applied, what return/Sharpe range do you consider "looks reasonable, run for longer"? This affects how we frame the post-fix `BACKTEST_RESULTS.md` entry.

---

## Appendix — call sites of `adjust_position_size`

For the eventual diff:

```
strategies/risk_manager.py:545          definition (delete)
strategies/momentum_strategy.py:777     call (delete via removing _apply_risk_adjustments at 771–779)
strategies/mean_reversion_strategy.py:394  call (delete)
strategies/mean_reversion_strategy.py:483  call (delete)
strategies/ensemble_strategy.py:523     call (delete)
tests/unit/test_risk_manager.py         entire TestAdjustPositionSize class (lines 451–524)
```

No other production callers. No external API surface.
