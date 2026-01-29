"""Core types and dataclasses for PolyLOL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


# =============================================================================
# Enums
# =============================================================================


class Side(str, Enum):
    """Trading side: YES or NO."""

    YES = "YES"
    NO = "NO"


class TruthStatus(str, Enum):
    """Truth engine state."""

    PRE_MATCH = "PRE_MATCH"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    FINAL = "FINAL"


class TradingStatus(str, Enum):
    """Per-market trading state."""

    IDLE = "IDLE"
    BUILDING_PAIR = "BUILDING_PAIR"
    LOCKED_PAIR = "LOCKED_PAIR"
    TEMPORAL_ACTIVE = "TEMPORAL_ACTIVE"
    FINALIZING = "FINALIZING"
    RESOLVED = "RESOLVED"
    HALT = "HALT"


class OrderStatus(str, Enum):
    """Order lifecycle status."""

    PENDING = "PENDING"
    PLACED = "PLACED"
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class FillType(str, Enum):
    """Fill type."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"


class MatchEventType(str, Enum):
    """Normalized esports match event types."""

    MATCH_CREATED = "MATCH_CREATED"
    MATCH_STARTED = "MATCH_STARTED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    MAP_STARTED = "MAP_STARTED"
    ROUND_ENDED = "ROUND_ENDED"
    MAP_ENDED = "MAP_ENDED"
    SCORE_UPDATE = "SCORE_UPDATE"
    MATCH_ENDED = "MATCH_ENDED"
    CORRECTION = "CORRECTION"


class DataSourceTier(str, Enum):
    """Data source quality tier."""

    TIER_A = "A"  # Official/GRID - low latency, high reliability
    TIER_B = "B"  # PandaScore/OpenDota - good latency
    TIER_C = "C"  # Liquipedia - confirmation only


