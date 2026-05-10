"""End-to-end sizing pipeline tests for live MomentumStrategy after B3.

Replaces the strategy-side adjust_position_size soft multiplier (deleted as
part of issue #11; see docs/personal/RISK_MANAGER_SIZING_DESIGN.md) with
pipeline-level coverage: Kelly + enforce_position_size_limit + (when reachable)
gateway gates.

Each test uses synthetic price series with computable statistical properties
so the assertion bounds are derived from generator parameters, not measured
against external market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from engine.backtest_engine import BacktestEngine
from strategies.momentum_strategy import MomentumStrategy


# ---------------------------------------------------------------------------
# Synthetic price-series fixtures with computable expected behavior
# ---------------------------------------------------------------------------


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _build_stable_trending_bars(
    n_days: int = 80, seed: int = 42, start_price: float = 100.0
) -> list[_Bar]:
    """80 bars of mild downtrend then gentle uptrend, low volatility (~1% daily std).

    Reused from tests/unit/test_momentum_strategy_backtest_parity.py with the
    same generator parameters — produces at least one buy signal at price ~$100,
    annualized vol well below RiskManager.volatility_threshold (0.4) so the
    gateway hard gate doesn't reject. Kelly disabled, so sizing is
    `buying_power * 0.05` capped by enforce_position_size_limit at 5%.
    Expected entry quantity for $100k capital at ~$100/share: ~50 shares.
    """
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 2)

    phase1 = start_price - np.linspace(0, 5, 40) + rng.normal(0, 0.3, 40)
    phase2 = phase1[-1] + np.linspace(0.5, 10, 40) + rng.normal(0, 0.3, 40)
    closes = np.concatenate([phase1, phase2])

    volumes = np.full(80, 800_000.0)
    for i in range(40, 80, 4):
        volumes[i] = 2_500_000.0
    volumes = volumes + rng.normal(0, 30_000, 80)

    bars: list[_Bar] = []
    for i in range(len(closes)):
        prev = closes[i - 1] if i > 0 else closes[0]
        c = float(closes[i])
        o = float(prev)
        h = float(max(o, c) + 0.5)
        low = float(min(o, c) - 0.5)
        bars.append(
            _Bar(
                timestamp=start + timedelta(days=i),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=float(volumes[i]),
            )
        )
    return bars[:n_days] if n_days <= len(bars) else bars


class _FakeDataBroker:
    """Stub for AlpacaBroker that yields a deterministic synthetic series."""

    def __init__(self, bars: list[_Bar], *args, **kwargs):
        self._bars = bars

    async def get_bars(self, symbol, start, end, timeframe="1Day"):
        return list(self._bars)


class _FakeHistoricalUniverse:
    def __init__(self, broker=None):
        self.broker = broker

    async def initialize(self):
        return None

    def get_statistics(self):
        return {"total_symbols": 1}

    def get_tradeable_symbols(self, _date, symbols):
        return symbols


# ---------------------------------------------------------------------------
# End-to-end pipeline assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_series_lands_near_5pct_cap(monkeypatch):
    """Stable trending series, Kelly disabled, position_size=0.05 default →
    a $100k account should buy ~50 shares of a $100 stock.

    Pre-B3 this produced ~0.5 shares because adjust_position_size's
    units-mismatched 0.01/risk multiplier shrank by ~100×. Post-B3 the only
    sizing constraints are buying_power × position_size and the
    max_position_size cap, both 0.05.

    Assertion bounds:
      - shares >= 30 (allows up to 40% slippage from the $5000 / $100 = 50
        ideal; tighter bound risks flake from stochastic synthetic data)
      - shares <= 100 (sanity ceiling — anything above means cap isn't
        enforcing or sizing is now over-aggressive)
    """
    bars = _build_stable_trending_bars()

    def _fake_alpaca(*_args, **_kwargs):
        return _FakeDataBroker(bars)

    monkeypatch.setattr("brokers.alpaca_broker.AlpacaBroker", _fake_alpaca)
    monkeypatch.setattr("engine.backtest_engine.HistoricalUniverse", _FakeHistoricalUniverse)

    engine = BacktestEngine()
    result = await engine.run_backtest(
        strategy_class=MomentumStrategy,
        symbols=["AAPL"],
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 4, 30),
        initial_capital=100_000,
        strategy_params={
            "use_multi_timeframe": False,
            "use_volatility_regime": False,
            "use_kelly_criterion": False,
        },
    )

    assert result["total_trades"] > 0, "expected at least one buy signal"
    qty = result["trades"][0]["quantity"]
    price = result["trades"][0]["price"]

    # Expected: $100k × 0.05 / price ≈ 50 shares at $100. Allow generous bounds
    # for slippage modeling and synthetic-series price drift; the upper bound
    # catches accidental sizing-aggression regressions.
    assert qty >= 30, (
        f"expected >=30 shares for $100k account at ~${price:.2f}, "
        f"got {qty} — sizing pipeline is over-shrinking"
    )
    assert qty <= 100, (
        f"expected <=100 shares for $100k account at ~${price:.2f}, "
        f"got {qty} — sizing cap may not be enforcing"
    )

    # Cross-check the dollar magnitude: position should be ~$5000.
    notional = qty * price
    assert 3000 <= notional <= 6000, (
        f"position notional ${notional:.2f} outside expected $3000–$6000 "
        f"window for default 5% sizing on $100k"
    )


@pytest.mark.asyncio
async def test_kelly_disabled_baseline_sizing(monkeypatch):
    """Explicitly verify the Kelly-disabled fixed-sizing path lands at ~5%.

    This is the path used by today's BACKTEST_RESULTS.md baseline. With
    use_kelly_criterion=False and position_size=0.05, the dollar value
    flowing into enforce_position_size_limit is buying_power × 0.05 = $5000.
    The cap (max_position_size=0.05) is non-binding (5% == 5%), so $5000
    flows through unchanged.
    """
    bars = _build_stable_trending_bars()

    def _fake_alpaca(*_args, **_kwargs):
        return _FakeDataBroker(bars)

    monkeypatch.setattr("brokers.alpaca_broker.AlpacaBroker", _fake_alpaca)
    monkeypatch.setattr("engine.backtest_engine.HistoricalUniverse", _FakeHistoricalUniverse)

    engine = BacktestEngine()
    result = await engine.run_backtest(
        strategy_class=MomentumStrategy,
        symbols=["AAPL"],
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 4, 30),
        initial_capital=100_000,
        strategy_params={
            "use_multi_timeframe": False,
            "use_volatility_regime": False,
            "use_kelly_criterion": False,
            "position_size": 0.05,
            "max_position_size": 0.05,
        },
    )

    assert result["total_trades"] > 0
    trade = result["trades"][0]
    notional = trade["quantity"] * trade["price"]

    # 5% of $100k = $5000. Allow for slippage / partial fill / drift.
    assert 4000 <= notional <= 5500, (
        f"Kelly-disabled 5% sizing should land near $5000, got ${notional:.2f}"
    )


# ---------------------------------------------------------------------------
# Unit-level: enforce_position_size_limit cap behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_position_size_limit_caps_at_5pct():
    """Direct test of the cap. Independent of strategy plumbing — verifies
    the load-bearing safety check enforces 5%-of-equity.
    """
    strategy = MomentumStrategy.__new__(MomentumStrategy)
    strategy.broker = AsyncMock()
    strategy.broker.get_account = AsyncMock(
        return_value=SimpleNamespace(equity="100000", buying_power="100000")
    )
    strategy.max_position_size = 0.05
    strategy.logger = __import__("logging").getLogger("test")

    # Request way more than the cap allows
    capped_value, capped_quantity = await strategy.enforce_position_size_limit(
        "AAPL", desired_position_value=20_000.0, current_price=200.0
    )

    # Cap = $100k * 0.05 = $5000 → 25 shares at $200
    assert capped_value == pytest.approx(5_000.0, rel=0.001)
    assert capped_quantity == pytest.approx(25.0, rel=0.001)


@pytest.mark.asyncio
async def test_enforce_position_size_limit_passes_through_when_below_cap():
    """When desired is below the cap, value flows through unchanged."""
    strategy = MomentumStrategy.__new__(MomentumStrategy)
    strategy.broker = AsyncMock()
    strategy.broker.get_account = AsyncMock(
        return_value=SimpleNamespace(equity="100000", buying_power="100000")
    )
    strategy.max_position_size = 0.05
    strategy.logger = __import__("logging").getLogger("test")

    capped_value, capped_quantity = await strategy.enforce_position_size_limit(
        "AAPL", desired_position_value=2_000.0, current_price=200.0
    )

    assert capped_value == pytest.approx(2_000.0, rel=0.001)
    assert capped_quantity == pytest.approx(10.0, rel=0.001)
