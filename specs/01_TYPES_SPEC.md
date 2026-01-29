# Specification: Core Types

> All dataclasses, enums, and type definitions for PolyLOL

---

## 1. Enums

### 1.1 Side

```python
class Side(str, Enum):
    YES = "YES"
    NO = "NO"
```

### 1.2 TruthStatus

```python
class TruthStatus(str, Enum):
    PRE_MATCH = "PRE_MATCH"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    FINAL = "FINAL"
```

### 1.3 TradingStatus

```python
class TradingStatus(str, Enum):
    IDLE = "IDLE"
    BUILDING_PAIR = "BUILDING_PAIR"
    LOCKED_PAIR = "LOCKED_PAIR"
    TEMPORAL_ACTIVE = "TEMPORAL_ACTIVE"
    FINALIZING = "FINALIZING"
    RESOLVED = "RESOLVED"
    HALT = "HALT"
```

### 1.4 OrderStatus

```python
class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
```

### 1.5 FillType

```python
class FillType(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
```

### 1.6 MatchEventType

```python
class MatchEventType(str, Enum):
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
```

### 1.7 DataSourceTier

```python
class DataSourceTier(str, Enum):
    TIER_A = "A"  # Official/GRID - low latency, high reliability
    TIER_B = "B"  # PandaScore/OpenDota - good latency
    TIER_C = "C"  # Liquipedia - confirmation only
```

### 1.8 CircuitState

```python
class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
```

---

## 2. Core Dataclasses

### 2.1 Fill

```python
@dataclass(frozen=True)
class Fill:
    """A single fill/trade."""
    side: Side
    qty: float          # shares
    price: float        # [0, 1]
    ts_ms: int = 0
    fill_type: FillType = FillType.FULL
    order_id: Optional[str] = None
```

### 2.2 PairPosition

```python
@dataclass
class PairPosition:
    """Tracks YES/NO position for a market."""
    market_id: str
    fee_rate: float = 0.02
    
    # Quantities
    q_yes: float = 0.0
    q_no: float = 0.0
    
    # Costs (USDC spent)
    c_yes: float = 0.0
    c_no: float = 0.0
    
    # Timestamps
    first_fill_at: Optional[datetime] = None
    last_fill_at: Optional[datetime] = None
    
    def apply_fill(self, fill: Fill) -> None: ...
    def total_cost(self) -> float: ...
    def q_min(self) -> float: ...
    def payout_net(self) -> float: ...
    def guaranteed_pnl(self) -> float: ...
    def avg_yes(self) -> Optional[float]: ...
    def avg_no(self) -> Optional[float]: ...
    def pair_cost_avg(self) -> Optional[float]: ...
    def leg_imbalance_usdc(self) -> float: ...
    def leg_imbalance_shares(self) -> float: ...
    def copy(self) -> "PairPosition": ...
    def hypo_buy(self, side: Side, usdc: float, price: float) -> "PairPosition": ...
```

### 2.3 Order

```python
@dataclass
class Order:
    """An order submitted to the exchange."""
    id: str
    market_id: str
    token_id: str
    side: Side
    price: float
    size: float  # USDC
    
    status: OrderStatus = OrderStatus.PENDING
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    
    # Lifecycle timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
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
```

### 2.4 OrderIntent

```python
@dataclass
class OrderIntent:
    """Intent to place an order (pre-risk-check)."""
    market_id: str
    side: Side
    price: float
    size: float  # USDC
    
    strategy: str  # "pair_arb" | "temporal_arb"
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    
    # Context
    reason: str = ""
    truth_confidence: Optional[float] = None
    expected_edge: Optional[float] = None
```

### 2.5 CancelIntent

```python
@dataclass
class CancelIntent:
    """Intent to cancel an order."""
    order_id: str
    market_id: str
    reason: str = ""
```

---

## 3. Market & Mapping Types

### 3.1 MarketMapping

```python
@dataclass
class MarketMapping:
    """Maps esports match to Polymarket market."""
    match_id: str           # e.g., "dota2_12345"
    poly_market_id: str     # Polymarket market ID
    poly_yes_token: str     # Token ID for YES
    poly_no_token: str      # Token ID for NO
    
    team_a_id: str          # Maps to YES
    team_a_name: str
    team_b_id: str          # Maps to NO
    team_b_name: str
    
    game: str               # "dota2" | "lol"
    tournament: Optional[str] = None
    resolution_time: Optional[datetime] = None
    
    # Correlation grouping
    correlation_group: Optional[str] = None  # e.g., tournament_id
```

### 3.2 OrderBookLevel

```python
@dataclass(frozen=True)
class OrderBookLevel:
    """Single price level in orderbook."""
    price: float
    size: float  # shares available
```

### 3.3 OrderBook

```python
@dataclass
class OrderBook:
    """Current orderbook state for a token."""
    token_id: str
    bids: list[OrderBookLevel]  # sorted descending by price
    asks: list[OrderBookLevel]  # sorted ascending by price
    timestamp_ms: int
    
    @property
    def best_bid(self) -> Optional[float]: ...
    
    @property
    def best_ask(self) -> Optional[float]: ...
    
    @property
    def mid_price(self) -> Optional[float]: ...
    
    @property
    def spread_bps(self) -> Optional[float]: ...
    
    def effective_price_for_size(self, side: Side, size_usdc: float) -> float:
        """Walk book levels to get average fill price."""
        ...
```

---

## 4. Event Types

### 4.1 Base Event

```python
@dataclass
class Event:
    """Base class for all events."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    market_id: Optional[str] = None  # For partitioning
```

### 4.2 MatchEvent

