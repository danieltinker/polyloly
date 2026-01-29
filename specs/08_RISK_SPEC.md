# Specification: Risk Management

> Layered risk controls with circuit breakers and kill switches

---

## 1. Purpose

Risk Management:
- **Prevents** catastrophic losses through layered controls
- **Limits** exposure per market, per correlation group, and globally
- **Detects** anomalies and halts trading automatically
- **Provides** manual override capabilities

---

## 2. Risk Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                      RISK MANAGEMENT LAYERS                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1: PRE-TRADE CHECKS (per order)                          │
│  ├── Order size limits                                          │
│  ├── Slippage check                                             │
│  ├── Leg imbalance check                                        │
│  └── Market-specific limits                                      │
│                                                                  │
│  Layer 2: POSITION LIMITS (per market)                          │
│  ├── Max position per market                                    │
│  ├── Max open orders per market                                 │
│  └── Per-market circuit breaker                                  │
│                                                                  │
│  Layer 3: CORRELATION LIMITS (grouped markets)                  │
│  ├── Max exposure per correlation group                         │
│  └── Tournament/event grouping                                   │
│                                                                  │
│  Layer 4: GLOBAL LIMITS                                         │
│  ├── Max total exposure                                         │
│  ├── Max daily loss                                             │
│  ├── Global kill switch                                          │
│  └── Connectivity requirements                                   │
│                                                                  │
│  Layer 5: TIME-BASED RULES                                      │
│  ├── Market open/close buffers                                  │
│  ├── Peak hours size reduction                                  │
│  └── Weekend trading toggle                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Risk Parameters

### 3.1 Configuration

```python
@dataclass
class RiskParams:
    # Layer 1: Per-order limits
    min_order_size: float = 5.0
    max_single_order: float = 100.0
    max_slippage_bps: float = 50.0
    max_leg_imbalance_usdc: float = 100.0
    max_leg_imbalance_shares: float = 50.0
    
    # Layer 2: Per-market limits
    max_position_per_market: float = 1500.0
    max_open_orders_per_market: int = 5
    max_consecutive_rejects: int = 3
    max_cancel_failures: int = 3
    max_order_latency_ms: float = 5000.0
    
    # Layer 3: Correlation limits
    max_exposure_per_correlation_group: float = 2000.0
    
    # Layer 4: Global limits
    max_total_exposure: float = 5000.0
    max_daily_loss: float = 200.0
    max_consecutive_global_errors: int = 5
    
    # Layer 5: Time-based
    market_open_buffer_minutes: int = 5
    market_close_buffer_minutes: int = 10
    peak_hours_size_multiplier: float = 0.5
    peak_hours_utc: list[tuple[int, int]] = field(
        default_factory=lambda: [(14, 16), (20, 22)]
    )
    weekend_trading_enabled: bool = False
```

---

## 4. Risk Manager Implementation

### 4.1 Core Class

```python
class RiskManager:
    def __init__(self, params: RiskParams):
        self._params = params
        self._state = GlobalRiskState()
        self._market_states: dict[str, MarketRiskState] = {}
        self._correlation_groups: dict[str, set[str]] = {}  # group_id -> market_ids
    
    def check_order(self, intent: OrderIntent) -> RiskCheckResult:
        """
        Run all risk checks on an order intent.
        Returns approval/rejection with reason.
        """
        # Layer 5: Time-based (check first, quick reject)
        result = self._check_time_rules(intent)
        if not result.approved:
            return result
        
        # Layer 4: Global limits
        result = self._check_global_limits(intent)
        if not result.approved:
            return result
        
        # Layer 3: Correlation limits
        result = self._check_correlation_limits(intent)
        if not result.approved:
            return result
        
        # Layer 2: Market limits
        result = self._check_market_limits(intent)
        if not result.approved:
            return result
        
        # Layer 1: Order limits
        result = self._check_order_limits(intent)
        if not result.approved:
            return result
        
        return RiskCheckResult(approved=True)
```

### 4.2 Layer 1: Order Checks

```python
    def _check_order_limits(self, intent: OrderIntent) -> RiskCheckResult:
        """Check per-order limits."""
        # Min size
        if intent.size < self._params.min_order_size:
            return RiskCheckResult(
                approved=False,
                reason=f"Below min size: {intent.size} < {self._params.min_order_size}",
            )
        
        # Max size (with potential adjustment)
        if intent.size > self._params.max_single_order:
            # Cap instead of reject
            return RiskCheckResult(
                approved=True,
                adjusted_size=self._params.max_single_order,
                reason=f"Capped from {intent.size} to {self._params.max_single_order}",
            )
        
        return RiskCheckResult(approved=True)
```

