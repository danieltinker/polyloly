# PolyLOL Architecture - Improvement Suggestions

> Review Date: January 29, 2025  
> Reviewer Notes: This document provides actionable improvements and additions to enhance the PolyLOL architecture for production readiness.

---

## Executive Summary

The architecture is well-structured with clear separation of concerns and thoughtful design decisions. The suggestions below focus on:
1. **Resilience & fault tolerance** gaps
2. **Operational observability** enhancements  
3. **Security hardening** for handling private keys and credentials
4. **Edge cases** in trading logic
5. **Missing components** for production operation

---

## 1. Critical Additions

### 1.1 Circuit Breaker Pattern (Missing)

The current kill switch is reactive. Add a **proactive circuit breaker** for external API calls:

```python
# Suggested: src/bot/circuit_breaker.py

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Failures before opening
    recovery_timeout: float = 30.0      # Seconds before half-open
    half_open_max_calls: int = 3        # Test calls in half-open state

class CircuitBreaker:
    """Wrap adapter calls to prevent cascade failures."""
    
    states: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    
    async def call(self, func: Callable, *args, **kwargs):
        if self.state == "OPEN":
            if time.monotonic() - self.last_failure > self.config.recovery_timeout:
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError()
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
```

**Where to apply:**
- Polymarket REST API calls
- Esports API polling
- WebSocket reconnection logic

---

### 1.2 Secrets Management (Critical Security Gap)

Current `.env` approach is insufficient for production private key handling:

```yaml
# Suggested: config/secrets.yaml (not committed)
# OR use a secrets manager

secrets:
  provider: "vault"  # Options: vault, aws_secrets, gcp_secrets, env
  
  vault:
    addr: "https://vault.internal:8200"
    path: "secret/data/polyloly"
    auth_method: "kubernetes"  # or "token"
  
  aws_secrets:
    secret_name: "polyloly/prod"
    region: "us-east-1"
```

**Recommended approach:**
1. **Development:** Use `.env` with `POLYMARKET_PRIVATE_KEY`
2. **Production:** HashiCorp Vault or cloud secrets manager
3. **Never** log or expose private keys in error messages

Add to `src/bot/secrets.py`:
```python
class SecretsManager:
    """Lazy-load secrets, never hold in memory longer than needed."""
    
    def get_private_key(self) -> str:
        """Fetch, use, and clear from memory."""
        ...
```

---

### 1.3 Graceful Shutdown Handling (Missing)

No mention of handling `SIGTERM`/`SIGINT` for clean shutdown:

```python
# Suggested addition to src/bot/main.py

class GracefulShutdown:
    """Ensure clean exit with position safety."""
    
    async def shutdown(self, signal_received):
        logger.info(f"Shutdown initiated: {signal_received}")
        
        # 1. Stop accepting new signals
        self.strategies_paused = True
        
        # 2. Cancel all open orders (configurable)
        if self.config.cancel_on_shutdown:
            await self.order_manager.cancel_all()
        
        # 3. Flush event log
        await self.storage.flush()
        
        # 4. Close WebSocket connections cleanly
        await self.adapters.close_all()
        
        # 5. Final state snapshot
        await self.storage.write_state_snapshot(self.state)
```

---

### 1.4 Idempotency Keys for Orders (Enhancement)

The architecture mentions "idempotent order placement" but lacks implementation detail:

```python
# src/execution/order_manager.py

@dataclass 
class OrderIntent:
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    # Key = hash(market_id + side + price + size + timestamp_bucket)

class OrderManager:
    def __init__(self):
        self._pending_keys: TTLCache[str, OrderStatus] = TTLCache(maxsize=1000, ttl=300)
    
    async def place(self, intent: OrderIntent) -> OrderResult:
        # Deduplicate within 5-minute window
        if intent.idempotency_key in self._pending_keys:
            return self._pending_keys[intent.idempotency_key]
        
        # Track before sending
        self._pending_keys[intent.idempotency_key] = OrderStatus.PENDING
        
        try:
            result = await self._client.place(intent)
            self._pending_keys[intent.idempotency_key] = result.status
            return result
        except Exception:
            del self._pending_keys[intent.idempotency_key]
            raise
```

---

## 2. Architecture Enhancements

### 2.1 Add Health Check Endpoint

For container orchestration (K8s, Docker Swarm):

```python
# Suggested: src/bot/health.py

from aiohttp import web

class HealthServer:
    """Lightweight HTTP server for health probes."""
    
    async def liveness(self, request) -> web.Response:
        """K8s liveness: is the process running?"""
        return web.json_response({"status": "alive"})
    
    async def readiness(self, request) -> web.Response:
        """K8s readiness: can we accept traffic?"""
        checks = {
            "polymarket_ws": self.adapters.polymarket.is_connected,
            "esports_ws": self.adapters.esports.is_connected,
            "kill_switch": not self.risk.kill_switch_active,
        }
        status = "ready" if all(checks.values()) else "not_ready"
        code = 200 if status == "ready" else 503
        return web.json_response({"status": status, "checks": checks}, status=code)
```

