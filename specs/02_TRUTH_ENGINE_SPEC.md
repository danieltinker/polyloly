# Specification: Truth Engine

> State machine for converting esports events into trading signals

---

## 1. Purpose

The Truth Engine:
- Converts **raw esports provider events** into **high-confidence truth signals**
- Is **deterministic** and **idempotent**
- Supports **multi-source confirmation** for critical events
- Is **tolerant to out-of-order events**

---

## 2. States

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRUTH ENGINE STATES                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PRE_MATCH                                                      │
│      │                                                          │
│      │ MATCH_STARTED                                            │
│      ▼                                                          │
│    LIVE ◀──────────────────┐                                    │
│      │                     │                                    │
│      │ PAUSED              │ RESUMED                            │
│      ▼                     │                                    │
│   PAUSED ──────────────────┘                                    │
│      │                                                          │
│      │ MATCH_ENDED (from LIVE or PAUSED)                        │
│      ▼                                                          │
│  PENDING_CONFIRM ─────────────────────────────────────────────┐ │
│      │                                                        │ │
│      │ (confidence ≥ 0.90 OR timeout ≥ 10s)                   │ │
│      ▼                                                        │ │
│    FINAL                                                      │ │
│      │                                                        │ │
│      │ (contradiction detected)                               │ │
│      └────────────────────────────────────────────────────────┘ │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### State Definitions

| State | Description | Allowed Events |
|-------|-------------|----------------|
| `PRE_MATCH` | Match created but not started | `MATCH_STARTED`, `PAUSED` |
| `LIVE` | Match actively in progress | `SCORE_UPDATE`, `ROUND_ENDED`, `MAP_ENDED`, `PAUSED`, `MATCH_ENDED` |
| `PAUSED` | Match temporarily paused | `RESUMED`, `MATCH_ENDED` |
| `PENDING_CONFIRM` | Match ended, awaiting confirmation | `MATCH_ENDED` (same winner), scoreboard confirm |
| `FINAL` | Outcome confirmed, immutable | `CORRECTION` (optional) |

---

## 3. Transitions

### 3.1 PRE_MATCH Transitions

```python
def on_event_pre_match(self, event: MatchEvent) -> Optional[TruthDelta]:
    match event.event_type:
        case MatchEventType.MATCH_STARTED:
            self.status = TruthStatus.LIVE
            return TruthDelta(delta_type="status", new_value="LIVE")
        
        case MatchEventType.PAUSED:
            # Rare but allowed (pre-match technical issues)
            self.status = TruthStatus.PAUSED
            return TruthDelta(delta_type="status", new_value="PAUSED")
        
        case _:
            # Ignore other events until match starts
            return None
```

### 3.2 LIVE Transitions

```python
def on_event_live(self, event: MatchEvent) -> Optional[TruthDelta]:
    match event.event_type:
        case MatchEventType.PAUSED:
            self.status = TruthStatus.PAUSED
            return TruthDelta(delta_type="status", new_value="PAUSED")
        
        case MatchEventType.SCORE_UPDATE:
            old_score = (self.score_a, self.score_b)
            self.score_a = event.payload["team_a_score"]
            self.score_b = event.payload["team_b_score"]
            return TruthDelta(
                delta_type="score",
                old_value=old_score,
                new_value=(self.score_a, self.score_b)
            )
        
        case MatchEventType.ROUND_ENDED:
            self.round_index = event.payload["round_index"]
            winner = event.payload["winner_team_id"]
            return TruthDelta(delta_type="round", new_value=winner)
        
        case MatchEventType.MAP_ENDED:
            self.map_index = event.payload["map_index"]
            winner = event.payload["winner_team_id"]
            return TruthDelta(delta_type="map", new_value=winner)
        
        case MatchEventType.MATCH_ENDED:
            self._enter_pending_confirm(event)
            return TruthDelta(
                delta_type="status",
                new_value="PENDING_CONFIRM",
                confidence=self.confidence
            )
        
        case _:
            return None
```

### 3.3 PAUSED Transitions

