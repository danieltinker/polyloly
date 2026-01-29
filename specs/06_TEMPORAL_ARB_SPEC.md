# Specification: Temporal Arbitrage

> Profit from faster information than the market

---

## 1. Purpose

Temporal Arbitrage:
- Exploits **latency advantage** when we know match outcome before market adjusts
- Uses Truth Engine's **high-confidence signals** to predict winner
- Trades **before market prices update** to the new reality
- Has **85-98% win rate** but with directional risk

---

## 2. Edge Model

### 2.1 Latency Window

```
Timeline:
  T0: Match event occurs (e.g., final kill)
  T1: Our data feed receives event
  T2: We process and emit signal
  T3: Market makers receive same info
  T4: Market price adjusts
  
  Edge Window = T4 - T2
  
  Typical values:
  - Tier A source: 500ms - 2s edge
  - Tier B source: 2s - 5s edge
  - Tier C source: No edge (too slow)
```

### 2.2 Implied Probability Model

```python
def truth_to_implied_prob(truth: TruthState, game: str) -> float:
    """
    Convert truth engine state to win probability estimate.
    """
    # Final/confirmed outcomes
    if truth.status == TruthStatus.FINAL:
        return 0.99 if truth.winner_team_id == truth.team_a_id else 0.01
    
    if truth.status == TruthStatus.PENDING_CONFIRM:
        base = 0.85
        confidence_bonus = truth.confidence * 0.10
        if truth.winner_team_id == truth.team_a_id:
            return base + confidence_bonus
        else:
            return 1.0 - (base + confidence_bonus)
    
    # Live score-based probabilities (game-specific)
    if truth.status == TruthStatus.LIVE:
        if game == "dota2":
            return _dota2_live_prob(truth)
        elif game == "lol":
            return _lol_live_prob(truth)
    
    return 0.50  # No edge

def _dota2_live_prob(truth: TruthState) -> float:
    """
    Dota 2 win probability based on map score.
    Best-of-3: first to 2 maps wins.
    """
    score_a, score_b = truth.score_a, truth.score_b
    
    if score_a == 2:
        return 0.99  # A won
    if score_b == 2:
        return 0.01  # B won
    
    if score_a == 1 and score_b == 0:
        return 0.70  # A leads 1-0
    if score_a == 0 and score_b == 1:
        return 0.30  # B leads 1-0
    if score_a == 1 and score_b == 1:
        return 0.50  # Tied 1-1, decider map
    
    return 0.50  # Start of match

def _lol_live_prob(truth: TruthState) -> float:
    """
    LoL win probability based on game/map score.
    Similar logic to Dota 2.
    """
    # Implement based on best-of format
    return _dota2_live_prob(truth)  # Same logic for Bo3
```

---

## 3. Edge Calculation

### 3.1 Core Formula

```python
def calculate_edge(implied_prob: float, market_price: float) -> float:
    """
    Calculate our edge over the market.
    
    implied_prob: Our estimate of win probability
    market_price: Current market price (= implied prob from market)
    
    Positive edge = we think outcome is more likely than market does
    """
    return implied_prob - market_price

# Example:
# We think Team A wins with 0.90 probability
# Market prices Team A at 0.75
# Edge = 0.90 - 0.75 = 0.15 (15% edge)
```

### 3.2 Position Sizing (Kelly Criterion)

```python
def kelly_stake(
    edge: float,
    odds: float,
    fraction: float = 0.25,
    max_stake: float = 50.0,
) -> float:
    """
    Calculate optimal stake using fractional Kelly.
    
    edge: Our edge (e.g., 0.15 = 15%)
    odds: Decimal odds (1/price)
    fraction: Kelly fraction (0.25 = quarter Kelly)
    max_stake: Hard cap
    """
    if edge <= 0:
        return 0.0
    
    # Kelly formula: f* = edge / (odds - 1)
    # But since we're using probability, adjust:
    # f* = (p * odds - 1) / (odds - 1) where p = implied_prob
    
    b = odds - 1  # Net odds
    full_kelly = edge / b if b > 0 else 0
    
    # Apply fraction and cap
    stake = full_kelly * fraction
    return min(max(0, stake), max_stake)

# Example:
# Edge = 0.15, price = 0.75 (odds = 1.33)
# b = 0.33
# full_kelly = 0.15 / 0.33 = 0.45 (45% of bankroll!)
# quarter_kelly = 0.45 * 0.25 = 0.1125 (11.25% of bankroll)
# With $1000 bankroll: $112.50 stake (capped at $50)
```

