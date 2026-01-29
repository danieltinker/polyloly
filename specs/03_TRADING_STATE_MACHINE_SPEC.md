# Specification: Trading State Machine

> Per-market execution state management

---

## 1. Purpose

The Trading State Machine:
- Controls **what actions are allowed** for each market
- Coordinates between **pair arb** and **temporal arb** strategies
- Handles **halt conditions** and **finalization**
- Prevents **late orders** during market resolution

---

## 2. States

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRADING ENGINE STATES                         │
│                       (per market)                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│                          ┌──────────────────────────────────┐   │
│                          │                                  │   │
│  IDLE ───────────────────┼──────────────────────────────────┤   │
│    │                     │                                  │   │
│    │ (pair arb           │ (temporal signal)                │   │
│    │  opportunity)       │                                  │   │
│    ▼                     ▼                                  │   │
│  BUILDING_PAIR      TEMPORAL_ACTIVE                         │   │
│    │                     │                                  │   │
│    │ (guaranteed_pnl>0)  │ (signal expires/filled)          │   │
│    ▼                     │                                  │   │
│  LOCKED_PAIR ◀───────────┘                                  │   │
│    │                                                        │   │
│    │ (truth.is_effectively_final)                           │   │
│    ▼                                                        │   │
│  FINALIZING                                                 │   │
│    │                                                        │   │
│    │ (settlement confirmed)                                 │   │
│    ▼                                                        │   │
│  RESOLVED                                                   │   │
│                                                             │   │
│  ═══════════════════════════════════════════════════════════│   │
│  Any state ──(risk trigger)──▶ HALT                         │   │
│                                                             │   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. State Definitions

### 3.1 IDLE

**Description:** Watching the market, no active strategy.

**Entry Conditions:**
- Initial state for new market
- After RESOLVED (new market cycle)

**Exit Conditions:**
- Pair arb opportunity detected → BUILDING_PAIR
- Temporal signal detected → TEMPORAL_ACTIVE
- Risk trigger → HALT

**Allowed Actions:**
- `watch` (observe orderbook, truth engine)

### 3.2 BUILDING_PAIR

**Description:** Actively accumulating YES/NO pair position.

**Entry Conditions:**
- From IDLE: `pair_cost_avg < pair_cost_cap` and `should_buy_more() == True`

**Exit Conditions:**
- `guaranteed_pnl > 0` → LOCKED_PAIR
- Truth becomes effectively final → FINALIZING
- Risk trigger → HALT
- No opportunity for N ticks → IDLE (configurable)

**Allowed Actions:**
- `buy_yes`
- `buy_no`
- `cancel` (open orders)

**Constraints:**
- Must respect leg imbalance limits
- Must improve guaranteed PnL with each trade

### 3.3 LOCKED_PAIR

**Description:** Guaranteed profit locked, waiting for resolution.

**Entry Conditions:**
- From BUILDING_PAIR: `guaranteed_pnl > 0`
- From TEMPORAL_ACTIVE: position qualifies

**Exit Conditions:**
- Truth becomes effectively final → FINALIZING
- Risk trigger → HALT

**Allowed Actions:**
- `watch` (no new orders)

**Rationale:** Once profit is locked, no reason to trade more. Just wait for resolution.

### 3.4 TEMPORAL_ACTIVE

**Description:** Acting on temporal arbitrage signal.

**Entry Conditions:**
- From IDLE: high-confidence truth signal + market price lag

**Exit Conditions:**
- Signal expires (latency window passed) → IDLE or LOCKED_PAIR
- Position filled → LOCKED_PAIR (if qualifies) or IDLE
- Truth becomes effectively final → FINALIZING
- Risk trigger → HALT

**Allowed Actions:**
- `buy_winner` (buy the winning side)
- `cancel` (if signal expires before fill)

**Constraints:**
- Limited exposure ($200 default)
- Single-direction only (no hedging)

### 3.5 FINALIZING

**Description:** Match outcome known, stopping all activity.

**Entry Conditions:**
- From any active state: `truth.is_effectively_final == True`

**Exit Conditions:**
- Settlement confirmed on-chain → RESOLVED
- Timeout waiting for settlement → RESOLVED (with reconciliation)

**Allowed Actions:**
- `cancel_all` (cancel any open orders)
- No new entries

**Rationale:** Prevent late orders that could execute at wrong prices during resolution.