class CircuitState(str, Enum):
    """Circuit breaker state."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# =============================================================================
# Trading Types
# =============================================================================


@dataclass(frozen=True)
class Fill:
    """A single trade fill."""

    side: Side
    qty: float  # shares
    price: float  # [0, 1]
    ts_ms: int = 0
    fill_type: FillType = FillType.FULL
    order_id: Optional[str] = None


@dataclass
class PairPosition:
    """Tracks YES/NO position for a binary market."""

    market_id: str = ""
    fee_rate: float = 0.02

    # Quantities (shares)
    q_yes: float = 0.0
    q_no: float = 0.0

    # Costs (USDC spent)
    c_yes: float = 0.0
    c_no: float = 0.0

    # Timestamps
    first_fill_at: Optional[datetime] = None
    last_fill_at: Optional[datetime] = None

    def apply_fill(self, fill: Fill) -> None:
        """Apply a fill to the position."""
        if fill.qty <= 0:
            return
        if not (0.0 <= fill.price <= 1.0):
            raise ValueError(f"Price must be in [0, 1], got {fill.price}")

        now = datetime.now(timezone.utc)
        if self.first_fill_at is None:
            self.first_fill_at = now
        self.last_fill_at = now

        if fill.side == Side.YES:
            self.q_yes += fill.qty
            self.c_yes += fill.qty * fill.price
        else:
            self.q_no += fill.qty
            self.c_no += fill.qty * fill.price

    def total_cost(self) -> float:
        """Total USDC spent."""
        return self.c_yes + self.c_no

    def q_min(self) -> float:
        """Minimum of YES and NO quantities."""
        return min(self.q_yes, self.q_no)

    def payout_net(self) -> float:
        """Guaranteed payout at resolution (net of fees)."""
        return self.q_min() * (1.0 - self.fee_rate)

    def guaranteed_pnl(self) -> float:
        """Guaranteed P&L at resolution."""
        return self.payout_net() - self.total_cost()

    def avg_yes(self) -> Optional[float]:
        """Average cost per YES share."""
        return None if self.q_yes <= 0 else self.c_yes / self.q_yes

    def avg_no(self) -> Optional[float]:
        """Average cost per NO share."""
        return None if self.q_no <= 0 else self.c_no / self.q_no

    def pair_cost_avg(self) -> Optional[float]:
        """Average cost for a YES+NO pair."""
        ay, an = self.avg_yes(), self.avg_no()
        if ay is None or an is None:
            return None
        return ay + an

    def leg_imbalance_usdc(self) -> float:
        """Absolute difference in cost between legs."""
        return abs(self.c_yes - self.c_no)

    def leg_imbalance_shares(self) -> float:
        """Absolute difference in shares between legs."""
        return abs(self.q_yes - self.q_no)

    def copy(self) -> PairPosition:
        """Create a copy of this position."""
        return PairPosition(
            market_id=self.market_id,
            fee_rate=self.fee_rate,
            q_yes=self.q_yes,
            q_no=self.q_no,
            c_yes=self.c_yes,
            c_no=self.c_no,
            first_fill_at=self.first_fill_at,
            last_fill_at=self.last_fill_at,
        )

    def hypo_buy(self, side: Side, usdc_amount: float, price: float) -> PairPosition:
        """Create hypothetical position after a buy."""
        if usdc_amount <= 0 or price <= 0:
            return self.copy()

        qty = usdc_amount / price
        new_pos = self.copy()
        new_pos.apply_fill(Fill(side=side, qty=qty, price=price))
        return new_pos


@dataclass
class Order:
    """An order submitted to the exchange."""

    id: str = field(default_factory=lambda: str(uuid4()))
    market_id: str = ""
    token_id: str = ""
    side: Side = Side.YES
    price: float = 0.0
    size: float = 0.0  # USDC

    status: OrderStatus = OrderStatus.PENDING
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))

    # Lifecycle timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    placed_at: Optional[datetime] = None
    matched_at: Optional[datetime] = None
    mined_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None

    # Fill info
    filled_size: float = 0.0
    avg_fill_price: Optional[float] = None

    # Error info
    error_message: Optional[str] = None
    reject_reason: Optional[str] = None


@dataclass
class OrderIntent:
    """Intent to place an order (pre-risk-check)."""

    market_id: str
    side: Side
    price: float
    size: float  # USDC

    strategy: str = "pair_arb"  # "pair_arb" | "temporal_arb"
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))

    # Context
    reason: str = ""
    truth_confidence: Optional[float] = None
    expected_edge: Optional[float] = None


@dataclass
class CancelIntent:
    """Intent to cancel an order."""

    order_id: str
    market_id: str
    reason: str = ""


# =============================================================================
# Market Types
# =============================================================================


@dataclass
class MarketMapping:
    """Maps esports match to Polymarket market."""

    match_id: str  # e.g., "dota2_12345"
    poly_market_id: str
    poly_yes_token: str
    poly_no_token: str

    team_a_id: str  # Maps to YES
    team_a_name: str
    team_b_id: str  # Maps to NO
    team_b_name: str

    game: str  # "dota2" | "lol"
    tournament: Optional[str] = None
    resolution_time: Optional[datetime] = None
    correlation_group: Optional[str] = None


@dataclass(frozen=True)
class OrderBookLevel:
    """Single price level in orderbook."""

    price: float
    size: float  # shares available


@dataclass
class OrderBook:
    """Current orderbook state for a token."""

    token_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)  # sorted desc by price
    asks: list[OrderBookLevel] = field(default_factory=list)  # sorted asc by price
    timestamp_ms: int = 0

    @property
    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price."""
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid price between best bid and ask."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> Optional[float]:
        """Spread in basis points."""
        if self.best_bid is None or self.best_ask is None:
            return None
        mid = self.mid_price
        if mid is None or mid == 0:
            return None
        return (self.best_ask - self.best_bid) / mid * 10000

    def total_bid_liquidity(self) -> float:
        """Total USDC liquidity on bid side."""
        return sum(level.price * level.size for level in self.bids)

    def total_ask_liquidity(self) -> float:
        """Total USDC liquidity on ask side."""
        return sum(level.price * level.size for level in self.asks)

    def effective_price_for_size(self, side: Side, size_usdc: float) -> float:
        """
        Walk the book to get actual average fill price.
        
        Args:
            side: BUY walks asks, SELL walks bids
            size_usdc: Amount to fill in USDC
            
        Returns:
            Average fill price, or float('inf') if insufficient liquidity
        """
        levels = self.asks if side == Side.YES else self.bids
        remaining = size_usdc
        total_cost = 0.0
        total_qty = 0.0

        for level in levels:
            max_usdc_at_level = level.size * level.price
            usdc_at_level = min(remaining, max_usdc_at_level)
            qty_at_level = usdc_at_level / level.price

            total_cost += usdc_at_level
            total_qty += qty_at_level
            remaining -= usdc_at_level

            if remaining <= 0:
                break

        if total_qty == 0:
            return float("inf")

        return total_cost / total_qty


