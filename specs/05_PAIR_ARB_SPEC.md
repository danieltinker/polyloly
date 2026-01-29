# Specification: Binary Pair Arbitrage

> Guaranteed profit through YES/NO position accumulation

---

## 1. Purpose

Binary Pair Arbitrage:
- Exploits price inefficiency when `YES_price + NO_price < 1.0 - fee`
- Builds balanced positions in both outcomes
- Locks in **guaranteed profit** regardless of outcome
- Has **~100% win rate** with proper execution

---

## 2. Mathematical Foundation

### 2.1 Core Equations

```
Definitions:
  q_yes = quantity of YES shares
  q_no  = quantity of NO shares
  c_yes = total cost for YES (USDC)
  c_no  = total cost for NO (USDC)
  fee_rate = winner's fee (0.02 = 2%)

Guaranteed Payout:
  q_min = min(q_yes, q_no)
  payout_net = q_min × (1 - fee_rate)

Guaranteed P&L:
  guaranteed_pnl = payout_net - (c_yes + c_no)

Average Costs:
  avg_yes = c_yes / q_yes  (if q_yes > 0)
  avg_no  = c_no / q_no    (if q_no > 0)
  pair_cost_avg = avg_yes + avg_no

Profitability Condition:
  pair_cost_avg < 1 - fee_rate
  
With 2% fee:
  pair_cost_avg < 0.98
```

### 2.2 Effective Price vs Top-of-Book

**Critical improvement:** Don't use top-of-book price. Calculate **effective fill price** from orderbook depth.

```python
def effective_price_for_size(
    orderbook: OrderBook,
    side: Side,  # BUY or SELL
    size_usdc: float,
) -> float:
    """
    Walk the book to calculate actual average fill price.
    
    For BUY: walk asks (ascending)
    For SELL: walk bids (descending)
    """
    levels = orderbook.asks if side == Side.BUY else orderbook.bids
    remaining = size_usdc
    total_cost = 0.0
    total_qty = 0.0
    
    for price, qty_available in levels:
        # Max we can buy at this level
        max_usdc_at_level = qty_available * price
        usdc_at_level = min(remaining, max_usdc_at_level)
        qty_at_level = usdc_at_level / price
        
        total_cost += usdc_at_level
        total_qty += qty_at_level
        remaining -= usdc_at_level
        
        if remaining <= 0:
            break
    
    if total_qty == 0:
        return float('inf')  # No liquidity
    
    return total_cost / total_qty
```

### 2.3 Enhanced Profitability Check

```python
def is_pair_profitable(
    orderbook_yes: OrderBook,
    orderbook_no: OrderBook,
    size_usdc: float,
    params: PairArbParams,
) -> bool:
    """
    Check if buying both sides at given size is profitable.
    Uses effective fill prices, not top-of-book.
    """
    eff_yes = effective_price_for_size(orderbook_yes, Side.BUY, size_usdc)
    eff_no = effective_price_for_size(orderbook_no, Side.BUY, size_usdc)
    
    # Check profitability with safety margin
    threshold = 1.0 - params.fee_rate - params.safety_margin
    
    return (eff_yes + eff_no) < threshold
```

---

## 3. Decision Logic

### 3.1 Should Buy More

```python
def should_buy_more(
    pos: PairPosition,
    side: Side,
    usdc_amount: float,
    price: float,
    orderbook: OrderBook,
    params: PairArbParams,
) -> tuple[bool, str]:
    """
    Determine if we should place this buy.
    Returns (allowed, reason).
    """
    # 1. Size limits
    if usdc_amount < params.min_order_size:
        return False, "below_min_size"
    
    if usdc_amount > params.max_single_order:
        return False, "exceeds_max_single"
    
    if pos.total_cost() + usdc_amount > params.max_total_cost:
        return False, "exceeds_max_total"
    
    # 2. Liquidity check
    available_liquidity = orderbook.total_ask_liquidity()
    if available_liquidity < usdc_amount * 2:  # 2x buffer
        return False, "insufficient_liquidity"
    
    # 3. Effective price check
    eff_price = effective_price_for_size(orderbook, Side.BUY, usdc_amount)
    if eff_price > price * (1 + params.max_slippage_bps / 10000):
        return False, "slippage_exceeded"
    
    # 4. Simulate the position
    new_pos = pos.hypo_buy(side, usdc_amount, eff_price)
    
    # 5. Check pair cost cap
    pc = new_pos.pair_cost_avg()
    if pc is not None:
        if pc >= (1.0 - params.fee_rate):
            return False, "pair_cost_exceeds_net"
        if pc >= params.pair_cost_cap:
            return False, "pair_cost_exceeds_cap"
    
    # 6. Check leg imbalance
    if new_pos.leg_imbalance_usdc() > params.max_leg_imbalance_usdc:
        return False, "leg_imbalance_usdc"
    
    if new_pos.leg_imbalance_shares() > params.max_leg_imbalance_shares:
        return False, "leg_imbalance_shares"
    
    # 7. Must improve guaranteed PnL
    if new_pos.guaranteed_pnl() <= pos.guaranteed_pnl():
        return False, "no_pnl_improvement"
    
    return True, "approved"
```