Add to `docker/compose.yaml`:
```yaml
services:
  bot:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health/live"]
      interval: 10s
      timeout: 5s
      retries: 3
```

---

### 2.2 Enhanced Event Bus with Dead Letter Queue

Current bus lacks failure handling:

```python
# Enhancement to src/bot/bus.py

class EventBus:
    def __init__(self):
        self._dlq: asyncio.Queue[FailedEvent] = asyncio.Queue()
        self._retry_policy = RetryPolicy(max_attempts=3, backoff=ExponentialBackoff())
    
    async def publish(self, event: Event) -> None:
        for handler in self._handlers[type(event)]:
            for attempt in range(self._retry_policy.max_attempts):
                try:
                    await asyncio.wait_for(
                        handler(event),
                        timeout=self._handler_timeout
                    )
                    break
                except Exception as e:
                    if attempt == self._retry_policy.max_attempts - 1:
                        await self._dlq.put(FailedEvent(event, e, datetime.utcnow()))
                        logger.error(f"Event sent to DLQ: {event}", exc_info=e)
                    await asyncio.sleep(self._retry_policy.get_delay(attempt))
```

---

### 2.3 Structured Logging Schema

Define a consistent log schema for analysis:

```python
# src/bot/logging.py

LOG_SCHEMA = {
    # Always present
    "ts": "ISO8601 timestamp",
    "level": "DEBUG|INFO|WARNING|ERROR|CRITICAL",
    "component": "truth_engine|order_manager|pair_arb|...",
    "event_type": "string identifier",
    
    # Contextual (when applicable)
    "market_id": "optional",
    "order_id": "optional", 
    "match_id": "optional",
    "latency_ms": "optional float",
    "pnl_usdc": "optional float",
}

# Example structured log
logger.info({
    "event_type": "order_placed",
    "component": "order_manager",
    "market_id": "0x123...",
    "order_id": "abc-123",
    "side": "YES",
    "price": 0.45,
    "size": 25.0,
    "latency_ms": 145.2
})
```

---

### 2.4 Add Metrics Export (Prometheus)

Enhance observability beyond logs:

```python
# Suggested: src/bot/metrics.py

from prometheus_client import Counter, Gauge, Histogram

# Trading metrics
orders_placed = Counter('polyloly_orders_placed_total', 'Orders placed', ['side', 'strategy'])
orders_filled = Counter('polyloly_orders_filled_total', 'Orders filled', ['side', 'strategy'])
order_latency = Histogram('polyloly_order_latency_seconds', 'Order placement latency')

# P&L metrics  
pnl_realized = Gauge('polyloly_pnl_realized_usdc', 'Realized P&L')
pnl_unrealized = Gauge('polyloly_pnl_unrealized_usdc', 'Unrealized P&L')
daily_pnl = Gauge('polyloly_daily_pnl_usdc', 'Daily P&L')

# System metrics
ws_reconnects = Counter('polyloly_ws_reconnects_total', 'WebSocket reconnections', ['feed'])
event_bus_queue_depth = Gauge('polyloly_event_bus_depth', 'Events pending processing')
truth_engine_confidence = Gauge('polyloly_truth_confidence', 'Current truth engine confidence', ['match_id'])
```

---

## 3. Trading Logic Improvements

### 3.1 Pair Arb: Handle Partial Fills

Current logic assumes full fills. Add partial fill handling:

```python
# src/strategies/pair_arb/engine.py

class PairArbEngine:
    async def on_fill(self, fill: UserFill):
        if fill.fill_type == FillType.PARTIAL:
            # Recalculate position imbalance
            imbalance = abs(self.position.qty_yes - self.position.qty_no)
            
            if imbalance > self.params.max_imbalance:
                # Prioritize rebalancing the lagging leg
                lagging_side = "NO" if self.position.qty_yes > self.position.qty_no else "YES"
                await self._emit_rebalance_intent(lagging_side, imbalance)
```

---

### 3.2 Temporal Arb: Add Latency Measurement

Critical for temporal edge:

```python
# src/strategies/temporal_arb/latency.py

class LatencyTracker:
    """Track data freshness vs market reaction."""
    
    def __init__(self, window_size: int = 100):
        self._samples: deque[LatencySample] = deque(maxlen=window_size)
    
    def record(self, event_ts: float, market_reaction_ts: float):
        """Record how long market took to react to event."""
        self._samples.append(LatencySample(
            event_ts=event_ts,
            market_ts=market_reaction_ts,
            delta_ms=(market_reaction_ts - event_ts) * 1000
        ))
    
    @property
    def avg_edge_window_ms(self) -> float:
        """Average time we have to act before market adjusts."""
        if not self._samples:
            return 0.0
        return statistics.mean(s.delta_ms for s in self._samples)
    
    def is_edge_available(self, current_event_age_ms: float) -> bool:
        """Do we still have time to trade?"""
        return current_event_age_ms < self.avg_edge_window_ms * 0.7  # 30% buffer
```

---

### 3.3 Add Slippage Protection

Missing from execution layer:

```python
# src/execution/slippage.py

class SlippageGuard:
    def check(self, intent: PlaceOrderIntent, orderbook: OrderBook) -> SlippageResult:
        if intent.order_type == OrderType.MARKET:
            # Calculate expected execution price
            expected_price = orderbook.simulate_market_order(intent.side, intent.size)
            slippage_bps = abs(expected_price - orderbook.mid_price) / orderbook.mid_price * 10000
            
            if slippage_bps > self.params.max_slippage_bps:
                return SlippageResult(
                    allowed=False,
                    expected_slippage_bps=slippage_bps,
                    recommendation="Convert to limit order or reduce size"
                )
        
        return SlippageResult(allowed=True, expected_slippage_bps=0)
```

---

## 4. Risk Management Additions

### 4.1 Add Market Correlation Limits

Prevent over-exposure to correlated outcomes:

```python
# src/domain/risk.py

@dataclass
class CorrelationRule:
    """Limit exposure to correlated markets."""
    
    # E.g., all matches in the same tournament
    correlation_groups: dict[str, list[str]]  # group_id -> [market_ids]
    max_exposure_per_group: float = 2000.0
    
    def check(self, positions: dict[str, Position], new_intent: PlaceOrderIntent) -> bool:
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

---

### 4.2 Add Volatility-Adjusted Position Sizing

Current sizing is static. Add dynamic adjustment:

```python
# src/domain/risk.py

class VolatilityAdjustedSizer:
    """Reduce size when market is volatile."""
    
    def __init__(self, base_size: float, lookback_ticks: int = 50):
        self._price_history: deque[float] = deque(maxlen=lookback_ticks)
    
    def update(self, mid_price: float):
        self._price_history.append(mid_price)
    
    def get_adjusted_size(self, base_size: float) -> float:
        if len(self._price_history) < 10:
            return base_size * 0.5  # Conservative until we have data
        
        volatility = statistics.stdev(self._price_history)
        volatility_factor = 1.0 / (1.0 + volatility * 10)  # Dampening
        
        return base_size * max(0.25, min(1.0, volatility_factor))
```

---

### 4.3 Add Time-Based Risk Rules

```python
# src/domain/risk.py

@dataclass
class TimeBasedRules:
    """Time-sensitive risk controls."""
    
    # Don't trade in first/last N minutes of market
    market_open_buffer_minutes: int = 5
    market_close_buffer_minutes: int = 10
    
    # Reduce size during high-activity periods
    peak_hours_size_multiplier: float = 0.5
    peak_hours: list[tuple[int, int]] = field(default_factory=lambda: [(14, 16), (20, 22)])  # UTC
    
    # Weekend behavior
    weekend_trading_enabled: bool = False
```

---

## 5. Testing Improvements

### 5.1 Add Property-Based Testing

For math-critical components:

```python
# tests/test_pair_math_property.py

from hypothesis import given, strategies as st

@given(
    qty_yes=st.floats(min_value=0, max_value=10000),
    qty_no=st.floats(min_value=0, max_value=10000),
    cost_yes=st.floats(min_value=0, max_value=10000),
    cost_no=st.floats(min_value=0, max_value=10000),
)
def test_guaranteed_pnl_never_exceeds_investment(qty_yes, qty_no, cost_yes, cost_no):
    """Guaranteed PnL can never exceed total cost (sanity check)."""
    pos = Position(qty_yes=qty_yes, qty_no=qty_no, cost_yes=cost_yes, cost_no=cost_no)
    
    # At resolution, you get back min(qty_yes, qty_no) * (1 - fee)
    # This should never exceed what you paid
    if pos.total_cost() > 0:
        assert pos.guaranteed_pnl() <= pos.total_cost()
```

---

### 5.2 Add Chaos Testing Scripts

```python
# scripts/chaos_test.py

"""Inject failures to test resilience."""

