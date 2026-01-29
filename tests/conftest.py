"""Pytest fixtures for PolyLOL tests."""

from __future__ import annotations

import pytest

from src.bot.clock import MockClock, set_clock
from src.bot.settings import (
    EventBusConfig,
    PairArbConfig,
    RiskConfig,
    Settings,
    TruthEngineConfig,
    set_settings,
)
from src.domain.types import PairPosition, Side


@pytest.fixture
def mock_clock():
    """Provide a mock clock for deterministic tests."""
    clock = MockClock()
    set_clock(clock)
    yield clock


@pytest.fixture
def test_settings():
    """Provide test settings."""
    settings = Settings(
        log_level="DEBUG",
        risk=RiskConfig(
            max_daily_loss=100.0,
            max_position_per_market=500.0,
            max_total_exposure=1000.0,
        ),
        pair_arb=PairArbConfig(
            enabled=True,
            pair_cost_cap=0.975,
            fee_rate=0.02,
            step_usdc=25.0,
            max_total_cost=500.0,
        ),
        truth_engine=TruthEngineConfig(
            confirm_threshold=0.90,
            max_wait_ms=10000,
            required_sources_for_final=2,
        ),
        event_bus=EventBusConfig(
            max_queue_size=100,
            overflow_policy="drop",
        ),
    )
    set_settings(settings)
    yield settings


@pytest.fixture
def empty_position():
    """Provide an empty pair position."""
    return PairPosition(
        market_id="test_market_1",
        fee_rate=0.02,
    )


@pytest.fixture
def balanced_position():
    """Provide a balanced position with guaranteed profit."""
    pos = PairPosition(
        market_id="test_market_1",
        fee_rate=0.02,
    )
    # Buy 100 YES at 0.45 = 45 USDC
    pos.q_yes = 100.0
    pos.c_yes = 45.0
    # Buy 100 NO at 0.50 = 50 USDC
    pos.q_no = 100.0
    pos.c_no = 50.0
    # Total cost: 95 USDC
    # Guaranteed payout: 100 * 0.98 = 98 USDC
    # Guaranteed PnL: 98 - 95 = 3 USDC
    return pos


@pytest.fixture
def imbalanced_position():
    """Provide an imbalanced position."""
    pos = PairPosition(
        market_id="test_market_1",
        fee_rate=0.02,
    )
    # Buy 100 YES at 0.45
    pos.q_yes = 100.0
    pos.c_yes = 45.0
    # Only 50 NO at 0.50
    pos.q_no = 50.0
    pos.c_no = 25.0
    return pos