---

## 4. Decision Logic

### 4.1 Signal Evaluation

```python
def evaluate_temporal_signal(
    truth: TruthState,
    orderbook: OrderBook,
    params: TemporalArbParams,
    latency_tracker: LatencyTracker,
) -> Optional[OrderIntent]:
    """
    Evaluate if we should trade on temporal signal.
    """
    # 1. Check truth confidence
    if truth.confidence < params.min_confidence:
        return None
    
    # 2. Calculate implied probability
    implied_prob = truth_to_implied_prob(truth, truth.game)
    
    # 3. Determine which side to buy
    if implied_prob > 0.5:
        # Bet on team A (YES)
        side = Side.YES
        our_prob = implied_prob
    else:
        # Bet on team B (NO)
        side = Side.NO
        our_prob = 1.0 - implied_prob
    
    # 4. Get market price
    market_price = orderbook.best_ask
    if market_price is None:
        return None
    
    # 5. Calculate edge
    edge = our_prob - market_price
    
    if edge < params.min_edge_threshold:
        return None
    
    # 6. Check if edge window still open
    event_age_ms = time.time() * 1000 - truth.last_event_ms
    if not latency_tracker.is_edge_available(event_age_ms):
        return None
    
    # 7. Price sanity check
    if market_price > params.max_entry_price:
        return None
    
    # 8. Calculate stake
    odds = 1.0 / market_price
    stake = kelly_stake(
        edge=edge,
        odds=odds,
        fraction=params.kelly_fraction,
        max_stake=params.max_single_stake,
    )
    
    if stake < params.min_stake:
        return None
    
    return OrderIntent(
        market_id=truth.match_id,
        side=side,
        price=market_price,
        size=stake,
        strategy="temporal_arb",
        reason=f"edge={edge:.2%}, conf={truth.confidence:.2f}",
        truth_confidence=truth.confidence,
        expected_edge=edge,
    )
```

---

## 5. Latency Tracking

### 5.1 LatencyTracker

```python
@dataclass
class LatencySample:
    event_ts_ms: float
    market_reaction_ts_ms: float
    delta_ms: float

class LatencyTracker:
    """Track how long we have to trade before market reacts."""
    
    def __init__(self, window_size: int = 100):
        self._samples: deque[LatencySample] = deque(maxlen=window_size)
    
    def record(
        self,
        event_ts_ms: float,
        market_reaction_ts_ms: float,
    ):
        """
        Record a latency sample.
        
        event_ts_ms: When the event occurred (from data source)
        market_reaction_ts_ms: When market price moved significantly
        """
        delta = market_reaction_ts_ms - event_ts_ms
        
        self._samples.append(LatencySample(
            event_ts_ms=event_ts_ms,
            market_reaction_ts_ms=market_reaction_ts_ms,
            delta_ms=delta,
        ))
        
        metrics.latency_edge_ms.observe(delta)
    
    @property
    def avg_edge_window_ms(self) -> float:
        """Average time we have to act."""
        if not self._samples:
            return 0.0
        return statistics.mean(s.delta_ms for s in self._samples)
    
    @property
    def p10_edge_window_ms(self) -> float:
        """Conservative (10th percentile) edge window."""
        if len(self._samples) < 10:
            return 0.0
        deltas = sorted(s.delta_ms for s in self._samples)
        idx = len(deltas) // 10
        return deltas[idx]
    
    def is_edge_available(self, event_age_ms: float) -> bool:
        """
        Do we still have time to trade?
        Uses conservative estimate with safety buffer.
        """
        edge_window = self.p10_edge_window_ms
        if edge_window <= 0:
            return False
        
        # 30% safety buffer
        return event_age_ms < edge_window * 0.7
```