### 3.6 RESOLVED

**Description:** Market fully settled.

**Entry Conditions:**
- From FINALIZING: settlement confirmed or timeout

**Exit Conditions:**
- None (terminal state for this market cycle)

**Allowed Actions:**
- None

**Post-Resolution:**
- Calculate final P&L
- Update daily P&L tracker
- Archive position data

### 3.7 HALT

**Description:** Trading suspended due to risk trigger.

**Entry Conditions:**
- From any state: per-market or global risk trigger

**Exit Conditions:**
- Manual reset
- Automatic recovery (configurable)
- Market resolved → RESOLVED

**Allowed Actions:**
- `cancel_all` (defensive)
- No new orders

**Triggers:**
- Consecutive order rejects ≥ 3
- Consecutive cancel failures ≥ 3
- Order latency > threshold
- Global kill switch
- Manual halt

---

## 4. Transitions

### 4.1 State Transition Table

| From | Event | Condition | To |
|------|-------|-----------|-----|
| IDLE | OrderBookDelta | `should_start_pair_arb()` | BUILDING_PAIR |
| IDLE | TruthDelta | `should_start_temporal()` | TEMPORAL_ACTIVE |
| IDLE | * | risk_triggered | HALT |
| BUILDING_PAIR | UserFill | `guaranteed_pnl > 0` | LOCKED_PAIR |
| BUILDING_PAIR | TruthFinal | - | FINALIZING |
| BUILDING_PAIR | * | risk_triggered | HALT |
| BUILDING_PAIR | Timeout | no_activity_for_N_ticks | IDLE |
| LOCKED_PAIR | TruthFinal | - | FINALIZING |
| LOCKED_PAIR | * | risk_triggered | HALT |
| TEMPORAL_ACTIVE | UserFill | position_qualifies | LOCKED_PAIR |
| TEMPORAL_ACTIVE | Timeout | signal_expired | IDLE |
| TEMPORAL_ACTIVE | TruthFinal | - | FINALIZING |
| TEMPORAL_ACTIVE | * | risk_triggered | HALT |
| FINALIZING | Settlement | confirmed | RESOLVED |
| FINALIZING | Timeout | settlement_timeout | RESOLVED |
| HALT | ManualReset | - | IDLE |
| HALT | TruthFinal | - | FINALIZING |

### 4.2 Transition Implementation

```python
class TradingEngine:
    def __init__(self, market_id: str, params: TradingEngineParams):
        self._state = TradingState(
            market_id=market_id,
            status=TradingStatus.IDLE,
            position=PairPosition(market_id=market_id),
        )
        self._params = params
    
    def on_event(self, event: Event) -> list[OrderIntent | CancelIntent]:
        """Process event and return any intents."""
        intents = []
        
        # Check for risk triggers first
        if self._check_risk_trigger(event):
            self._transition_to(TradingStatus.HALT)
            intents.extend(self._cancel_all_orders())
            return intents
        
        # Check for finalization
        if isinstance(event, TruthFinal):
            if self._state.status not in (TradingStatus.RESOLVED, TradingStatus.HALT):
                self._transition_to(TradingStatus.FINALIZING)
                intents.extend(self._cancel_all_orders())
                return intents
        
        # State-specific handling
        match self._state.status:
            case TradingStatus.IDLE:
                intents = self._handle_idle(event)
            case TradingStatus.BUILDING_PAIR:
                intents = self._handle_building_pair(event)
            case TradingStatus.LOCKED_PAIR:
                intents = self._handle_locked_pair(event)
            case TradingStatus.TEMPORAL_ACTIVE:
                intents = self._handle_temporal_active(event)
            case TradingStatus.FINALIZING:
                intents = self._handle_finalizing(event)
            case TradingStatus.RESOLVED:
                pass  # No actions
            case TradingStatus.HALT:
                pass  # No actions (except manual reset)
        
        return intents
    
    def _transition_to(self, new_status: TradingStatus):
        old_status = self._state.status
        self._state.status = new_status
        self._state.entered_state_at = datetime.utcnow()
        logger.info({
            "event_type": "state_transition",
            "market_id": self._state.market_id,
            "from": old_status.value,
            "to": new_status.value,
        })
```

---

## 5. Handler Implementations

### 5.1 IDLE Handler

