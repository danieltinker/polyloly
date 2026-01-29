"""
Trading Engine - Per-market execution state machine.

Controls what actions are allowed for each market based on current state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.bot.logging import get_component_logger
from src.domain.types import (
    CancelIntent,
    Order,
    OrderBook,
    OrderIntent,
    OrderStatus,
    PairPosition,
    Side,
    TradingState,
    TradingStatus,
    should_buy_more,
)

logger = get_component_logger("trading_engine")


@dataclass
class TradingEngineConfig:
    """Configuration for the trading engine."""

    # Timeouts
    idle_after_no_opportunity_ticks: int = 100
    temporal_signal_ttl_ms: int = 5000
    settlement_timeout_ms: int = 60000

    # Recovery
    auto_recover_from_halt: bool = False
    halt_recovery_delay_ms: int = 300000

    # Pair arb params
    pair_cost_cap: float = 0.975
    fee_rate: float = 0.02
    step_usdc: float = 25.0
    max_total_cost: float = 1500.0
    max_leg_imbalance_usdc: float = 100.0

    # Risk
    max_consecutive_rejects: int = 3
    max_cancel_failures: int = 3


class TradingEngine:
    """
    Per-market trading state machine.
    
    States:
        IDLE: Watching, no active strategy
        BUILDING_PAIR: Accumulating YES/NO pair
        LOCKED_PAIR: Guaranteed profit locked
        TEMPORAL_ACTIVE: Acting on temporal signal
        FINALIZING: Match ending, stopping activity
        RESOLVED: Market settled
        HALT: Trading suspended
        
    Allowed Actions by State:
        IDLE: [watch]
        BUILDING_PAIR: [buy_yes, buy_no, cancel]
        LOCKED_PAIR: [watch]
        TEMPORAL_ACTIVE: [buy_winner, cancel]
        FINALIZING: [cancel_all]
        RESOLVED: []
        HALT: [cancel_all]
    """

    def __init__(
        self,
        market_id: str,
        config: TradingEngineConfig,
        position: Optional[PairPosition] = None,
    ):
        self._config = config
        self._state = TradingState(
            market_id=market_id,
            position=position or PairPosition(market_id=market_id, fee_rate=config.fee_rate),
        )
        self._no_opportunity_ticks = 0
        self._temporal_signal_at_ms: Optional[int] = None

    @property
    def market_id(self) -> str:
        """Market ID."""
        return self._state.market_id

    @property
    def status(self) -> TradingStatus:
        """Current trading status."""
        return self._state.status

    @property
    def position(self) -> PairPosition:
        """Current position."""
        return self._state.position

    @property
    def can_place_orders(self) -> bool:
        """Can we place new orders?"""
        return self._state.status in (
            TradingStatus.BUILDING_PAIR,
            TradingStatus.TEMPORAL_ACTIVE,
        )

    @property
    def is_active(self) -> bool:
        """Is the engine actively trading?"""
        return self._state.status not in (
            TradingStatus.RESOLVED,
            TradingStatus.HALT,
        )

    def get_allowed_actions(self) -> set[str]:
        """Get allowed actions in current state."""
        match self._state.status:
            case TradingStatus.IDLE:
                return {"watch"}
            case TradingStatus.BUILDING_PAIR:
                return {"buy_yes", "buy_no", "cancel"}
            case TradingStatus.LOCKED_PAIR:
                return {"watch"}
            case TradingStatus.TEMPORAL_ACTIVE:
                return {"buy_winner", "cancel"}
            case TradingStatus.FINALIZING:
                return {"cancel_all"}
            case TradingStatus.RESOLVED:
                return set()
            case TradingStatus.HALT:
                return {"cancel_all"}

    # =========================================================================
    # State Transitions
    # =========================================================================

    def _transition_to(self, new_status: TradingStatus, reason: str = "") -> None:
        """Transition to a new state."""
        old_status = self._state.status
        self._state.status = new_status
        self._state.entered_state_at = datetime.now(timezone.utc)
        self._state.last_activity_at = datetime.now(timezone.utc)

        logger.info(
            "State transition",
            event_type="trading_state_transition",
            market_id=self._state.market_id,
            from_status=old_status.value,
            to_status=new_status.value,
            reason=reason,
        )

    def halt(self, reason: str) -> list[CancelIntent]:
        """Halt trading for this market."""
        if self._state.status == TradingStatus.HALT:
            return []

        self._transition_to(TradingStatus.HALT, reason)
        return self._cancel_all_orders()

    def resume_from_halt(self) -> bool:
        """Resume from HALT state."""
        if self._state.status != TradingStatus.HALT:
            return False

        # Reset circuit breaker counters
        self._state.consecutive_rejects = 0
        self._state.consecutive_cancel_failures = 0

        self._transition_to(TradingStatus.IDLE, "manual_resume")
        return True

    def finalize(self) -> list[CancelIntent]:
        """Enter FINALIZING state (match ending)."""
        if self._state.status in (TradingStatus.RESOLVED, TradingStatus.HALT):
            return []

        self._transition_to(TradingStatus.FINALIZING, "match_ending")
        return self._cancel_all_orders()

    def resolve(self) -> None:
        """Enter RESOLVED state (market settled)."""
        self._transition_to(TradingStatus.RESOLVED, "market_settled")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def on_orderbook_update(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[OrderIntent]:
        """
        Process orderbook update and potentially emit order intent.
        
        Returns:
            OrderIntent if we should place an order, None otherwise
        """
        self._state.last_activity_at = datetime.now(timezone.utc)

        # Check for risk triggers
        if self._check_circuit_breaker():
            return None

        match self._state.status:
            case TradingStatus.IDLE:
                return self._handle_idle_orderbook(orderbook_yes, orderbook_no)

            case TradingStatus.BUILDING_PAIR:
                return self._handle_building_orderbook(orderbook_yes, orderbook_no)

            case TradingStatus.TEMPORAL_ACTIVE:
                return self._handle_temporal_orderbook(orderbook_yes, orderbook_no)

            case _:
                return None

    def on_fill(
        self,
        side: Side,
        qty: float,
        price: float,
        order_id: str,
    ) -> Optional[OrderIntent]:
        """
        Process a fill notification.
        
        Returns:
            OrderIntent if we should rebalance, None otherwise
        """
        from src.domain.types import Fill

        # Apply fill to position
        self._state.position.apply_fill(Fill(side=side, qty=qty, price=price))
        self._state.last_activity_at = datetime.now(timezone.utc)

        # Remove from open orders
        if order_id in self._state.open_orders:
            del self._state.open_orders[order_id]

        logger.info(
            "Fill applied",
            event_type="fill_applied",
            market_id=self._state.market_id,
            side=side.value,
            qty=qty,
            price=price,
            guaranteed_pnl=self._state.position.guaranteed_pnl(),
        )

        # Check for state transitions
        match self._state.status:
            case TradingStatus.BUILDING_PAIR:
                return self._handle_building_fill()

            case TradingStatus.TEMPORAL_ACTIVE:
                return self._handle_temporal_fill()

            case _:
                return None

    def on_order_rejected(self, order_id: str, reason: str) -> None:
        """Handle order rejection."""
        self._state.consecutive_rejects += 1
        self._state.last_activity_at = datetime.now(timezone.utc)

        if order_id in self._state.open_orders:
            self._state.open_orders[order_id].status = OrderStatus.REJECTED
            self._state.open_orders[order_id].reject_reason = reason

        logger.warning(
            "Order rejected",
            event_type="order_rejected",
            market_id=self._state.market_id,
            order_id=order_id,
            reason=reason,
            consecutive_rejects=self._state.consecutive_rejects,
        )

        self._check_circuit_breaker()

    def on_order_success(self, order_id: str) -> None:
        """Handle successful order placement."""
        self._state.consecutive_rejects = 0
        self._state.last_activity_at = datetime.now(timezone.utc)

        if order_id in self._state.open_orders:
            self._state.open_orders[order_id].status = OrderStatus.PLACED

    def on_cancel_failure(self, order_id: str) -> None:
        """Handle cancel failure."""
        self._state.consecutive_cancel_failures += 1

        logger.warning(
            "Cancel failed",
            event_type="cancel_failed",
            market_id=self._state.market_id,
            order_id=order_id,
            consecutive_failures=self._state.consecutive_cancel_failures,
        )

        self._check_circuit_breaker()

    def on_cancel_success(self, order_id: str) -> None:
        """Handle successful cancel."""
        self._state.consecutive_cancel_failures = 0

        if order_id in self._state.open_orders:
            self._state.open_orders[order_id].status = OrderStatus.CANCELLED
            del self._state.open_orders[order_id]

    def on_tick(self, now_ms: int) -> list[CancelIntent]:
        """
        Periodic tick for timeout checks.
        
        Returns:
            List of cancel intents if timeouts triggered
        """
        intents: list[CancelIntent] = []

        match self._state.status:
            case TradingStatus.BUILDING_PAIR:
                # Check for no-opportunity timeout
                self._no_opportunity_ticks += 1
                if self._no_opportunity_ticks >= self._config.idle_after_no_opportunity_ticks:
                    self._transition_to(TradingStatus.IDLE, "no_opportunity_timeout")
                    self._no_opportunity_ticks = 0

            case TradingStatus.TEMPORAL_ACTIVE:
                # Check for signal expiry
                if self._temporal_signal_at_ms:
                    elapsed = now_ms - self._temporal_signal_at_ms
                    if elapsed >= self._config.temporal_signal_ttl_ms:
                        logger.info(
                            "Temporal signal expired",
                            event_type="temporal_signal_expired",
                            market_id=self._state.market_id,
                            elapsed_ms=elapsed,
                        )
                        intents.extend(self._cancel_all_orders())
                        self._transition_to(TradingStatus.IDLE, "signal_expired")
                        self._temporal_signal_at_ms = None

        return intents

    # =========================================================================
    # State-Specific Handlers
    # =========================================================================

    def _handle_idle_orderbook(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[OrderIntent]:
        """Handle orderbook in IDLE state."""
        # Check for pair arb opportunity
        intent = self._evaluate_pair_arb_opportunity(orderbook_yes, orderbook_no)
        if intent:
            self._transition_to(TradingStatus.BUILDING_PAIR, "pair_arb_opportunity")
            self._no_opportunity_ticks = 0
            return intent

        return None

    def _handle_building_orderbook(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[OrderIntent]:
        """Handle orderbook in BUILDING_PAIR state."""
        intent = self._evaluate_pair_arb_opportunity(orderbook_yes, orderbook_no)
        if intent:
            self._no_opportunity_ticks = 0
            return intent

        # No opportunity this tick
        self._no_opportunity_ticks += 1
        return None

    def _handle_building_fill(self) -> Optional[OrderIntent]:
        """Handle fill in BUILDING_PAIR state."""
        # Check if we've locked profit
        if self._state.position.guaranteed_pnl() > 0:
            self._transition_to(TradingStatus.LOCKED_PAIR, "profit_locked")
            return None

        # Check for leg imbalance that needs rebalancing
        imbalance = self._state.position.leg_imbalance_usdc()
        if imbalance > self._config.max_leg_imbalance_usdc:
            # Determine lagging leg
            if self._state.position.c_yes > self._state.position.c_no:
                side = Side.NO
            else:
                side = Side.YES

            logger.info(
                "Rebalancing needed",
                event_type="rebalance_needed",
                market_id=self._state.market_id,
                imbalance_usdc=imbalance,
                side=side.value,
            )
            # Rebalance intent would be generated on next orderbook update

        return None

    def _handle_temporal_orderbook(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[OrderIntent]:
        """Handle orderbook in TEMPORAL_ACTIVE state."""
        # Temporal strategy would evaluate continued opportunity here
        # For now, we just wait for fill or timeout
        return None

    def _handle_temporal_fill(self) -> Optional[OrderIntent]:
        """Handle fill in TEMPORAL_ACTIVE state."""
        # Check if position now qualifies for locked pair
        if self._state.position.guaranteed_pnl() > 0:
            self._transition_to(TradingStatus.LOCKED_PAIR, "temporal_to_locked")
        else:
            self._transition_to(TradingStatus.IDLE, "temporal_filled")

        self._temporal_signal_at_ms = None
        return None

    # =========================================================================
    # Strategy Logic
    # =========================================================================

    def _evaluate_pair_arb_opportunity(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[OrderIntent]:
        """Evaluate if there's a pair arb opportunity."""
        # Select which leg to buy
        side = self._select_leg_to_buy(orderbook_yes, orderbook_no)
        if side is None:
            return None

        orderbook = orderbook_yes if side == Side.YES else orderbook_no
        price = orderbook.best_ask
        if price is None:
            return None

        # Check if we should buy
        allowed, reason = should_buy_more(
            pos=self._state.position,
            side=side,
            usdc_amount=self._config.step_usdc,
            price=price,
            pair_cost_cap=self._config.pair_cost_cap,
            max_total_cost=self._config.max_total_cost,
            max_leg_imbalance_usdc=self._config.max_leg_imbalance_usdc,
        )

        if not allowed:
            logger.debug(
                "Pair arb rejected",
                event_type="pair_arb_rejected",
                market_id=self._state.market_id,
                side=side.value,
                reason=reason,
            )
            return None

        return OrderIntent(
            market_id=self._state.market_id,
            side=side,
            price=price,
            size=self._config.step_usdc,
            strategy="pair_arb",
            reason=f"pair_cost_avg={self._state.position.pair_cost_avg()}",
        )

    def _select_leg_to_buy(
        self,
        orderbook_yes: OrderBook,
        orderbook_no: OrderBook,
    ) -> Optional[Side]:
        """Select which leg to buy next."""
        pos = self._state.position

        # If significantly imbalanced, buy the lagging leg
        imbalance_shares = pos.q_yes - pos.q_no
        threshold = 20.0  # Could be configurable

        if imbalance_shares > threshold:
            return Side.NO  # YES is ahead, buy NO
        if imbalance_shares < -threshold:
            return Side.YES  # NO is ahead, buy YES

        # Otherwise, buy the cheaper side
        yes_ask = orderbook_yes.best_ask
        no_ask = orderbook_no.best_ask

        if yes_ask is None and no_ask is None:
            return None

        if yes_ask is None:
            return Side.NO
        if no_ask is None:
            return Side.YES

        return Side.YES if yes_ask < no_ask else Side.NO

    # =========================================================================
    # Risk & Circuit Breaker
    # =========================================================================

    def _check_circuit_breaker(self) -> bool:
        """
        Check if circuit breaker should trip.
        
        Returns:
            True if circuit breaker tripped, False otherwise
        """
        if self._state.status == TradingStatus.HALT:
            return True

        reason: Optional[str] = None

        if self._state.consecutive_rejects >= self._config.max_consecutive_rejects:
            reason = f"consecutive_rejects:{self._state.consecutive_rejects}"

        if self._state.consecutive_cancel_failures >= self._config.max_cancel_failures:
            reason = f"cancel_failures:{self._state.consecutive_cancel_failures}"

        if reason:
            logger.warning(
                "Circuit breaker tripping",
                event_type="circuit_breaker_trip",
                market_id=self._state.market_id,
                reason=reason,
            )
            self.halt(reason)
            return True

        return False

    def _cancel_all_orders(self) -> list[CancelIntent]:
        """Generate cancel intents for all open orders."""
        intents: list[CancelIntent] = []

        for order_id, order in list(self._state.open_orders.items()):
            if order.status in (OrderStatus.PLACED, OrderStatus.PENDING):
                intents.append(
                    CancelIntent(
                        order_id=order_id,
                        market_id=self._state.market_id,
                        reason="cancel_all",
                    )
                )

        return intents

    def track_order(self, order: Order) -> None:
        """Track an order that was placed."""
        self._state.open_orders[order.id] = order

    def get_state_snapshot(self) -> TradingState:
        """Return copy of current state."""
        import copy
        return copy.deepcopy(self._state)