### 5.2 Detecting Market Reaction

```python
class MarketReactionDetector:
    """Detect when market has reacted to an event."""
    
    def __init__(self, threshold_bps: float = 100):
        self._threshold_bps = threshold_bps
        self._pre_event_price: Optional[float] = None
        self._event_ts_ms: Optional[float] = None
    
    def on_truth_delta(self, delta: TruthDelta):
        """Record price before significant truth event."""
        if delta.delta_type in ("map", "match_ended"):
            self._pre_event_price = self._current_price
            self._event_ts_ms = delta.timestamp_ms
    
    def on_orderbook_update(
        self,
        orderbook: OrderBook,
        latency_tracker: LatencyTracker,
    ):
        """Check if market has reacted."""
        if self._pre_event_price is None:
            return
        
        current = orderbook.mid_price
        if current is None:
            return
        
        # Calculate price move
        move_bps = abs(current - self._pre_event_price) / self._pre_event_price * 10000
        
        if move_bps >= self._threshold_bps:
            # Market has reacted
            latency_tracker.record(
                event_ts_ms=self._event_ts_ms,
                market_reaction_ts_ms=orderbook.timestamp_ms,
            )
            
            # Reset
            self._pre_event_price = None
            self._event_ts_ms = None
```

---

## 6. Staleness Detection

### 6.1 Source Health Monitoring

```python
@dataclass
class SourceHealth:
    source_id: str
    last_event_at: datetime
    event_count: int = 0
    error_count: int = 0
    
    stale_threshold_ms: int = 30000  # 30 seconds
    
    @property
    def is_stale(self) -> bool:
        age_ms = (datetime.utcnow() - self.last_event_at).total_seconds() * 1000
        return age_ms > self.stale_threshold_ms
    
    @property
    def error_rate(self) -> float:
        if self.event_count == 0:
            return 0.0
        return self.error_count / self.event_count

class SourceHealthMonitor:
    def __init__(self, params: TemporalArbParams):
        self._sources: dict[str, SourceHealth] = {}
        self._params = params
    
    def on_event(self, source_id: str):
        if source_id not in self._sources:
            self._sources[source_id] = SourceHealth(
                source_id=source_id,
                last_event_at=datetime.utcnow(),
            )
        
        health = self._sources[source_id]
        health.last_event_at = datetime.utcnow()
        health.event_count += 1
    
    def on_error(self, source_id: str):
        if source_id in self._sources:
            self._sources[source_id].error_count += 1
    
    def should_halt_temporal(self, match_id: str) -> tuple[bool, str]:
        """
        Check if we should halt temporal strategy for a match.
        Returns (should_halt, reason).
        """
        for source_id, health in self._sources.items():
            if health.is_stale:
                return True, f"source_stale:{source_id}"
            
            if health.error_rate > 0.1:  # 10% error rate
                return True, f"source_errors:{source_id}"
        
        return False, ""
```

---

## 7. Engine Implementation

### 7.1 TemporalArbEngine