class ChaosMonkey:
    scenarios = [
        "polymarket_ws_disconnect",
        "esports_api_timeout",
        "order_rejection_burst",
        "clock_drift",
        "slow_event_processing",
    ]
    
    async def run_scenario(self, scenario: str, duration_sec: float):
        match scenario:
            case "polymarket_ws_disconnect":
                await self._disconnect_ws("polymarket", duration_sec)
            case "esports_api_timeout":
                await self._inject_latency("esports", timeout_ms=30000)
            # ...
```

---

## 6. Documentation Additions

### 6.1 Add Runbook for Common Issues

```markdown
# Suggested: docs/05_RUNBOOK.md

## Kill Switch Triggered

### Symptoms
- All trading stopped
- Alert: "Kill switch activated"

### Diagnosis
1. Check daily P&L: `GET /metrics | grep daily_pnl`
2. Check error count: `grep "exec_error" logs/bot.jsonl | tail -20`

### Resolution
1. Identify root cause from logs
2. Fix underlying issue
3. Reset kill switch: `curl -X POST localhost:8080/admin/reset-kill-switch`
4. Monitor for 15 minutes before leaving unattended

---

## WebSocket Disconnection Loop

### Symptoms
- Reconnection counter incrementing rapidly
- No orderbook updates

### Diagnosis
1. Check Polymarket status: https://status.polymarket.com
2. Check local network: `curl -I https://clob.polymarket.com`

### Resolution
1. If Polymarket down: wait for recovery
2. If local issue: check firewall, DNS
3. If persistent: increase backoff in config
```

---

### 6.2 Add Decision Log Template

Track why key decisions were made:

```markdown
# Suggested: docs/DECISIONS.md

## ADR-001: Event Bus vs Direct Calls

**Status:** Accepted  
**Date:** 2025-01-XX

**Context:** Need to decouple components for testability and replay.

**Decision:** Use async event bus with typed events.

**Consequences:**
- (+) Components testable in isolation
- (+) Easy replay from event log
- (-) Slight latency overhead
- (-) Debugging requires tracing through bus

---

## ADR-002: SQLite vs PostgreSQL for Local State

**Status:** Proposed

**Context:** Need persistent storage for position state across restarts.

**Options:**
1. SQLite - simple, file-based
2. PostgreSQL - robust, but adds dependency
3. Redis - fast, but not durable by default

**Decision:** TBD
```

---

## 7. Deployment Improvements

### 7.1 Add Multi-Stage Dockerfile

```dockerfile
# docker/Dockerfile (improved)

# Build stage
FROM python:3.11-slim as builder
WORKDIR /build
COPY pyproject.toml .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -e .

# Runtime stage
FROM python:3.11-slim as runtime
WORKDIR /app

# Security: non-root user
RUN useradd --create-home --shell /bin/bash botuser
USER botuser

# Install only runtime deps
COPY --from=builder /wheels /wheels
RUN pip install --no-cache /wheels/*

COPY src/ src/
COPY config/ config/

# Health check
HEALTHCHECK --interval=30s --timeout=10s \
  CMD curl -f http://localhost:8080/health/live || exit 1

ENTRYPOINT ["python", "-m", "src.bot.main"]
```

---

### 7.2 Add Pre-Commit Hooks

```yaml
# .pre-commit-config.yaml

repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]

  - repo: local
    hooks:
      - id: test-critical
        name: Run critical tests
        entry: pytest tests/test_pair_math.py tests/test_truth_engine.py -v
        language: system
        pass_filenames: false
```

---

## 8. Quick Wins (Low Effort, High Value)

| Improvement | Effort | Impact | Location |
|-------------|--------|--------|----------|
| Add `__version__` to package | 5 min | Debugging | `src/__init__.py` |
| Add request IDs to all API calls | 15 min | Tracing | `adapters/*` |
| Add startup config validation | 30 min | Fail fast | `src/bot/settings.py` |
| Add order ID to all log lines | 10 min | Debugging | `execution/*` |
| Add `--dry-run` CLI flag | 20 min | Safety | `src/bot/main.py` |
| Add position snapshot on startup | 30 min | Recovery | `src/bot/main.py` |

---

## Summary Checklist

### Must-Have Before Production
- [ ] Circuit breaker for external calls
- [ ] Secrets management (not plain `.env`)
- [ ] Graceful shutdown handler
- [ ] Health check endpoint
- [ ] Partial fill handling
- [ ] Slippage protection

### Should-Have
- [ ] Dead letter queue for event bus
- [ ] Prometheus metrics
- [ ] Latency tracking for temporal arb
- [ ] Correlation-based position limits
- [ ] Runbook documentation

### Nice-to-Have
- [ ] Property-based testing
- [ ] Chaos testing scripts
- [ ] Decision log (ADRs)
- [ ] Multi-stage Docker build

---

*Improvement Suggestions v1.0 | January 2025*