```python
def on_event_paused(self, event: MatchEvent) -> Optional[TruthDelta]:
    match event.event_type:
        case MatchEventType.RESUMED:
            self.status = TruthStatus.LIVE
            return TruthDelta(delta_type="status", new_value="LIVE")
        
        case MatchEventType.MATCH_ENDED:
            self._enter_pending_confirm(event)
            return TruthDelta(delta_type="status", new_value="PENDING_CONFIRM")
        
        case _:
            # Ignore other events while paused
            return None
```

### 3.4 PENDING_CONFIRM Transitions

```python
def on_event_pending(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
    match event.event_type:
        case MatchEventType.MATCH_ENDED:
            if event.payload["winner_team_id"] == self.winner_team_id:
                # Consistent confirmation
                self._add_confirmation(event.source)
                if self._should_finalize():
                    return self._finalize()
            else:
                # Contradiction! Revert to LIVE
                self.status = TruthStatus.LIVE
                self.winner_team_id = None
                self.confidence = 0.0
                self.sources_confirming.clear()
                return TruthDelta(
                    delta_type="status",
                    new_value="LIVE",
                    reason="contradiction"
                )
        
        case _:
            return None
    
    return None

def check_timeout(self, now_ms: int) -> Optional[TruthFinal]:
    """Called periodically to check timeout finalization."""
    if self.status != TruthStatus.PENDING_CONFIRM:
        return None
    
    if self.ended_at_ms and (now_ms - self.ended_at_ms) >= self.params.max_wait_ms:
        return self._finalize()
    
    return None
```

### 3.5 FINAL State

```python
def on_event_final(self, event: MatchEvent) -> None:
    """FINAL state is immutable except for corrections."""
    if event.event_type == MatchEventType.CORRECTION:
        # Log but don't change state - manual review required
        logger.warning(f"Correction received for finalized match: {event}")
    # All other events ignored
```

---

## 4. Confidence Accumulation

### 4.1 Initial Confidence

```python
def _enter_pending_confirm(self, event: MatchEvent):
    self.status = TruthStatus.PENDING_CONFIRM
    self.winner_team_id = event.payload["winner_team_id"]
    self.ended_at_ms = event.timestamp_ms
    self.sources_confirming = {event.source}
    
    # Base confidence depends on source tier
    if event.source_tier == DataSourceTier.TIER_A:
        self.confidence = 0.90  # Tier A alone is nearly sufficient
    else:
        self.confidence = 0.80
```

### 4.2 Accumulation Rules

```python
def _add_confirmation(self, source: str):
    if source in self.sources_confirming:
        return  # Already counted
    
    self.sources_confirming.add(source)
    
    # Boost based on source tier
    source_tier = self._get_source_tier(source)
    
    if source_tier == DataSourceTier.TIER_A:
        self.confidence = min(1.0, self.confidence + 0.10)
    elif source_tier == DataSourceTier.TIER_B:
        self.confidence = min(0.95, self.confidence + 0.08)
    elif source_tier == DataSourceTier.TIER_C:
        self.confidence = min(0.90, self.confidence + 0.03)
```

### 4.3 Finalization Criteria

```python
def _should_finalize(self) -> bool:
    # Criterion 1: High confidence
    if self.confidence >= self.params.confirm_threshold:
        return True
    
    # Criterion 2: Tier-A source confirmed
    tier_a_confirmed = any(
        self._get_source_tier(s) == DataSourceTier.TIER_A
        for s in self.sources_confirming
    )
    if tier_a_confirmed:
        return True
    
    # Criterion 3: Multiple sources agree
    if len(self.sources_confirming) >= self.params.required_sources_for_final:
        return True
    
    return False
```

---

## 5. Event Deduplication & Ordering

### 5.1 Deduplication

```python
def _is_duplicate(self, event: MatchEvent) -> bool:
    # Method 1: Source provides event ID
    if event.source_event_id:
        if event.source_event_id in self.seen_event_ids:
            return True
        self.seen_event_ids.add(event.source_event_id)
        return False
    
    # Method 2: Hash-based dedup
    event_hash = self._hash_event(event)
    if event_hash in self.seen_event_ids:
        return True
    self.seen_event_ids.add(event_hash)
    return False

def _hash_event(self, event: MatchEvent) -> str:
    content = f"{event.event_type}:{event.timestamp_ms}:{json.dumps(event.payload, sort_keys=True)}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

### 5.2 Ordering

```python
def _is_out_of_order(self, event: MatchEvent) -> bool:
    # Method 1: Sequence numbers
    if event.seq is not None and self.last_seq is not None:
        if event.seq <= self.last_seq:
            return True
        self.last_seq = event.seq
        return False
    
    # Method 2: Timestamp-based (with skew tolerance)
    if event.timestamp_ms < self.last_event_ms - self.params.allowed_skew_ms:
        return True
    
    self.last_event_ms = max(self.last_event_ms, event.timestamp_ms)
    return False