### 4.3 Layer 2: Market Checks

```python
    def _check_market_limits(self, intent: OrderIntent) -> RiskCheckResult:
        """Check per-market limits."""
        market_state = self._get_market_state(intent.market_id)
        
        # Circuit breaker
        if market_state.circuit_open:
            return RiskCheckResult(
                approved=False,
                reason=f"Market circuit breaker open: {market_state.circuit_reason}",
            )
        
        # Max position
        current_exposure = market_state.total_exposure
        if current_exposure + intent.size > self._params.max_position_per_market:
            remaining = self._params.max_position_per_market - current_exposure
            if remaining < self._params.min_order_size:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Max position reached: {current_exposure}",
                )
            return RiskCheckResult(
                approved=True,
                adjusted_size=remaining,
            )
        
        # Max open orders
        if market_state.open_order_count >= self._params.max_open_orders_per_market:
            return RiskCheckResult(
                approved=False,
                reason=f"Max open orders: {market_state.open_order_count}",
            )
        
        return RiskCheckResult(approved=True)
```

### 4.4 Layer 3: Correlation Checks

```python
    def _check_correlation_limits(self, intent: OrderIntent) -> RiskCheckResult:
        """Check correlation group limits."""
        group_id = self._get_correlation_group(intent.market_id)
        if not group_id:
            return RiskCheckResult(approved=True)
        
        # Sum exposure across group
        group_exposure = 0.0
        for market_id in self._correlation_groups.get(group_id, set()):
            if market_id in self._market_states:
                group_exposure += self._market_states[market_id].total_exposure
        
        max_group = self._params.max_exposure_per_correlation_group
        if group_exposure + intent.size > max_group:
            remaining = max_group - group_exposure
            if remaining < self._params.min_order_size:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Correlation group limit: {group_id} at {group_exposure}",
                )
            return RiskCheckResult(
                approved=True,
                adjusted_size=remaining,
            )
        
        return RiskCheckResult(approved=True)
```

### 4.5 Layer 4: Global Checks

```python
    def _check_global_limits(self, intent: OrderIntent) -> RiskCheckResult:
        """Check global limits."""
        # Kill switch
        if self._state.kill_switch_active:
            return RiskCheckResult(
                approved=False,
                reason=f"Kill switch active: {self._state.kill_switch_reason}",
            )
        
        # Daily loss
        if self._state.daily_pnl < -self._params.max_daily_loss:
            self._activate_kill_switch("daily_loss_exceeded")
            return RiskCheckResult(
                approved=False,
                reason="Daily loss limit exceeded",
            )
        
        # Total exposure
        total = sum(s.total_exposure for s in self._market_states.values())
        if total + intent.size > self._params.max_total_exposure:
            remaining = self._params.max_total_exposure - total
            if remaining < self._params.min_order_size:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Max total exposure: {total}",
                )
            return RiskCheckResult(
                approved=True,
                adjusted_size=remaining,
            )
        
        return RiskCheckResult(approved=True)
```

### 4.6 Layer 5: Time-Based Checks

```python
    def _check_time_rules(self, intent: OrderIntent) -> RiskCheckResult:
        """Check time-based rules."""
        now = datetime.utcnow()
        
        # Weekend check
        if now.weekday() >= 5 and not self._params.weekend_trading_enabled:
            return RiskCheckResult(
                approved=False,
                reason="Weekend trading disabled",
            )
        
        # Peak hours size reduction
        hour = now.hour
        for start, end in self._params.peak_hours_utc:
            if start <= hour < end:
                # Reduce max size during peak
                adjusted_max = self._params.max_single_order * self._params.peak_hours_size_multiplier
                if intent.size > adjusted_max:
                    return RiskCheckResult(
                        approved=True,
                        adjusted_size=adjusted_max,
                        reason=f"Peak hours size reduction",
                    )
        
        return RiskCheckResult(approved=True)
```

---

## 5. Circuit Breakers

### 5.1 Per-Market Circuit Breaker

