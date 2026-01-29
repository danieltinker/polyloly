# PolyLOL — Improved Architecture v2.0

> Production-Grade Esports Algo-Trader for Polymarket
> Incorporating all improvements from architecture reviews

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview](#2-system-overview)
3. [Project Structure](#3-project-structure)
4. [Dual State Machine Design](#4-dual-state-machine-design)
5. [Event Bus Architecture](#5-event-bus-architecture)
6. [Data Source Strategy](#6-data-source-strategy)
7. [Trading Strategies](#7-trading-strategies)
8. [Execution Layer](#8-execution-layer)
9. [Risk Management](#9-risk-management)
10. [Observability](#10-observability)
11. [Resilience Patterns](#11-resilience-patterns)
12. [Security](#12-security)
13. [Startup & Shutdown](#13-startup--shutdown)
14. [Failure Modes](#14-failure-modes)
15. [Development Phases](#15-development-phases)
16. [Specification Index](#16-specification-index)

---

## 1. Executive Summary

### What We're Building

A **production-grade** algorithmic trading system for Polymarket esports markets featuring:

- **Dual state machines**: Truth Engine (match state) + Trading Engine (per-market execution state)
- **Event-driven architecture**: Partitioned bus with backpressure and dead-letter queues
- **Multi-source confirmation**: Tiered data sources with consensus requirements
- **Robust execution**: Idempotent orders, partial fill handling, slippage protection
- **Defense in depth**: Circuit breakers, kill switches, correlation limits, graceful degradation

### Key Improvements Over v1.0

| Area | v1.0 | v2.0 |
|------|------|------|
| State Management | Single Truth Engine | Dual: Truth + Trading state machines |
| Event Bus | Simple pub/sub | Partitioned, backpressure, DLQ |
| Data Sources | All treated equally | Tiered (A/B/C) with confirmation rules |
| Paper Trading | Env var flag | First-class adapter |
| Price Checks | Top-of-book only | Effective fill price from depth |
| Risk | Global kill switch | Per-market + global circuit breakers |
| Observability | Basic logging | Structured logs + Prometheus metrics |
| Resilience | Minimal | Circuit breakers, graceful shutdown |

---

## 2. System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              POLYLOLY SYSTEM v2.0                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │                         DATA INGESTION LAYER                                │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │ │
│  │  │   TIER A     │  │   TIER B     │  │   TIER C     │  │  POLYMARKET  │   │ │
│  │  │  GRID/Official│  │ PandaScore   │  │ Liquipedia   │  │              │   │ │
│  │  │  (live feed) │  │ (good latency)│  │ (confirm only)│  │ • Orderbook  │   │ │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │ • User fills │   │ │
│  │         │ Circuit         │ Circuit         │          │ • REST API   │   │ │
│  │         │ Breaker         │ Breaker         │          └──────┬───────┘   │ │
│  │         └─────────────────┴─────────────────┴─────────────────┘           │ │
│  └──────────────────────────────────┬─────────────────────────────────────────┘ │
│                                     │                                            │
│  ┌──────────────────────────────────▼─────────────────────────────────────────┐ │
│  │                    PARTITIONED EVENT BUS                                    │ │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │ │
│  │  │ Market A Q  │ │ Market B Q  │ │ Market C Q  │ │  Global Q   │          │ │
│  │  │ (ordered)   │ │ (ordered)   │ │ (ordered)   │ │ (clock/sys) │  → DLQ   │ │
│  │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘          │ │
│  └─────────┼───────────────┼───────────────┼───────────────┼──────────────────┘ │
│            │               │               │               │                     │
│  ┌─────────▼───────────────▼───────────────▼───────────────▼──────────────────┐ │
│  │                         PROCESSING LAYER                                    │ │
│  │                                                                              │ │
│  │  ┌─────────────────────┐      ┌─────────────────────┐                      │ │
│  │  │    TRUTH ENGINE     │      │   TRADING ENGINE    │  (per market)        │ │
│  │  │                     │      │                     │                      │ │
│  │  │ PRE_MATCH           │      │ IDLE                │                      │ │
│  │  │ LIVE           ────────────▶ BUILDING_PAIR       │                      │ │
│  │  │ PAUSED              │      │ LOCKED_PAIR         │                      │ │
│  │  │ PENDING_CONFIRM     │      │ TEMPORAL_ACTIVE     │                      │ │
│  │  │ FINAL          ────────────▶ FINALIZING          │                      │ │
│  │  │                     │      │ RESOLVED            │                      │ │
│  │  │                     │      │ HALT                │                      │ │
│  │  └─────────────────────┘      └──────────┬──────────┘                      │ │
│  └──────────────────────────────────────────┼──────────────────────────────────┘ │
│                                             │                                    │
│  ┌──────────────────────────────────────────▼──────────────────────────────────┐ │
│  │                         EXECUTION LAYER                                      │ │
│  │                                                                              │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │ │
│  │  │  RISK MANAGER   │  │  ORDER MANAGER  │  │  RECONCILER     │             │ │
│  │  │                 │  │                 │  │                 │             │ │
│  │  │ • Per-market CB │  │ • Idempotency   │  │ • Position sync │             │ │
│  │  │ • Global limits │  │ • Lifecycle     │  │ • Invariants    │             │ │
│  │  │ • Correlation   │  │ • Partial fills │  │ • Alerts        │             │ │
│  │  │ • Kill switch   │  │ • Retries       │  │                 │             │ │
│  │  └─────────────────┘  └────────┬────────┘  └─────────────────┘             │ │
│  │                                │                                            │ │
│  │           ┌────────────────────┴────────────────────┐                      │ │
│  │           ▼                                         ▼                      │ │
│  │  ┌─────────────────┐                       ┌─────────────────┐             │ │
│  │  │  REAL EXECUTOR  │                       │ PAPER EXECUTOR  │             │ │
│  │  │ (py-clob-client)│                       │ (deterministic) │             │ │
│  │  └─────────────────┘                       └─────────────────┘             │ │
│  └──────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────────┐ │
│  │                         OBSERVABILITY LAYER                                  │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │ │
│  │  │ Structured  │  │ Prometheus  │  │   Alerts    │  │   Event     │        │ │
│  │  │ Logs (JSON) │  │  Metrics    │  │ (Telegram)  │  │   Replay    │        │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │ │
│  └──────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Project Structure

```
polyloly/
├── IMPROVED_ARCHITECTURE.md     # This document
├── pyproject.toml
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
│
├── specs/                       # Specification documents
│   ├── 01_TYPES_SPEC.md
│   ├── 02_TRUTH_ENGINE_SPEC.md
│   ├── 03_TRADING_STATE_MACHINE_SPEC.md
│   ├── 04_EVENT_BUS_SPEC.md
│   ├── 05_PAIR_ARB_SPEC.md
│   ├── 06_TEMPORAL_ARB_SPEC.md
│   ├── 07_EXECUTION_SPEC.md
│   ├── 08_RISK_SPEC.md
│   └── 09_ADAPTERS_SPEC.md
│
├── config/
│   ├── base.yaml
│   ├── dev.yaml
│   └── prod.yaml
│
├── docs/
│   ├── RUNBOOK.md               # Operational runbook
│   ├── DECISIONS.md             # Architecture Decision Records
│   └── archive/                 # Original design docs
│
├── src/
│   ├── __init__.py              # Contains __version__
│   │
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── main.py              # Entry point
│   │   ├── settings.py          # Config + validation
│   │   ├── bus.py               # Partitioned event bus
│   │   ├── clock.py             # Monotonic + wall clock
│   │   ├── circuit_breaker.py   # Circuit breaker pattern
│   │   ├── health.py            # Health check server
│   │   ├── shutdown.py          # Graceful shutdown
│   │   ├── errors.py            # Custom exceptions
│   │   ├── logging.py           # Structured JSON logging
│   │   └── metrics.py           # Prometheus metrics
│   │
│   ├── adapters/
│   │   ├── __init__.py
│   │   │
│   │   ├── polymarket/
│   │   │   ├── __init__.py
│   │   │   ├── client.py        # py-clob-client wrapper
│   │   │   ├── execution_real.py    # Real order execution
│   │   │   ├── execution_paper.py   # Paper trading executor
│   │   │   ├── ws_orderbook.py
│   │   │   ├── ws_user.py
│   │   │   └── models.py
│   │   │
│   │   └── esports/
│   │       ├── __init__.py
│   │       ├── base.py          # Provider interface + tiers
│   │       ├── grid.py          # Tier A: Official/GRID
│   │       ├── pandascore.py    # Tier B: PandaScore
│   │       ├── opendota.py      # Tier B: OpenDota
│   │       ├── liquipedia.py    # Tier C: Confirmation only
│   │       └── models.py
│   │
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── types.py             # Core dataclasses
│   │   │
│   │   ├── state/               # Immutable state models
│   │   │   ├── __init__.py
│   │   │   ├── truth_state.py
│   │   │   ├── market_state.py
│   │   │   └── execution_state.py
│   │   │
│   │   ├── engines/
│   │   │   ├── __init__.py
│   │   │   ├── truth_engine.py      # Match state machine
│   │   │   └── trading_engine.py    # Per-market trading state
│   │   │
│   │   ├── pnl/                 # Fee-aware P&L accounting
│   │   │   ├── __init__.py
│   │   │   ├── calculator.py
│   │   │   └── attribution.py
│   │   │
│   │   ├── markets.py
│   │   ├── signals.py
│   │   └── risk.py
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   │
│   │   ├── pair_arb/
│   │   │   ├── __init__.py
│   │   │   ├── math.py          # Arb math (effective prices)
│   │   │   ├── engine.py        # Decision loop
│   │   │   └── params.py
│   │   │
│   │   └── temporal_arb/
│   │       ├── __init__.py
│   │       ├── edge_model.py    # Probability estimation
│   │       ├── latency.py       # Latency tracking
│   │       ├── engine.py
│   │       └── params.py
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_manager.py     # Idempotent + lifecycle
│   │   ├── slippage.py          # Slippage protection
│   │   ├── state.py
│   │   └── reconciliation.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── events_writer.py     # Versioned JSONL
│   │   └── sqlite.py
│   │
│   └── tools/
│       ├── __init__.py
│       ├── healthcheck.py       # CLI health verifier
│       ├── record_streams.py
│       └── replay_backtest.py
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_truth_engine.py
│   │   ├── test_trading_engine.py
│   │   ├── test_pair_math.py
│   │   └── test_pair_math_property.py   # Hypothesis tests
│   ├── integration/
│   │   └── test_full_flow.py
│   └── chaos/
│       └── chaos_monkey.py
│
├── scripts/
│   ├── pnl_simulation.py
│   └── chaos_test.py
│
└── docker/
    ├── Dockerfile
    ├── compose.yaml
    └── prometheus.yml
```

---

## 4. Dual State Machine Design

### 4.1 Truth Engine (Match State)

Converts esports events → high-confidence truth signals.

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRUTH ENGINE STATES                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PRE_MATCH ──────▶ LIVE ──────▶ PENDING_CONFIRM ──────▶ FINAL   │
│       │              │                 │                         │
│       │              ▼                 │                         │
│       └────────▶ PAUSED ◀──────────────┘                        │
│                    │                                             │
│                    ▼                                             │
│               (timeout/error)                                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Outputs:
  • TruthDelta(type, data, confidence)
  • TruthFinal(winner_team_id, confidence, sources)
```

**Confidence Accumulation:**
- Initial `MATCH_ENDED`: 0.80
- Second consistent source within 5s: +0.10
- Tier-A source confirms: +0.05
- Final threshold: 0.90 (or timeout 10s)

**Multi-Source Confirmation:**
```yaml
truth_engine:
  confirm_threshold: 0.90
  max_wait_ms: 10000
  required_sources_for_final: 2    # OR one Tier-A source
  tier_a_sources: ["grid", "official"]
```

### 4.2 Trading Engine (Per-Market Execution State)

Controls allowed actions per market.

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRADING ENGINE STATES                         │
│                       (per market)                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  IDLE ────────────────┬──────────────────────────────────────┐  │
│    │                  │                                      │  │
│    │ (arb opportunity)│ (temporal signal)                    │  │
│    ▼                  ▼                                      │  │
│  BUILDING_PAIR    TEMPORAL_ACTIVE                            │  │
│    │                  │                                      │  │
│    │ (pnl > 0)        │ (signal expires)                     │  │
│    ▼                  │                                      │  │
│  LOCKED_PAIR ◀────────┘                                      │  │
│    │                                                         │  │
│    │ (truth.effectively_final)                               │  │
│    ▼                                                         │  │
│  FINALIZING ─────────────────────────────────────────────────┤  │
│    │                                                         │  │
│    │ (settlement confirmed)                                  │  │
│    ▼                                                         │  │
│  RESOLVED                                                    │  │
│                                                              │  │
│  ─────────────────────────────────────────────────────────   │  │
│  Any state ──(risk trigger)──▶ HALT                          │  │
│                                                              │  │
└─────────────────────────────────────────────────────────────────┘

Allowed Actions by State:
  IDLE:             [watch]
  BUILDING_PAIR:    [buy_yes, buy_no, cancel]
  LOCKED_PAIR:      [watch] (no new orders)
  TEMPORAL_ACTIVE:  [buy_winner, cancel]
  FINALIZING:       [cancel_all] (no new entries)
  RESOLVED:         [none]
  HALT:             [cancel_all]
```

---

## 5. Event Bus Architecture

### 5.1 Partitioned Design

```python
class PartitionedEventBus:
    """Per-market queues for ordering + isolation."""
    
    def __init__(self, config: BusConfig):
        self._market_queues: dict[str, asyncio.Queue] = {}
        self._global_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._dlq: asyncio.Queue = asyncio.Queue()
        self._config = config
    
    async def publish(self, event: Event) -> None:
        queue = self._get_queue(event)
        
        # Backpressure handling
        if queue.full():
            match self._config.overflow_policy:
                case "drop":
                    logger.warning(f"Dropping event: {event}")
                    return
                case "coalesce":
                    await self._coalesce(queue, event)
                case "block":
                    await queue.put(event)  # Will block
                case "halt":
                    raise BackpressureError()
```

### 5.2 Event Types

```python
# Market-scoped events (go to market partition)
MatchEvent          # From esports
OrderBookDelta      # From Polymarket
UserFill            # From Polymarket
TruthDelta          # From Truth Engine
TruthFinal          # From Truth Engine

# Global events (go to global queue)
ClockTick           # Periodic heartbeat
SystemHalt          # Kill switch triggered
ConfigReload        # Hot config update
```

### 5.3 Dead Letter Queue

Failed events after max retries go to DLQ for manual inspection.

```python
@dataclass
class FailedEvent:
    event: Event
    error: Exception
    failed_at: datetime
    attempt_count: int
    handler_name: str
```

---

## 6. Data Source Strategy

### 6.1 Tiered Sources

| Tier | Sources | Latency | Reliability | Use Case |
|------|---------|---------|-------------|----------|
| **A** | GRID, Official League APIs | < 500ms | High | Live signals, final confirmation |
| **B** | PandaScore, OpenDota | 1-5s | Medium | Primary data, cross-validation |
| **C** | Liquipedia, community wikis | 10s-5min | Variable | Confirmation only, never for signals |

### 6.2 Multi-Source Confirmation Rules

```yaml
confirmation:
  # For MATCH_ENDED to become FINAL:
  rules:
    - type: "tier_a_single"
      description: "One Tier-A source is sufficient"
      
    - type: "tier_b_consensus"
      description: "Two Tier-B sources must agree"
      required_sources: 2
      max_time_diff_ms: 5000
      
    - type: "timeout_fallback"
      description: "After 10s, accept single source"
      timeout_ms: 10000
```

### 6.3 Staleness Detection

```python
@dataclass
class SourceHealth:
    source_id: str
    last_event_at: datetime
    stale_threshold_ms: int = 30000
    
    @property
    def is_stale(self) -> bool:
        age_ms = (datetime.utcnow() - self.last_event_at).total_seconds() * 1000
        return age_ms > self.stale_threshold_ms

# If stale: halt temporal strategy for that match
```

---

## 7. Trading Strategies

### 7.1 Binary Pair Arbitrage (Improved)

**Key Improvements:**
1. Use **effective fill price** from orderbook depth, not just top-of-book
2. Add **leg imbalance constraint** to ensure hedge completion
3. Handle **partial fills** gracefully

```python
# Effective price calculation
def effective_price_for_size(orderbook: OrderBook, side: Side, size_usdc: float) -> float:
    """Walk the book to get true average fill price."""
    remaining = size_usdc
    total_cost = 0.0
    total_qty = 0.0
    
    levels = orderbook.asks if side == Side.BUY else orderbook.bids
    for price, qty in levels:
        fill_qty = min(remaining / price, qty)
        total_cost += fill_qty * price
        total_qty += fill_qty
        remaining -= fill_qty * price
        if remaining <= 0:
            break
    
    return total_cost / total_qty if total_qty > 0 else float('inf')

# Profitability check (improved)
def is_pair_profitable(orderbook_yes, orderbook_no, size_usdc, params) -> bool:
    eff_yes = effective_price_for_size(orderbook_yes, Side.BUY, size_usdc)
    eff_no = effective_price_for_size(orderbook_no, Side.BUY, size_usdc)
    
    return (eff_yes + eff_no) < (1.0 - params.fee_rate - params.safety_margin)
```

**Leg Imbalance Constraint:**
```python
@dataclass
class PairArbParams:
    # ... existing params ...
    max_leg_imbalance_usdc: float = 100.0   # |cost_yes - cost_no| limit
    max_leg_imbalance_shares: float = 50.0  # |qty_yes - qty_no| limit
```

### 7.2 Temporal Arbitrage (Improved)

**Key Improvements:**
1. Map truth state → **implied probability** (not just confidence)
2. Add **latency tracking** to measure edge window
3. Add **staleness detection** to halt on feed issues

```python
# Implied probability model
def truth_to_implied_prob(truth: TruthState, game: str) -> float:
    """Map truth engine state to win probability estimate."""
    if truth.status == TruthStatus.FINAL:
        return 0.99
    
    if truth.status == TruthStatus.PENDING_CONFIRM:
        return 0.85 + (truth.confidence * 0.10)
    
    # Live score-based (game-specific)
    if game == "dota2":
        return _dota2_score_model(truth)
    elif game == "lol":
        return _lol_score_model(truth)
    
    return 0.50  # Unknown

# Edge calculation
def calculate_edge(implied_prob: float, market_price: float) -> float:
    return implied_prob - market_price

# Trade when edge > threshold
if edge > params.min_edge_threshold:
    size = kelly_stake(edge, 1/market_price, params.kelly_fraction)
    emit(PlaceOrderIntent(...))
```

**Latency Tracking:**
```python
class LatencyTracker:
    def __init__(self, window_size: int = 100):
        self._samples: deque[float] = deque(maxlen=window_size)
    
    def record(self, event_ts_ms: float, market_reaction_ts_ms: float):
        self._samples.append(market_reaction_ts_ms - event_ts_ms)
    
    @property
    def avg_edge_window_ms(self) -> float:
        return statistics.mean(self._samples) if self._samples else 0.0
    
    def is_edge_available(self, event_age_ms: float) -> bool:
        """Do we still have time to trade?"""
        return event_age_ms < self.avg_edge_window_ms * 0.7  # 30% buffer
```

---

## 8. Execution Layer

### 8.1 Order Manager (Improved)

**Idempotency:**
```python
@dataclass
class OrderIntent:
    market_id: str
    side: Side
    price: float
    size: float
    idempotency_key: str = field(
        default_factory=lambda: str(uuid4())
    )

class OrderManager:
    def __init__(self):
        self._pending_keys: TTLCache[str, OrderStatus] = TTLCache(
            maxsize=1000, 
            ttl=300
        )
    
    async def place(self, intent: OrderIntent) -> OrderResult:
        # Deduplicate
        if intent.idempotency_key in self._pending_keys:
            return OrderResult(
                status=self._pending_keys[intent.idempotency_key],
                deduplicated=True
            )
        
        self._pending_keys[intent.idempotency_key] = OrderStatus.PENDING
        # ... place order ...
```

**Partial Fill Handling:**
```python
async def on_fill(self, fill: UserFill):
    if fill.fill_type == FillType.PARTIAL:
        # Update position
        self._position.apply_fill(fill)
        
        # Check leg imbalance
        imbalance = abs(self._position.cost_yes - self._position.cost_no)
        if imbalance > self.params.max_leg_imbalance_usdc:
            # Prioritize rebalancing
            await self._emit_rebalance_intent()
```

### 8.2 Slippage Protection

```python
class SlippageGuard:
    def check(self, intent: OrderIntent, orderbook: OrderBook) -> SlippageResult:
        expected_price = effective_price_for_size(
            orderbook, 
            intent.side, 
            intent.size
        )
        
        slippage_bps = abs(expected_price - orderbook.mid_price) / orderbook.mid_price * 10000
        
        if slippage_bps > self.params.max_slippage_bps:
            return SlippageResult(
                allowed=False,
                slippage_bps=slippage_bps,
                recommendation="Reduce size or use limit order"
            )
        
        return SlippageResult(allowed=True, slippage_bps=slippage_bps)
```

### 8.3 Paper Trading Adapter

```python
# src/adapters/polymarket/execution_paper.py

class PaperExecutor(ExecutorInterface):
    """Deterministic paper trading for testing/replay."""
    
    def __init__(self, orderbook_simulator: OrderBookSimulator):
        self._simulator = orderbook_simulator
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
    
    async def place_order(self, intent: OrderIntent) -> Order:
        # Simulate fill based on orderbook state
        fill_price = self._simulator.simulate_fill(intent)
        
        order = Order(
            id=str(uuid4()),
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            # ...
        )
        
        self._orders[order.id] = order
        self._update_position(intent.market_id, intent.side, intent.size, fill_price)
        
        return order
```

---

## 9. Risk Management

### 9.1 Layered Risk Controls

```
┌─────────────────────────────────────────────────────────────────┐
│                      RISK MANAGEMENT LAYERS                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1: PRE-TRADE CHECKS                                      │
│  ├── Slippage guard                                             │
│  ├── Size limits (per-order, per-market)                        │
│  ├── Leg imbalance check                                        │
│  └── Correlation exposure check                                  │
│                                                                  │
│  Layer 2: PER-MARKET CIRCUIT BREAKERS                           │
│  ├── Max consecutive rejects                                    │
│  ├── Max cancel failures                                        │
│  └── Max order latency                                          │
│                                                                  │
│  Layer 3: GLOBAL RISK LIMITS                                    │
│  ├── Max total exposure                                         │
│  ├── Max daily loss (kill switch)                               │
│  └── Drawdown-based sizing                                      │
│                                                                  │
│  Layer 4: TIME-BASED RULES                                      │
│  ├── Market open/close buffers                                  │
│  ├── Peak hours size reduction                                  │
│  └── Weekend trading toggle                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 9.2 Per-Market Circuit Breaker

```python
@dataclass
class MarketCircuitBreaker:
    market_id: str
    max_consecutive_rejects: int = 3
    max_cancel_failures: int = 3
    max_order_latency_ms: float = 5000
    
    # State
    consecutive_rejects: int = 0
    cancel_failures: int = 0
    
    def on_reject(self):
        self.consecutive_rejects += 1
        if self.consecutive_rejects >= self.max_consecutive_rejects:
            return CircuitAction.HALT_MARKET
        return CircuitAction.CONTINUE
    
    def on_success(self):
        self.consecutive_rejects = 0
```

### 9.3 Correlation Limits

```python
@dataclass
class CorrelationRule:
    """Limit exposure to correlated markets."""
    
    # E.g., all matches in same tournament
    correlation_groups: dict[str, list[str]]  # group_id -> [market_ids]
    max_exposure_per_group: float = 2000.0
    
    def check(self, positions: dict[str, Position], new_intent: OrderIntent) -> bool:
        group_id = self._get_group(new_intent.market_id)
        if not group_id:
            return True
        
        group_exposure = sum(
            positions[mid].total_cost
            for mid in self.correlation_groups[group_id]
            if mid in positions
        )
        
        return (group_exposure + new_intent.size) <= self.max_exposure_per_group
```

### 9.4 Volatility-Adjusted Sizing

```python
class VolatilityAdjustedSizer:
    def __init__(self, lookback_ticks: int = 50):
        self._price_history: deque[float] = deque(maxlen=lookback_ticks)
    
    def update(self, mid_price: float):
        self._price_history.append(mid_price)
    
    def get_adjusted_size(self, base_size: float) -> float:
        if len(self._price_history) < 10:
            return base_size * 0.5  # Conservative until data
        
        volatility = statistics.stdev(self._price_history)
        factor = 1.0 / (1.0 + volatility * 10)
        
        return base_size * max(0.25, min(1.0, factor))
```

---

## 10. Observability

### 10.1 Structured Logging Schema

```python
LOG_SCHEMA = {
    # Always present
    "ts": "ISO8601",
    "level": "DEBUG|INFO|WARNING|ERROR|CRITICAL",
    "component": "truth_engine|order_manager|pair_arb|...",
    "event_type": "string",
    
    # Correlation IDs (when applicable)
    "run_id": "uuid",
    "market_id": "optional",
    "match_id": "optional",
    "order_id": "optional",
    "intent_id": "optional",
    
    # Metrics (when applicable)
    "latency_ms": "optional float",
    "pnl_usdc": "optional float",
    "slippage_bps": "optional float",
}
```

### 10.2 Prometheus Metrics

```python
# Trading metrics
orders_placed = Counter('polyloly_orders_total', 'Orders placed', ['side', 'strategy', 'market'])
orders_filled = Counter('polyloly_fills_total', 'Orders filled', ['side', 'strategy'])
order_latency = Histogram('polyloly_order_latency_seconds', 'Order placement latency')
slippage_bps = Histogram('polyloly_slippage_bps', 'Execution slippage')

# P&L metrics
pnl_realized = Gauge('polyloly_pnl_realized_usdc', 'Realized P&L')
pnl_unrealized = Gauge('polyloly_pnl_unrealized_usdc', 'Unrealized P&L')
daily_pnl = Gauge('polyloly_daily_pnl_usdc', 'Daily P&L')

# System metrics
event_lag_ms = Histogram('polyloly_event_lag_ms', 'Event processing lag', ['source'])
ws_reconnects = Counter('polyloly_ws_reconnects_total', 'WS reconnections', ['feed'])
circuit_breaker_trips = Counter('polyloly_circuit_breaker_trips_total', 'CB trips', ['market', 'reason'])
truth_confidence = Gauge('polyloly_truth_confidence', 'Truth engine confidence', ['match_id'])
```

### 10.3 Replay Schema Versioning

```python
@dataclass
class EventEnvelope:
    schema_version: str = "1.0"
    event_type: str
    timestamp_ms: int
    sequence: int
    market_id: Optional[str]
    payload: dict
    
    def to_jsonl(self) -> str:
        return json.dumps(asdict(self))
```

---

## 11. Resilience Patterns

### 11.1 Circuit Breaker for External Calls

```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3

class CircuitBreaker:
    states = Literal["CLOSED", "OPEN", "HALF_OPEN"]
    
    async def call(self, func: Callable, *args, **kwargs):
        if self.state == "OPEN":
            if time.monotonic() - self.last_failure > self.config.recovery_timeout:
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError(f"Circuit open for {func.__name__}")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
```

### 11.2 Retry with Exponential Backoff

```python
class RetryPolicy:
    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
    
    def get_delay(self, attempt: int) -> float:
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)
```

---

## 12. Security

### 12.1 Secrets Management

```python
class SecretsManager:
    """Load secrets from appropriate backend."""
    
    def __init__(self, provider: str):
        self._provider = provider
    
    def get_private_key(self) -> str:
        match self._provider:
            case "env":
                return os.environ["POLYMARKET_PRIVATE_KEY"]
            case "vault":
                return self._fetch_from_vault("polymarket/private_key")
            case "aws_secrets":
                return self._fetch_from_aws("polyloly/prod")
    
    def _fetch_from_vault(self, path: str) -> str:
        # HashiCorp Vault integration
        ...
```

### 12.2 Security Rules

- **Never** log private keys or full credentials
- **Never** commit secrets to git (use `.gitignore`)
- **Always** use secrets manager in production
- **Rotate** keys periodically

---

## 13. Startup & Shutdown

### 13.1 Startup Checklist

```python
async def startup_checks() -> StartupResult:
    checks = []
    
    # 1. Config validation
    checks.append(("config", validate_config()))
    
    # 2. Time sync
    checks.append(("ntp_sync", check_ntp_sync()))
    
    # 3. Esports feeds
    for source in enabled_sources:
        checks.append((f"esports_{source}", await check_esports_connection(source)))
    
    # 4. Polymarket connectivity
    checks.append(("polymarket_ws", await check_polymarket_ws()))
    checks.append(("polymarket_rest", await check_polymarket_rest()))
    
    # 5. USDC allowance
    checks.append(("usdc_allowance", await check_usdc_allowance()))
    
    # 6. Warm caches
    checks.append(("market_mappings", await warm_market_cache()))
    
    # 7. Paper trading default
    if not os.environ.get("LIVE_TRADING"):
        logger.warning("Starting in PAPER_TRADING mode")
    
    return StartupResult(checks=checks, all_passed=all(c[1] for c in checks))
```

### 13.2 Graceful Shutdown

```python
class GracefulShutdown:
    async def shutdown(self, signal_received: signal.Signals):
        logger.info(f"Shutdown initiated: {signal_received}")
        
        # 1. Stop accepting new signals
        self.strategies_paused = True
        
        # 2. Cancel open orders (if configured)
        if self.config.cancel_on_shutdown:
            await self.order_manager.cancel_all()
        
        # 3. Flush event log
        await self.storage.flush()
        
        # 4. Close connections cleanly
        await self.adapters.close_all()
        
        # 5. Final state snapshot
        await self.storage.write_state_snapshot(self.state)
        
        logger.info("Shutdown complete")
```

---

## 14. Failure Modes

| Failure | Detection | Mitigation | Alert |
|---------|-----------|------------|-------|
| WS disconnect | Heartbeat timeout | Auto-reconnect with backoff | Telegram after 3 failures |
| Esports feed stale | `last_event_age > threshold` | Halt temporal strategy | Telegram immediately |
| Partial fills | Fill event type | Rebalance lagging leg | Log only |
| Order rejection burst | Consecutive reject counter | Per-market halt | Telegram |
| Chain congestion | Order latency > threshold | Reduce order rate | Log |
| Position mismatch | Reconciliation invariant | Halt market, rebuild state | Telegram (critical) |
| Daily loss exceeded | P&L tracker | Global kill switch | Telegram (critical) |
| API rate limit | 429 response | Exponential backoff | Log |

---

## 15. Development Phases

### Phase 1: Foundation (Week 1)
- [ ] Project scaffolding
- [ ] Core types (`src/domain/types.py`)
- [ ] Partitioned event bus
- [ ] Structured logging
- [ ] Settings/config validation

### Phase 2: State Machines (Week 2)
- [ ] Truth Engine (all states + transitions)
- [ ] Trading Engine (per-market states)
- [ ] Multi-source confirmation logic
- [ ] Unit tests (100% state coverage)

### Phase 3: Adapters (Week 3)
- [ ] Polymarket client wrapper
- [ ] Orderbook WebSocket
- [ ] OpenDota client (Tier B)
- [ ] GRID client stub (Tier A)
- [ ] Circuit breakers

### Phase 4: Strategies (Week 4)
- [ ] Pair arb math (effective prices)
- [ ] Pair arb engine
- [ ] Temporal arb edge model
- [ ] Latency tracker
- [ ] Property-based tests

### Phase 5: Execution (Week 5)
- [ ] Order manager (idempotent)
- [ ] Slippage guard
- [ ] Partial fill handling
- [ ] Paper trading adapter
- [ ] Reconciliation

### Phase 6: Risk & Hardening (Week 6)
- [ ] Per-market circuit breakers
- [ ] Global risk limits
- [ ] Correlation limits
- [ ] Kill switch
- [ ] Health checks
- [ ] Graceful shutdown

### Phase 7: Observability (Week 7)
- [ ] Prometheus metrics
- [ ] Grafana dashboards
- [ ] Telegram alerts
- [ ] Event replay tooling

### Phase 8: Deployment (Week 8)
- [ ] Multi-stage Dockerfile
- [ ] Docker Compose
- [ ] CI/CD pipeline
- [ ] Runbook documentation

---

## 16. Specification Index

| Spec | File | Description |
|------|------|-------------|
| Types | `specs/01_TYPES_SPEC.md` | All dataclasses and enums |
| Truth Engine | `specs/02_TRUTH_ENGINE_SPEC.md` | Match state machine |
| Trading Engine | `specs/03_TRADING_STATE_MACHINE_SPEC.md` | Per-market execution states |
| Event Bus | `specs/04_EVENT_BUS_SPEC.md` | Partitioned bus with backpressure |
| Pair Arb | `specs/05_PAIR_ARB_SPEC.md` | Binary pair arbitrage strategy |
| Temporal Arb | `specs/06_TEMPORAL_ARB_SPEC.md` | Temporal arbitrage strategy |
| Execution | `specs/07_EXECUTION_SPEC.md` | Order management |
| Risk | `specs/08_RISK_SPEC.md` | Risk management |
| Adapters | `specs/09_ADAPTERS_SPEC.md` | External API interfaces |

---

*Document Version: 2.0 | January 2025*