```

---

## 6. Public Interface

### 6.1 Main Entry Point

```python
class TruthEngine:
    def __init__(self, match_id: str, team_a_id: str, team_b_id: str, params: TruthEngineParams):
        self._state = TruthState(
            match_id=match_id,
            status=TruthStatus.PRE_MATCH,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
        )
        self._params = params
    
    def on_event(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
        """Process an event and return signal if state changed."""
        if self._is_duplicate(event):
            return None
        
        if self._is_out_of_order(event):
            logger.warning(f"Out of order event dropped: {event}")
            return None
        
        match self._state.status:
            case TruthStatus.PRE_MATCH:
                return self._on_event_pre_match(event)
            case TruthStatus.LIVE:
                return self._on_event_live(event)
            case TruthStatus.PAUSED:
                return self._on_event_paused(event)
            case TruthStatus.PENDING_CONFIRM:
                return self._on_event_pending(event)
            case TruthStatus.FINAL:
                self._on_event_final(event)
                return None
    
    def tick(self, now_ms: int) -> Optional[TruthFinal]:
        """Check for timeout-based finalization."""
        return self._check_timeout(now_ms)
```

### 6.2 Query Methods

```python
    @property
    def status(self) -> TruthStatus:
        return self._state.status
    
    @property
    def confidence(self) -> float:
        return self._state.confidence
    
    @property
    def is_live(self) -> bool:
        return self._state.status == TruthStatus.LIVE
    
    @property
    def is_paused(self) -> bool:
        return self._state.status == TruthStatus.PAUSED
    
    @property
    def is_effectively_final(self) -> bool:
        """True if confidence is high enough to act on."""
        return (
            self._state.status in (TruthStatus.PENDING_CONFIRM, TruthStatus.FINAL)
            and self._state.confidence >= 0.85
        )
    
    @property
    def winner_if_final(self) -> Optional[str]:
        if self.is_effectively_final:
            return self._state.winner_team_id
        return None
    
    def get_state_snapshot(self) -> TruthState:
        """Return immutable copy of current state."""
        return copy.deepcopy(self._state)
```

---

## 7. Configuration

```yaml
# config/base.yaml

truth_engine:
  confirm_threshold: 0.90
  max_wait_ms: 10000
  required_sources_for_final: 2
  allowed_skew_ms: 2000
  
  tier_a_sources:
    - grid
    - official_riot
    - official_valve
  
  tier_b_sources:
    - pandascore
    - opendota
  
  tier_c_sources:
    - liquipedia
```

---

## 8. Testing Requirements

### 8.1 State Transition Tests

- [ ] PRE_MATCH → LIVE on MATCH_STARTED
- [ ] LIVE → PAUSED on PAUSED
- [ ] PAUSED → LIVE on RESUMED
- [ ] LIVE → PENDING_CONFIRM on MATCH_ENDED
- [ ] PENDING_CONFIRM → FINAL on confidence threshold
- [ ] PENDING_CONFIRM → FINAL on timeout
- [ ] PENDING_CONFIRM → LIVE on contradiction

### 8.2 Confidence Tests

- [ ] Initial confidence based on source tier
- [ ] Confidence accumulation from multiple sources
- [ ] Confidence caps (never > 1.0)
- [ ] Tier-A single source finalization

### 8.3 Dedup & Ordering Tests

- [ ] Duplicate event rejection (same ID)
- [ ] Duplicate event rejection (same hash)
- [ ] Out-of-order event rejection
- [ ] Skew tolerance works correctly

### 8.4 Edge Cases

- [ ] Rapid fire events
- [ ] Events from unknown sources
- [ ] Correction events
- [ ] Pause/resume cycles

---

*Spec Version: 1.0*