# =============================================================================
# State Types
# =============================================================================


@dataclass
class TruthState:
    """Current state of Truth Engine for a match."""

    match_id: str
    status: TruthStatus = TruthStatus.PRE_MATCH

    # Team info
    team_a_id: str = ""
    team_b_id: str = ""

    # Match data
    score_a: int = 0
    score_b: int = 0
    map_index: int = 0
    round_index: int = 0

    # Confidence
    confidence: float = 0.0
    winner_team_id: Optional[str] = None

    # Timing
    last_event_ms: int = 0
    ended_at_ms: Optional[int] = None
    finalized_at_ms: Optional[int] = None

    # Dedup
    seen_event_ids: set[str] = field(default_factory=set)
    last_seq: Optional[int] = None

    # Source tracking
    sources_confirming: set[str] = field(default_factory=set)


@dataclass
class TradingState:
    """Current trading state for a market."""

    market_id: str
    status: TradingStatus = TradingStatus.IDLE

    # Position
    position: PairPosition = field(default_factory=PairPosition)

    # Open orders
    open_orders: dict[str, Order] = field(default_factory=dict)

    # Circuit breaker
    consecutive_rejects: int = 0
    consecutive_cancel_failures: int = 0
    last_order_latency_ms: Optional[float] = None

    # Timing
    entered_state_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GlobalRiskState:
    """Global risk tracking."""

    daily_pnl: float = 0.0
    total_exposure: float = 0.0

    kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_at: Optional[datetime] = None

    halted_markets: set[str] = field(default_factory=set)

    # Error tracking
    consecutive_errors: int = 0


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class OrderResult:
    """Result of order placement attempt."""

    success: bool
    order: Optional[Order] = None
    error: Optional[str] = None
    deduplicated: bool = False


@dataclass
class SlippageResult:
    """Result of slippage check."""

    allowed: bool
    expected_slippage_bps: float
    effective_price: float = 0.0
    recommendation: Optional[str] = None


@dataclass
class RiskCheckResult:
    """Result of risk check."""

    approved: bool
    reason: Optional[str] = None
    adjusted_size: Optional[float] = None


# =============================================================================
# Helper Functions
# =============================================================================


def should_buy_more(
    pos: PairPosition,
    side: Side,
    usdc_amount: float,
    price: float,
    *,
    pair_cost_cap: float,
    max_total_cost: float,
    max_leg_imbalance_usdc: float = float("inf"),
    require_improve: bool = True,
) -> tuple[bool, str]:
    """
    Determine if we should place this buy.
    
    Returns:
        (allowed, reason)
    """
    if usdc_amount <= 0:
        return False, "zero_amount"

    if pos.total_cost() + usdc_amount > max_total_cost:
        return False, "exceeds_max_total"

    # Simulate the buy
    new_pos = pos.hypo_buy(side, usdc_amount, price)

    # Check pair cost cap
    pc = new_pos.pair_cost_avg()
    if pc is not None:
        net_cap = 1.0 - new_pos.fee_rate
        if pc >= net_cap:
            return False, "pair_cost_exceeds_net"
        if pc >= pair_cost_cap:
            return False, "pair_cost_exceeds_cap"

    # Check leg imbalance
    if new_pos.leg_imbalance_usdc() > max_leg_imbalance_usdc:
        return False, "leg_imbalance"

    # Must improve guaranteed PnL
    if require_improve:
        if new_pos.guaranteed_pnl() <= pos.guaranteed_pnl():
            return False, "no_pnl_improvement"

    return True, "approved"
