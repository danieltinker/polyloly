# Architecture Decision Records (ADRs)

> Documenting key architectural decisions for PolyLOL

---

## ADR Index

| ID | Title | Status | Date |
|----|-------|--------|------|
| [ADR-001](#adr-001-event-driven-architecture) | Event-Driven Architecture | Accepted | 2025-01 |
| [ADR-002](#adr-002-dual-state-machines) | Dual State Machines | Accepted | 2025-01 |
| [ADR-003](#adr-003-partitioned-event-bus) | Partitioned Event Bus | Accepted | 2025-01 |
| [ADR-004](#adr-004-tiered-data-sources) | Tiered Data Sources | Accepted | 2025-01 |
| [ADR-005](#adr-005-paper-trading-as-first-class-adapter) | Paper Trading as First-Class Adapter | Accepted | 2025-01 |
| [ADR-006](#adr-006-effective-price-calculation) | Effective Price Calculation | Accepted | 2025-01 |
| [ADR-007](#adr-007-python-for-strategy-layer) | Python for Strategy Layer | Accepted | 2025-01 |

---

## ADR-001: Event-Driven Architecture

### Status
Accepted

### Context
We need to build a trading system that:
- Processes real-time data from multiple sources
- Maintains consistency across components
- Supports replay/backtest from historical data
- Is testable in isolation

### Decision
Use an **event-driven architecture** with an async pub/sub event bus.

All communication between major components happens through typed events:
- `MatchEvent` - Esports data
- `OrderBookDelta` - Market data
- `TruthDelta` - State changes
- `OrderIntent` - Trading decisions

### Consequences

**Positive:**
- Components are decoupled and independently testable
- Easy to record and replay events for backtest
- Natural fit for async I/O patterns
- Clear data flow, easy to trace

**Negative:**
- Slight latency overhead (< 1ms, acceptable)
- Debugging requires tracing through bus
- Event schema must be carefully versioned

### Alternatives Considered

1. **Direct function calls** - Rejected: tight coupling, hard to test
2. **Message queue (Redis/RabbitMQ)** - Rejected: overkill for single-process, adds operational complexity

---

## ADR-002: Dual State Machines

### Status
Accepted

### Context
The original design had a single Truth Engine state machine. However, trading execution has its own state lifecycle that doesn't map cleanly to match state.

### Decision
Implement **two separate state machines**:

1. **Truth Engine** - Match state (PRE_MATCH → LIVE → FINAL)
2. **Trading Engine** - Per-market execution state (IDLE → BUILDING_PAIR → LOCKED → FINALIZING → RESOLVED)

### Consequences

**Positive:**
- Clear separation of concerns
- Easier to reason about allowed actions per state
- Trading state can be halted independently
- Better test coverage (each machine tested separately)

**Negative:**
- More complexity (two state machines to maintain)
- Must keep them synchronized

### Alternatives Considered

1. **Single combined state machine** - Rejected: too many state combinations, hard to maintain
2. **Stateless handlers** - Rejected: loses important context, error-prone

---

## ADR-003: Partitioned Event Bus

### Status
Accepted

### Context
A simple global event queue risks:
- Cross-market head-of-line blocking
- Lost ordering guarantees when events interleave
- Memory exhaustion under load

### Decision
Implement a **partitioned event bus**:
- One queue per market (market_id as partition key)
- One global queue for system events
- Backpressure handling with configurable policies (drop, coalesce, block)
- Dead letter queue for failed events

### Consequences

**Positive:**
- Events for the same market are processed in order
- One slow market doesn't block others
- Bounded memory usage with backpressure
- Failed events captured for debugging

**Negative:**
- More complex implementation
- Must ensure correct partitioning

### Alternatives Considered

1. **Single global queue** - Rejected: ordering and blocking issues
2. **Kafka** - Rejected: operational overhead for single-node deployment

---

## ADR-004: Tiered Data Sources

### Status
Accepted

### Context
Esports data sources vary widely in:
- Latency (500ms to minutes)
- Reliability
- Cost

Using all sources equally leads to confusion about data quality.

### Decision
Implement a **tiered source classification**:

| Tier | Sources | Use Case |
|------|---------|----------|
| A | GRID, Official APIs | Live signals, single-source finalization |
| B | PandaScore, OpenDota | Primary data, cross-validation |
| C | Liquipedia | Confirmation only, never for live signals |

Finalization requires either:
- One Tier-A source, OR
- Two Tier-B sources agreeing, OR
- Timeout (10s) with any source

### Consequences

**Positive:**
- Clear data quality hierarchy
- Prevents trading on slow/unreliable data
- Explicit confirmation requirements

**Negative:**
- More configuration to manage
- May miss opportunities if all Tier-A sources are unavailable

### Alternatives Considered

1. **Treat all sources equally** - Rejected: invites trading on stale data
2. **Tier-A required** - Rejected: too restrictive, Tier-A may not cover all games

---

## ADR-005: Paper Trading as First-Class Adapter

### Status
Accepted

### Context
Paper trading was originally a simple env var flag. This led to:
- Conditional logic scattered throughout codebase
- Inconsistent behavior between paper and live
- Difficult to replay deterministically

### Decision
Implement paper trading as a **first-class execution adapter**:

```
ExecutorInterface
├── RealExecutor (py-clob-client)
└── PaperExecutor (deterministic simulation)
```

Both implement the same interface. Selection happens at startup based on config.

### Consequences

**Positive:**
- Clean separation, no conditionals in business logic
- Paper executor can simulate fills deterministically
- Enables CI integration tests with paper executor
- Same code path for paper and live

**Negative:**
- Must maintain two executor implementations
- Paper executor may drift from real behavior

### Alternatives Considered

1. **Env var flag with conditionals** - Rejected: messy, error-prone
2. **Testnet only** - Rejected: Polymarket testnet may not always be available

---

## ADR-006: Effective Price Calculation

### Status
Accepted

### Context
The original pair arb logic used top-of-book prices:
```
profitable if: best_ask_yes + best_ask_no < 0.98
```

This is incorrect because:
- Our order size may exceed top-of-book liquidity
- Actual fill price walks down the book

### Decision
Calculate **effective fill price** by walking the orderbook:

```python
def effective_price_for_size(orderbook, side, size_usdc):
    """Walk book levels to get actual average fill price."""
    remaining = size_usdc
    total_cost = 0
    total_qty = 0
    
    for price, qty in orderbook.levels:
        fill_at_level = min(remaining, qty * price)
        total_cost += fill_at_level
        total_qty += fill_at_level / price
        remaining -= fill_at_level
        if remaining <= 0:
            break
    
    return total_cost / total_qty
```

Profitability check becomes:
```
effective_yes + effective_no < 1.0 - fee - safety_margin
```

### Consequences

**Positive:**
- Accurate profitability assessment
- Avoids unprofitable trades in thin markets
- Safety margin provides additional buffer

**Negative:**
- More computation per check
- Requires up-to-date orderbook depth

### Alternatives Considered

1. **Top-of-book only** - Rejected: incorrect, leads to losses
2. **Fixed slippage estimate** - Rejected: doesn't adapt to actual liquidity

---

## ADR-007: Python for Strategy Layer

### Status
Accepted

### Context
The system could be built in:
- Python (easy, good libs, slower)
- Rust (fast, safe, steeper learning curve)
- Go (fast, simple, less trading libs)

### Decision
Use **Python with asyncio** for the entire stack:
- Strategy layer
- Execution layer
- Adapters

Reasons:
1. `py-clob-client` is the official Polymarket library
2. Rapid iteration more important than microsecond latency
3. Team expertise in Python
4. NumPy/pandas available for analysis

### Consequences

**Positive:**
- Fast development iteration
- Direct use of py-clob-client
- Rich ecosystem for data analysis
- Async support is sufficient for our latency needs

**Negative:**
- Slower than Rust/Go (acceptable for our use case)
- GIL limits true parallelism (mitigated with asyncio)

### Alternatives Considered

1. **Rust for ingestion, Python for strategy** - Rejected: added complexity, FFI overhead
2. **Go throughout** - Rejected: less library support for trading

---

## Template for New ADRs

```markdown
## ADR-XXX: Title

### Status
Proposed | Accepted | Deprecated | Superseded

### Context
What is the issue that we're seeing that is motivating this decision?

### Decision
What is the change that we're proposing and/or doing?

### Consequences
What becomes easier or more difficult to do because of this change?

**Positive:**
- ...

**Negative:**
- ...

### Alternatives Considered
What other options were considered and why were they rejected?
```

---

*ADR Log Version: 1.0 | Last Updated: January 2025*