```python
@dataclass
class MarketCircuitBreaker:
    market_id: str
    state: CircuitState = CircuitState.CLOSED
    
    # Counters
    consecutive_rejects: int = 0
    consecutive_cancel_failures: int = 0
    recent_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=10))
    
    # Trip thresholds
    max_rejects: int = 3
    max_cancel_failures: int = 3
    max_latency_ms: float = 5000
    
    # Recovery
    opened_at: Optional[datetime] = None
    recovery_delay_sec: float = 300  # 5 minutes
    
    @property
    def circuit_open(self) -> bool:
        return self.state == CircuitState.OPEN
    
    @property
    def circuit_reason(self) -> Optional[str]:
        if self.consecutive_rejects >= self.max_rejects:
            return f"consecutive_rejects:{self.consecutive_rejects}"
        if self.consecutive_cancel_failures >= self.max_cancel_failures:
            return f"cancel_failures:{self.consecutive_cancel_failures}"
        if self.recent_latencies and max(self.recent_latencies) > self.max_latency_ms:
            return f"high_latency:{max(self.recent_latencies):.0f}ms"
        return None
    
    def on_reject(self):
        self.consecutive_rejects += 1
        self._check_trip()
    
    def on_success(self):
        self.consecutive_rejects = 0
    
    def on_cancel_failure(self):
        self.consecutive_cancel_failures += 1
        self._check_trip()
    
    def on_cancel_success(self):
        self.consecutive_cancel_failures = 0
    
    def on_latency(self, latency_ms: float):
        self.recent_latencies.append(latency_ms)
        self._check_trip()
    
    def _check_trip(self):
        if self.circuit_reason:
            self.state = CircuitState.OPEN
            self.opened_at = datetime.utcnow()
            
            logger.warning({
                "event_type": "circuit_breaker_trip",
                "market_id": self.market_id,
                "reason": self.circuit_reason,
            })
            
            metrics.circuit_breaker_trips.labels(
                market=self.market_id,
                reason=self.circuit_reason,
            ).inc()
    
    def check_recovery(self) -> bool:
        if self.state != CircuitState.OPEN:
            return False
        
        if self.opened_at is None:
            return False
        
        elapsed = (datetime.utcnow() - self.opened_at).total_seconds()
        if elapsed >= self.recovery_delay_sec:
            self.state = CircuitState.HALF_OPEN
            return True
        
        return False
```

### 5.2 Global Kill Switch

```python
class KillSwitch:
    def __init__(self, params: RiskParams):
        self._params = params
        self._active = False
        self._reason: Optional[str] = None
        self._activated_at: Optional[datetime] = None
    
    @property
    def is_active(self) -> bool:
        return self._active
    
    @property
    def reason(self) -> Optional[str]:
        return self._reason
    
    def activate(self, reason: str):
        if self._active:
            return  # Already active
        
        self._active = True
        self._reason = reason
        self._activated_at = datetime.utcnow()
        
        logger.critical({
            "event_type": "kill_switch_activated",
            "reason": reason,
        })
        
        metrics.kill_switch_triggers.labels(reason=reason).inc()
        
        # Send alert
        # await alert_service.send_critical(f"KILL SWITCH: {reason}")
    
    def reset(self, admin_token: str):
        """Reset requires admin token for safety."""
        if not self._verify_admin(admin_token):
            raise UnauthorizedError("Invalid admin token")
        
        self._active = False
        self._reason = None
        
        logger.info({
            "event_type": "kill_switch_reset",
        })
    
    def check(self, state: GlobalRiskState) -> bool:
        """Check if kill switch should trip."""
        # Daily loss
        if state.daily_pnl < -self._params.max_daily_loss:
            self.activate("daily_loss_exceeded")
            return True
        
        # Consecutive errors
        if state.consecutive_errors > self._params.max_consecutive_global_errors:
            self.activate("consecutive_errors")
            return True
        
        # Connectivity
        if not state.polymarket_connected:
            self.activate("polymarket_disconnected")
            return True
        
        if not state.esports_connected:
            self.activate("esports_disconnected")
            return True
        
        # Manual
        if state.manual_halt:
            self.activate("manual_halt")
            return True
        
        return False
```

---

## 6. Volatility-Adjusted Sizing