```python
class TemporalArbEngine:
    def __init__(
        self,
        market_id: str,
        params: TemporalArbParams,
    ):
        self._market_id = market_id
        self._params = params
        self._latency_tracker = LatencyTracker()
        self._source_monitor = SourceHealthMonitor(params)
        self._reaction_detector = MarketReactionDetector()
        self._current_exposure: float = 0.0
        self._active_signal: Optional[TruthDelta] = None
    
    def on_truth_delta(
        self,
        delta: TruthDelta,
        orderbook: OrderBook,
    ) -> Optional[OrderIntent]:
        """Process truth update and potentially emit order."""
        # Update monitors
        self._source_monitor.on_event(delta.sources[0] if delta.sources else "unknown")
        self._reaction_detector.on_truth_delta(delta)
        
        # Check if we should halt
        should_halt, reason = self._source_monitor.should_halt_temporal(self._market_id)
        if should_halt:
            logger.warning({
                "event_type": "temporal_arb_halted",
                "market_id": self._market_id,
                "reason": reason,
            })
            return None
        
        # Check exposure limit
        if self._current_exposure >= self._params.max_exposure:
            return None
        
        # Evaluate signal
        intent = evaluate_temporal_signal(
            truth=delta,
            orderbook=orderbook,
            params=self._params,
            latency_tracker=self._latency_tracker,
        )
        
        if intent:
            # Adjust for remaining exposure
            remaining = self._params.max_exposure - self._current_exposure
            intent.size = min(intent.size, remaining)
            self._active_signal = delta
        
        return intent
    
    def on_fill(self, fill: UserFill):
        """Update exposure on fill."""
        self._current_exposure += fill.size
        
        logger.info({
            "event_type": "temporal_arb_fill",
            "market_id": self._market_id,
            "side": fill.side.value,
            "size": fill.size,
            "price": fill.price,
            "total_exposure": self._current_exposure,
        })
    
    def on_orderbook_update(self, orderbook: OrderBook):
        """Update latency tracking."""
        self._reaction_detector.on_orderbook_update(
            orderbook,
            self._latency_tracker,
        )
```

---

## 8. Parameters

```python
@dataclass
class TemporalArbParams:
    # Toggle
    enabled: bool = False  # Disabled by default (higher risk)
    
    # Confidence thresholds
    min_confidence: float = 0.90
    
    # Edge requirements
    min_edge_threshold: float = 0.05  # 5% minimum edge
    max_entry_price: float = 0.95     # Don't buy above 95%
    
    # Sizing
    kelly_fraction: float = 0.25      # Quarter Kelly
    min_stake: float = 10.0
    max_single_stake: float = 50.0
    max_exposure: float = 200.0       # Total exposure limit
    
    # Staleness
    stale_threshold_ms: int = 30000   # 30 seconds
    max_source_error_rate: float = 0.10
    
    # Latency
    min_edge_window_ms: float = 500   # Need at least 500ms edge
```

---

## 9. Configuration

```yaml
# config/base.yaml

strategies:
  temporal_arb:
    enabled: false  # Enable only after testing
    
    # Confidence
    min_confidence: 0.90
    
    # Edge
    min_edge_threshold: 0.05
    max_entry_price: 0.95
    
    # Sizing
    kelly_fraction: 0.25
    min_stake: 10.0
    max_single_stake: 50.0
    max_exposure: 200.0
    
    # Health
    stale_threshold_ms: 30000
    max_source_error_rate: 0.10
    min_edge_window_ms: 500
```

---

## 10. Risk Considerations

### 10.1 Key Risks

| Risk | Description | Mitigation |
|------|-------------|------------|
| Stale data | Feed goes down, we trade on old info | Staleness detection, halt on stale |
| False signals | Truth engine wrong | High confidence threshold (0.90) |
| Latency loss | Edge window shrinks | Track latency, adjust thresholds |
| Overexposure | Too much in one direction | Hard exposure cap ($200) |
| Adverse selection | Only filled when wrong | Use limit orders, not market |

### 10.2 Comparison to Pair Arb

| Aspect | Pair Arb | Temporal Arb |
|--------|----------|--------------|
| Win rate | ~100% | 85-98% |
| Risk profile | Hedged | Directional |
| Capital efficiency | Lower | Higher |
| Execution speed | Less critical | Very critical |
| Data requirements | Orderbook only | Orderbook + live events |

---

## 11. Testing Requirements

### 11.1 Unit Tests

- [ ] `truth_to_implied_prob` for all states
- [ ] `calculate_edge` math correct
- [ ] `kelly_stake` sizing correct
- [ ] Latency tracker statistics correct
- [ ] Staleness detection works

### 11.2 Integration Tests

- [ ] Full signal flow: truth → evaluate → intent
- [ ] Exposure tracking across fills
- [ ] Halt on stale source
- [ ] Latency recording on market reaction

### 11.3 Backtests

- [ ] Historical edge window analysis
- [ ] Win rate at different confidence thresholds
- [ ] Optimal Kelly fraction determination

---

*Spec Version: 1.0*