```python
def _handle_idle(self, event: Event) -> list[OrderIntent]:
    intents = []
    
    if isinstance(event, OrderBookDelta):
        # Check for pair arb opportunity
        if self._should_start_pair_arb(event):
            self._transition_to(TradingStatus.BUILDING_PAIR)
            intent = self._generate_pair_arb_intent(event)
            if intent:
                intents.append(intent)
    
    elif isinstance(event, TruthDelta):
        # Check for temporal arb opportunity
        if self._should_start_temporal(event):
            self._transition_to(TradingStatus.TEMPORAL_ACTIVE)
            intent = self._generate_temporal_intent(event)
            if intent:
                intents.append(intent)
    
    return intents
```

### 5.2 BUILDING_PAIR Handler

```python
def _handle_building_pair(self, event: Event) -> list[OrderIntent]:
    intents = []
    
    if isinstance(event, UserFill):
        # Update position
        self._apply_fill(event)
        
        # Check if locked
        if self._state.position.guaranteed_pnl() > 0:
            self._transition_to(TradingStatus.LOCKED_PAIR)
            return []
        
        # Check leg imbalance
        if self._needs_rebalance():
            intent = self._generate_rebalance_intent()
            if intent:
                intents.append(intent)
    
    elif isinstance(event, OrderBookDelta):
        # Continue building if opportunity exists
        intent = self._generate_pair_arb_intent(event)
        if intent:
            intents.append(intent)
        elif self._should_stop_building():
            self._transition_to(TradingStatus.IDLE)
    
    return intents
```

### 5.3 TEMPORAL_ACTIVE Handler

```python
def _handle_temporal_active(self, event: Event) -> list[OrderIntent | CancelIntent]:
    intents = []
    
    if isinstance(event, UserFill):
        self._apply_fill(event)
        
        # Check if we now qualify for locked pair
        if self._state.position.guaranteed_pnl() > 0:
            self._transition_to(TradingStatus.LOCKED_PAIR)
        else:
            self._transition_to(TradingStatus.IDLE)
    
    elif isinstance(event, ClockTick):
        # Check signal expiry
        if self._temporal_signal_expired():
            # Cancel any open orders
            intents.extend(self._cancel_all_orders())
            self._transition_to(TradingStatus.IDLE)
    
    return intents
```

---

## 6. Query Methods

```python
    @property
    def status(self) -> TradingStatus:
        return self._state.status
    
    @property
    def can_place_orders(self) -> bool:
        return self._state.status in (
            TradingStatus.BUILDING_PAIR,
            TradingStatus.TEMPORAL_ACTIVE,
        )
    
    @property
    def is_active(self) -> bool:
        return self._state.status not in (
            TradingStatus.RESOLVED,
            TradingStatus.HALT,
        )
    
    @property
    def position(self) -> PairPosition:
        return self._state.position
    
    def get_allowed_actions(self) -> set[str]:
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
```

---

## 7. Configuration

```yaml
# config/base.yaml

trading_engine:
  # Idle timeout
  idle_after_no_opportunity_ticks: 100
  
  # Temporal expiry
  temporal_signal_ttl_ms: 5000
  
  # Finalization
  settlement_timeout_ms: 60000
  
  # Recovery
  auto_recover_from_halt: false
  halt_recovery_delay_ms: 300000  # 5 minutes
```

---

## 8. Testing Requirements

### 8.1 State Machine Tests

- [ ] IDLE → BUILDING_PAIR on pair arb opportunity
- [ ] IDLE → TEMPORAL_ACTIVE on temporal signal
- [ ] BUILDING_PAIR → LOCKED_PAIR on positive PnL
- [ ] BUILDING_PAIR → FINALIZING on TruthFinal
- [ ] TEMPORAL_ACTIVE → IDLE on signal expiry
- [ ] Any state → HALT on risk trigger
- [ ] FINALIZING → RESOLVED on settlement

### 8.2 Action Validation Tests

- [ ] Only allowed actions accepted per state
- [ ] Rejected actions logged
- [ ] No orders placed in HALT
- [ ] No orders placed in FINALIZING

### 8.3 Integration Tests

- [ ] Full pair arb cycle: IDLE → BUILDING → LOCKED → FINALIZING → RESOLVED
- [ ] Temporal arb cycle: IDLE → TEMPORAL → IDLE
- [ ] Risk halt and recovery
- [ ] Concurrent with Truth Engine

---

*Spec Version: 1.0*