```python
class VolatilityAdjustedSizer:
    """Reduce position size during high volatility."""
    
    def __init__(self, lookback_ticks: int = 50):
        self._price_history: dict[str, deque[float]] = {}
        self._lookback = lookback_ticks
    
    def update(self, market_id: str, mid_price: float):
        if market_id not in self._price_history:
            self._price_history[market_id] = deque(maxlen=self._lookback)
        
        self._price_history[market_id].append(mid_price)
    
    def get_size_multiplier(self, market_id: str) -> float:
        """
        Returns multiplier in [0.25, 1.0].
        Higher volatility = lower multiplier.
        """
        history = self._price_history.get(market_id)
        
        if not history or len(history) < 10:
            return 0.5  # Conservative until we have data
        
        # Calculate volatility (std dev of price changes)
        prices = list(history)
        changes = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
        
        if not changes:
            return 1.0
        
        volatility = statistics.stdev(changes) if len(changes) > 1 else 0
        
        # Map volatility to multiplier
        # High vol (>0.05) -> 0.25
        # Low vol (<0.01) -> 1.0
        factor = 1.0 / (1.0 + volatility * 20)
        
        return max(0.25, min(1.0, factor))
    
    def adjust_size(self, market_id: str, base_size: float) -> float:
        multiplier = self.get_size_multiplier(market_id)
        return base_size * multiplier
```

---

## 7. P&L Tracking

```python
class PnLTracker:
    """Track realized and unrealized P&L."""
    
    def __init__(self):
        self._realized: float = 0.0
        self._unrealized: float = 0.0
        self._daily_realized: float = 0.0
        self._daily_start: date = date.today()
        self._trades: list[Trade] = []
    
    def on_fill(self, fill: Fill, position: PairPosition):
        """Record fill and update unrealized."""
        self._trades.append(Trade(
            timestamp=datetime.utcnow(),
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
        ))
    
    def on_settlement(self, market_id: str, winner: Side, position: PairPosition):
        """Record settlement and realize P&L."""
        pnl = position.guaranteed_pnl()
        
        self._realized += pnl
        self._daily_realized += pnl
        
        logger.info({
            "event_type": "pnl_realized",
            "market_id": market_id,
            "winner": winner.value,
            "pnl": pnl,
            "total_realized": self._realized,
            "daily_realized": self._daily_realized,
        })
        
        metrics.pnl_realized.set(self._realized)
        metrics.daily_pnl.set(self._daily_realized)
    
    def check_daily_reset(self):
        """Reset daily P&L at midnight UTC."""
        today = date.today()
        if today != self._daily_start:
            logger.info({
                "event_type": "daily_pnl_reset",
                "previous_day": self._daily_start.isoformat(),
                "previous_pnl": self._daily_realized,
            })
            
            self._daily_realized = 0.0
            self._daily_start = today
    
    @property
    def daily_pnl(self) -> float:
        self.check_daily_reset()
        return self._daily_realized
    
    @property
    def total_pnl(self) -> float:
        return self._realized + self._unrealized
```

---

## 8. Configuration

```yaml
# config/base.yaml

risk:
  # Per-order
  min_order_size: 5.0
  max_single_order: 100.0
  max_slippage_bps: 50
  max_leg_imbalance_usdc: 100.0
  
  # Per-market
  max_position_per_market: 1500.0
  max_open_orders_per_market: 5
  max_consecutive_rejects: 3
  max_cancel_failures: 3
  max_order_latency_ms: 5000
  
  # Correlation
  max_exposure_per_correlation_group: 2000.0
  
  # Global
  max_total_exposure: 5000.0
  max_daily_loss: 200.0
  max_consecutive_global_errors: 5
  
  # Time-based
  market_open_buffer_minutes: 5
  market_close_buffer_minutes: 10
  peak_hours_size_multiplier: 0.5
  peak_hours_utc:
    - [14, 16]
    - [20, 22]
  weekend_trading_enabled: false
  
  # Circuit breaker recovery
  circuit_breaker_recovery_sec: 300
  
  # Volatility
  volatility_lookback_ticks: 50
```

---

## 9. Testing Requirements

### 9.1 Unit Tests

- [ ] Order size limits enforced
- [ ] Market position limits enforced
- [ ] Correlation limits enforced
- [ ] Global exposure limits enforced
- [ ] Kill switch triggers correctly
- [ ] Circuit breaker trips and recovers

### 9.2 Integration Tests

- [ ] Full order flow with risk checks
- [ ] P&L tracking across fills
- [ ] Daily reset at midnight
- [ ] Volatility adjustment responds

### 9.3 Stress Tests

- [ ] High order volume
- [ ] Rapid price changes
- [ ] Multiple circuit breaker trips
- [ ] Kill switch activation/reset

---

*Spec Version: 1.0*