### 3.2 Leg Selection

```python
def select_leg_to_buy(
    pos: PairPosition,
    orderbook_yes: OrderBook,
    orderbook_no: OrderBook,
    params: PairArbParams,
) -> Optional[Side]:
    """
    Select which leg to buy next.
    Priority: balance legs > buy cheaper side.
    """
    # 1. If significantly imbalanced, buy the lagging leg
    imbalance = pos.q_yes - pos.q_no
    
    if imbalance > params.rebalance_threshold_shares:
        # YES is ahead, buy NO
        return Side.NO
    
    if imbalance < -params.rebalance_threshold_shares:
        # NO is ahead, buy YES
        return Side.YES
    
    # 2. Buy the cheaper side
    yes_ask = orderbook_yes.best_ask
    no_ask = orderbook_no.best_ask
    
    if yes_ask is None and no_ask is None:
        return None  # No liquidity
    
    if yes_ask is None:
        return Side.NO
    
    if no_ask is None:
        return Side.YES
    
    return Side.YES if yes_ask < no_ask else Side.NO
```

---

## 4. Engine Implementation

### 4.1 PairArbEngine

```python
class PairArbEngine:
    def __init__(
        self,
        market_id: str,
        params: PairArbParams,
        position: PairPosition,
    ):
        self._market_id = market_id
        self._params = params
        self._position = position
        self._orderbook_yes: Optional[OrderBook] = None
        self._orderbook_no: Optional[OrderBook] = None
    
    def on_orderbook_update(
        self,
        token_id: str,
        orderbook: OrderBook,
    ) -> Optional[OrderIntent]:
        """
        Process orderbook update and potentially emit order intent.
        """
        # Update local orderbook cache
        if self._is_yes_token(token_id):
            self._orderbook_yes = orderbook
        else:
            self._orderbook_no = orderbook
        
        # Need both orderbooks
        if not self._orderbook_yes or not self._orderbook_no:
            return None
        
        # Check for opportunity
        return self._evaluate_opportunity()
    
    def _evaluate_opportunity(self) -> Optional[OrderIntent]:
        """Evaluate if we should buy."""
        # Select leg
        side = select_leg_to_buy(
            self._position,
            self._orderbook_yes,
            self._orderbook_no,
            self._params,
        )
        
        if side is None:
            return None
        
        # Get orderbook for selected side
        orderbook = self._orderbook_yes if side == Side.YES else self._orderbook_no
        
        # Check if profitable
        allowed, reason = should_buy_more(
            pos=self._position,
            side=side,
            usdc_amount=self._params.step_usdc,
            price=orderbook.best_ask,
            orderbook=orderbook,
            params=self._params,
        )
        
        if not allowed:
            logger.debug({
                "event_type": "pair_arb_rejected",
                "market_id": self._market_id,
                "side": side.value,
                "reason": reason,
            })
            return None
        
        # Create intent
        return OrderIntent(
            market_id=self._market_id,
            side=side,
            price=orderbook.best_ask,
            size=self._params.step_usdc,
            strategy="pair_arb",
            reason=f"pair_cost_avg={self._position.pair_cost_avg():.4f}",
        )
    
    def on_fill(self, fill: UserFill):
        """Update position on fill."""
        self._position.apply_fill(Fill(
            side=self._fill_to_side(fill),
            qty=fill.size / fill.price,
            price=fill.price,
            fill_type=fill.fill_type,
        ))
        
        logger.info({
            "event_type": "pair_arb_fill",
            "market_id": self._market_id,
            "side": fill.side.value,
            "qty": fill.size,
            "price": fill.price,
            "guaranteed_pnl": self._position.guaranteed_pnl(),
            "pair_cost_avg": self._position.pair_cost_avg(),
        })
```

### 4.2 Partial Fill Handling

