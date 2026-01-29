"""Tests for pair arbitrage math."""

from __future__ import annotations

import pytest

from src.domain.types import Fill, PairPosition, Side, should_buy_more


class TestPairPosition:
    """Test PairPosition calculations."""

    def test_empty_position(self):
        """Empty position has zero values."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        assert pos.total_cost() == 0.0
        assert pos.q_min() == 0.0
        assert pos.payout_net() == 0.0
        assert pos.guaranteed_pnl() == 0.0
        assert pos.avg_yes() is None
        assert pos.avg_no() is None
        assert pos.pair_cost_avg() is None

    def test_apply_fill_yes(self):
        """Apply YES fill correctly."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))

        assert pos.q_yes == 100.0
        assert pos.c_yes == 45.0
        assert pos.avg_yes() == 0.45
        assert pos.total_cost() == 45.0

    def test_apply_fill_no(self):
        """Apply NO fill correctly."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        pos.apply_fill(Fill(side=Side.NO, qty=100, price=0.50))

        assert pos.q_no == 100.0
        assert pos.c_no == 50.0
        assert pos.avg_no() == 0.50
        assert pos.total_cost() == 50.0

    def test_balanced_position_guaranteed_pnl(self):
        """Balanced position calculates guaranteed PnL correctly."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        # Buy 100 YES at 0.45 = 45 USDC
        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))
        # Buy 100 NO at 0.50 = 50 USDC
        pos.apply_fill(Fill(side=Side.NO, qty=100, price=0.50))

        # Total cost: 95 USDC
        assert pos.total_cost() == 95.0

        # Guaranteed payout: min(100, 100) * (1 - 0.02) = 98 USDC
        assert pos.payout_net() == 98.0

        # Guaranteed PnL: 98 - 95 = 3 USDC
        assert pos.guaranteed_pnl() == 3.0

        # Pair cost avg: 0.45 + 0.50 = 0.95 (profitable!)
        assert pos.pair_cost_avg() == 0.95

    def test_unprofitable_position(self):
        """Position with pair cost > 0.98 is unprofitable."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        # Buy 100 YES at 0.55 = 55 USDC
        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.55))
        # Buy 100 NO at 0.50 = 50 USDC
        pos.apply_fill(Fill(side=Side.NO, qty=100, price=0.50))

        # Total cost: 105 USDC
        assert pos.total_cost() == 105.0

        # Pair cost avg: 0.55 + 0.50 = 1.05 (unprofitable!)
        assert pos.pair_cost_avg() == 1.05

        # Guaranteed PnL: 98 - 105 = -7 USDC
        assert pos.guaranteed_pnl() == -7.0

    def test_imbalanced_position(self):
        """Imbalanced position uses min quantity for payout."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        # Buy 100 YES at 0.45
        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))
        # Buy only 50 NO at 0.50
        pos.apply_fill(Fill(side=Side.NO, qty=50, price=0.50))

        # q_min = 50
        assert pos.q_min() == 50.0

        # Payout: 50 * 0.98 = 49 USDC
        assert pos.payout_net() == 49.0

        # Total cost: 45 + 25 = 70 USDC
        assert pos.total_cost() == 70.0

        # Guaranteed PnL: 49 - 70 = -21 USDC (loss due to imbalance!)
        assert pos.guaranteed_pnl() == -21.0

    def test_leg_imbalance(self):
        """Leg imbalance calculations."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))
        pos.apply_fill(Fill(side=Side.NO, qty=60, price=0.50))

        assert pos.leg_imbalance_shares() == 40.0  # |100 - 60|
        assert pos.leg_imbalance_usdc() == 15.0  # |45 - 30|

    def test_hypo_buy(self):
        """Hypothetical buy creates correct new position."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))

        # Hypothetically buy 25 USDC of NO at 0.50
        new_pos = pos.hypo_buy(Side.NO, usdc_amount=25.0, price=0.50)

        # Original unchanged
        assert pos.q_no == 0.0

        # New position has NO
        assert new_pos.q_no == 50.0  # 25 / 0.50
        assert new_pos.c_no == 25.0

    def test_copy(self):
        """Copy creates independent position."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.apply_fill(Fill(side=Side.YES, qty=100, price=0.45))

        copy = pos.copy()
        copy.apply_fill(Fill(side=Side.NO, qty=50, price=0.50))

        # Original unchanged
        assert pos.q_no == 0.0
        # Copy has NO
        assert copy.q_no == 50.0


class TestShouldBuyMore:
    """Test the should_buy_more decision function."""

    def test_rejects_zero_amount(self):
        """Rejects zero or negative amount."""
        pos = PairPosition(market_id="test", fee_rate=0.02)

        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.YES,
            usdc_amount=0,
            price=0.45,
            pair_cost_cap=0.975,
            max_total_cost=1000,
        )

        assert not allowed
        assert reason == "zero_amount"

    def test_rejects_exceeds_max_total(self):
        """Rejects if would exceed max total cost."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.c_yes = 900.0
        pos.q_yes = 2000.0

        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.NO,
            usdc_amount=200,
            price=0.45,
            pair_cost_cap=0.975,
            max_total_cost=1000,
        )

        assert not allowed
        assert reason == "exceeds_max_total"

    def test_rejects_pair_cost_exceeds_cap(self):
        """Rejects if pair cost would exceed cap."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        # Position with avg YES = 0.50
        pos.q_yes = 100.0
        pos.c_yes = 50.0

        # Trying to buy NO at 0.49 would give pair_cost = 0.99 > 0.975
        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.NO,
            usdc_amount=49,
            price=0.49,
            pair_cost_cap=0.975,
            max_total_cost=1000,
        )

        assert not allowed
        assert reason == "pair_cost_exceeds_cap"

    def test_rejects_pair_cost_exceeds_net(self):
        """Rejects if pair cost would exceed 1 - fee."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.q_yes = 100.0
        pos.c_yes = 55.0  # avg = 0.55

        # Trying to buy NO at 0.50 would give pair_cost = 1.05 > 0.98
        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.NO,
            usdc_amount=50,
            price=0.50,
            pair_cost_cap=0.99,  # High cap
            max_total_cost=1000,
        )

        assert not allowed
        assert reason == "pair_cost_exceeds_net"

    def test_rejects_leg_imbalance(self):
        """Rejects if would create excessive leg imbalance."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.q_yes = 100.0
        pos.c_yes = 45.0

        # Buying more YES would increase imbalance
        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.YES,
            usdc_amount=100,
            price=0.45,
            pair_cost_cap=0.975,
            max_total_cost=1000,
            max_leg_imbalance_usdc=50.0,
        )

        assert not allowed
        assert reason == "leg_imbalance"

    def test_rejects_no_pnl_improvement(self):
        """Rejects if doesn't improve guaranteed PnL."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        # Already profitable position
        pos.q_yes = 100.0
        pos.c_yes = 45.0
        pos.q_no = 100.0
        pos.c_no = 50.0
        # PnL = 3 USDC

        # Buying at price that doesn't improve
        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.YES,
            usdc_amount=10,
            price=0.95,  # Very high price
            pair_cost_cap=0.975,
            max_total_cost=1000,
            require_improve=True,
        )

        assert not allowed
        assert reason == "no_pnl_improvement"

    def test_approves_good_opportunity(self):
        """Approves valid buying opportunity."""
        pos = PairPosition(market_id="test", fee_rate=0.02)
        pos.q_yes = 100.0
        pos.c_yes = 45.0
        pos.q_no = 50.0
        pos.c_no = 25.0

        # Buying more NO at good price
        allowed, reason = should_buy_more(
            pos=pos,
            side=Side.NO,
            usdc_amount=25,
            price=0.48,
            pair_cost_cap=0.975,
            max_total_cost=1000,
        )

        assert allowed
        assert reason == "approved"