```python
@dataclass
class MatchEvent(Event):
    """Normalized esports match event."""
    match_id: str
    event_type: MatchEventType
    source: str                # "opendota" | "grid" | "liquipedia"
    source_tier: DataSourceTier
    
    # Optional event-specific data
    payload: dict = field(default_factory=dict)
    
    # For dedup
    source_event_id: Optional[str] = None
    seq: Optional[int] = None
```

### 4.3 OrderBookDelta

```python
@dataclass
class OrderBookDelta(Event):
    """Orderbook update from Polymarket."""
    token_id: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    is_snapshot: bool = False
```

### 4.4 UserFill

```python
@dataclass
class UserFill(Event):
    """Fill notification from Polymarket."""
    order_id: str
    token_id: str
    side: Side
    price: float
    size: float
    fill_type: FillType
    tx_hash: Optional[str] = None
```

### 4.5 TruthDelta

```python
@dataclass
class TruthDelta(Event):
    """Truth engine state change."""
    match_id: str
    delta_type: str  # "score" | "round" | "map" | "status"
    old_value: Any
    new_value: Any
    confidence: float
    sources: list[str]
```

### 4.6 TruthFinal

```python
@dataclass
class TruthFinal(Event):
    """Match outcome finalized."""
    match_id: str
    winner_team_id: str
    winner_side: Side  # YES or NO based on mapping
    confidence: float
    confirmed_by: list[str]  # Source IDs that confirmed
    finalized_at_ms: int
```

### 4.7 ClockTick

```python
@dataclass
class ClockTick(Event):
    """Periodic heartbeat for time-based logic."""
    tick_number: int
    wall_time: datetime
    monotonic_ns: int
```

### 4.8 SystemHalt

```python
@dataclass
class SystemHalt(Event):
    """Global trading halt."""
    reason: str
    triggered_by: str  # "kill_switch" | "manual" | "error"
    halt_at: datetime
```

---

## 5. State Types

### 5.1 TruthState

```python
@dataclass
class TruthState:
    """Current state of Truth Engine for a match."""
    match_id: str
    status: TruthStatus
    
    # Match data
    team_a_id: str
    team_b_id: str
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
```

### 5.2 TradingState

```python
@dataclass
class TradingState:
    """Current trading state for a market."""
    market_id: str
    status: TradingStatus
    
    # Position
    position: PairPosition
    
    # Open orders
    open_orders: dict[str, Order] = field(default_factory=dict)
    
    # Circuit breaker
    consecutive_rejects: int = 0
    consecutive_cancel_failures: int = 0
    last_order_latency_ms: Optional[float] = None
    
    # Timing
    entered_state_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: datetime = field(default_factory=datetime.utcnow)
```

### 5.3 GlobalRiskState

```python
@dataclass
class GlobalRiskState:
    """Global risk tracking."""
    daily_pnl: float = 0.0
    total_exposure: float = 0.0
    
    kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_at: Optional[datetime] = None
    
    # Per-market halts
    halted_markets: set[str] = field(default_factory=set)
```

---

## 6. Configuration Types

### 6.1 RiskParams

```python
@dataclass
class RiskParams:
    # Per-market
    max_position_per_market: float = 1500.0
    max_open_orders_per_market: int = 5
    max_leg_imbalance_usdc: float = 100.0
    max_leg_imbalance_shares: float = 50.0
    
    # Per-market circuit breaker
    max_consecutive_rejects: int = 3
    max_cancel_failures: int = 3
    max_order_latency_ms: float = 5000.0
    
    # Global
    max_total_exposure: float = 5000.0
    max_daily_loss: float = 200.0
    
    # Per-trade
    max_single_order: float = 100.0
    min_order_size: float = 5.0
    max_slippage_bps: float = 50.0
    
    # Correlation
    max_exposure_per_correlation_group: float = 2000.0
```

### 6.2 PairArbParams

```python
@dataclass
class PairArbParams:
    enabled: bool = True
    pair_cost_cap: float = 0.975
    safety_margin: float = 0.005
    fee_rate: float = 0.02
    step_usdc: float = 25.0
    max_total_cost: float = 1500.0
    
    # Leg balancing
    max_leg_imbalance_usdc: float = 100.0
    prefer_balance: bool = True
    
    # Orderbook requirements
    min_liquidity_usdc: float = 100.0
```

### 6.3 TemporalArbParams

```python
@dataclass
class TemporalArbParams:
    enabled: bool = False
    min_confidence: float = 0.90
    min_edge_threshold: float = 0.05
    max_entry_price: float = 0.95
    fixed_size: float = 50.0
    max_exposure: float = 200.0
    kelly_fraction: float = 0.25
    stale_threshold_ms: int = 30000
```

### 6.4 TruthEngineParams

```python
@dataclass
class TruthEngineParams:
    confirm_threshold: float = 0.90
    max_wait_ms: int = 10000
    required_sources_for_final: int = 2
    tier_a_sources: list[str] = field(default_factory=lambda: ["grid", "official"])
    allowed_skew_ms: int = 2000
```

---

## 7. Result Types

### 7.1 OrderResult

```python
@dataclass
class OrderResult:
    success: bool
    order: Optional[Order] = None
    error: Optional[str] = None
    deduplicated: bool = False
```

### 7.2 SlippageResult

```python
@dataclass
class SlippageResult:
    allowed: bool
    expected_slippage_bps: float
    recommendation: Optional[str] = None
```

### 7.3 RiskCheckResult

```python
@dataclass
class RiskCheckResult:
    approved: bool
    reason: Optional[str] = None
    adjusted_size: Optional[float] = None  # If size was capped
```

---

*Spec Version: 1.0*