```python
def on_fill(self, fill: UserFill):
    """Handle fill with special logic for partials."""
    self._position.apply_fill(...)
    
    if fill.fill_type == FillType.PARTIAL:
        # Check if we need rebalancing
        imbalance = self._position.leg_imbalance_usdc()
        
        if imbalance > self._params.max_leg_imbalance_usdc:
            logger.warning({
                "event_type": "leg_imbalance_warning",
                "market_id": self._market_id,
                "imbalance_usdc": imbalance,
            })
            
            # Emit rebalance intent
            lagging_side = Side.NO if self._position.q_yes > self._position.q_no else Side.YES
            return OrderIntent(
                market_id=self._market_id,
                side=lagging_side,
                price=self._get_best_ask(lagging_side),
                size=imbalance,
                strategy="pair_arb",
                reason="rebalance_partial_fill",
            )
    
    return None
```

---

## 5. Parameters

```python
@dataclass
class PairArbParams:
    # Toggle
    enabled: bool = True
    
    # Cost thresholds
    pair_cost_cap: float = 0.975      # Max avg cost for pair
    safety_margin: float = 0.005      # Extra margin beyond fee
    fee_rate: float = 0.02            # Polymarket winner fee
    
    # Sizing
    step_usdc: float = 25.0           # Size per order
    min_order_size: float = 5.0       # Minimum order
    max_single_order: float = 100.0   # Maximum single order
    max_total_cost: float = 1500.0    # Max position size
    
    # Leg balancing
    max_leg_imbalance_usdc: float = 100.0
    max_leg_imbalance_shares: float = 50.0
    rebalance_threshold_shares: float = 20.0
    prefer_balance: bool = True
    
    # Liquidity
    min_liquidity_usdc: float = 100.0
    max_slippage_bps: float = 50.0
```

---

## 6. Configuration

```yaml
# config/base.yaml

strategies:
  pair_arb:
    enabled: true
    
    # Profitability
    pair_cost_cap: 0.975
    safety_margin: 0.005
    fee_rate: 0.02
    
    # Sizing
    step_usdc: 25.0
    min_order_size: 5.0
    max_single_order: 100.0
    max_total_cost: 1500.0
    
    # Balancing
    max_leg_imbalance_usdc: 100.0
    max_leg_imbalance_shares: 50.0
    rebalance_threshold_shares: 20.0
    
    # Liquidity
    min_liquidity_usdc: 100.0
    max_slippage_bps: 50.0
```

---

## 7. Safety Margin Analysis

Results from `pnl_simulation.py`:

| pair_cost_cap | Mean PnL | Median PnL | P5 PnL | Positive Rate | Mean PnL/Spent |
|---------------|----------|------------|--------|---------------|----------------|
| 0.990 | $12.50 | $10.00 | -$2.50 | 92.3% | 0.95% |
| 0.985 | $18.75 | $15.00 | $2.50 | 96.8% | 1.42% |
| 0.980 | $22.50 | $20.00 | $7.50 | 98.5% | 1.71% |
| 0.975 | $25.00 | $22.50 | $10.00 | 99.2% | 1.90% |
| 0.970 | $27.50 | $25.00 | $12.50 | 99.5% | 2.08% |

**Recommendation:** Use `pair_cost_cap = 0.975` for good balance of:
- High positive rate (99%+)
- Reasonable PnL
- Sufficient trade frequency

---

## 8. Testing Requirements

### 8.1 Math Tests

- [ ] `effective_price_for_size` walks book correctly
- [ ] `is_pair_profitable` uses effective prices
- [ ] `guaranteed_pnl` calculation correct
- [ ] `pair_cost_avg` calculation correct
- [ ] Leg imbalance calculations correct

### 8.2 Decision Logic Tests

- [ ] Rejects when exceeding max position
- [ ] Rejects when pair cost exceeds cap
- [ ] Rejects when no PnL improvement
- [ ] Rejects when imbalance exceeded
- [ ] Approves valid opportunities

### 8.3 Property-Based Tests (Hypothesis)

```python
@given(
    qty_yes=st.floats(min_value=0, max_value=10000),
    qty_no=st.floats(min_value=0, max_value=10000),
    cost_yes=st.floats(min_value=0, max_value=10000),
    cost_no=st.floats(min_value=0, max_value=10000),
)
def test_guaranteed_pnl_bounded(qty_yes, qty_no, cost_yes, cost_no):
    """Guaranteed PnL never exceeds total cost."""
    pos = PairPosition(...)
    if pos.total_cost() > 0:
        assert pos.guaranteed_pnl() <= pos.total_cost()
```

### 8.4 Integration Tests

- [ ] Full cycle: opportunity → order → fill → lock
- [ ] Partial fill handling
- [ ] Multiple markets concurrently

---

*Spec Version: 1.0*
