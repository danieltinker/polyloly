# Specification: Execution Layer

> Idempotent order management with full lifecycle tracking

---

## 1. Purpose

The Execution Layer:
- Provides **idempotent** order placement (safe retries)
- Tracks **full order lifecycle** (PLACED → MATCHED → MINED → CONFIRMED)
- Handles **partial fills** gracefully
- Implements **slippage protection**
- Supports both **real** and **paper** trading modes

---

## 2. Order Lifecycle

```
┌──────────────────────────────────────────────────────────────────┐
│                        ORDER LIFECYCLE                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌────────┐    ┌────────┐    ┌────────┐    ┌───────────┐        │
│  │PENDING │───▶│ PLACED │───▶│MATCHED │───▶│   MINED   │        │
│  └────────┘    └────┬───┘    └────┬───┘    └─────┬─────┘        │
│                     │             │              │               │
│                     │             │              ▼               │
│                     │             │        ┌───────────┐         │
│                     │             │        │ CONFIRMED │         │
│                     │             │        └───────────┘         │
│                     │             │                              │
│                     ▼             ▼                              │
│               ┌──────────┐  ┌──────────┐                        │
│               │ REJECTED │  │ PARTIAL  │                        │
│               └──────────┘  │   FILL   │                        │
│                             └──────────┘                        │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│  From any state:                                                 │
│    CANCELLED (by user/strategy)                                  │
│    FAILED (chain revert, timeout)                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### State Descriptions

| State | Description | Next States |
|-------|-------------|-------------|
| `PENDING` | Intent received, not yet sent | PLACED, REJECTED |
| `PLACED` | Sent to exchange | MATCHED, REJECTED, CANCELLED |
| `MATCHED` | Order matched in orderbook | MINED, PARTIAL_FILL |
| `MINED` | Transaction mined on chain | CONFIRMED, FAILED |
| `CONFIRMED` | Transaction confirmed (finality) | Terminal |
| `REJECTED` | Exchange rejected order | Terminal |
| `CANCELLED` | User/strategy cancelled | Terminal |
| `FAILED` | Chain revert or timeout | Terminal |
| `PARTIAL_FILL` | Partially filled | MATCHED (more fills), CONFIRMED |

---

## 3. Order Manager

### 3.1 Core Interface

```python
class OrderManager:
    """Manages order lifecycle with idempotency."""
    
    def __init__(
        self,
        executor: ExecutorInterface,
        risk_manager: RiskManager,
        config: ExecutionConfig,
    ):
        self._executor = executor
        self._risk = risk_manager
        self._config = config
        
        # Order tracking
        self._orders: dict[str, Order] = {}
        self._pending_keys: TTLCache[str, Order] = TTLCache(
            maxsize=1000,
            ttl=300,  # 5 minutes
        )
        
        # Metrics
        self._consecutive_rejects = 0
        self._consecutive_failures = 0
    
    async def place(self, intent: OrderIntent) -> OrderResult:
        """
        Place an order from intent.
        Handles idempotency, risk checks, and lifecycle.
        """
        # 1. Idempotency check
        if intent.idempotency_key in self._pending_keys:
            existing = self._pending_keys[intent.idempotency_key]
            return OrderResult(
                success=True,
                order=existing,
                deduplicated=True,
            )
        
        # 2. Risk check
        risk_result = self._risk.check_order(intent)
        if not risk_result.approved:
            return OrderResult(
                success=False,
                error=f"Risk rejected: {risk_result.reason}",
            )
        
        # Adjust size if risk manager capped it
        if risk_result.adjusted_size:
            intent.size = risk_result.adjusted_size
        
        # 3. Slippage check
        slippage_result = await self._check_slippage(intent)
        if not slippage_result.allowed:
            return OrderResult(
                success=False,
                error=f"Slippage exceeded: {slippage_result.expected_slippage_bps:.1f}bps",
            )
        
        # 4. Create order
        order = Order(
            id=str(uuid4()),
            market_id=intent.market_id,
            token_id=self._get_token_id(intent),
            side=intent.side,
            price=intent.price,
            size=intent.size,
            idempotency_key=intent.idempotency_key,
            status=OrderStatus.PENDING,
        )
        
        # 5. Track before sending
        self._orders[order.id] = order
        self._pending_keys[intent.idempotency_key] = order
        
        # 6. Send to executor
        try:
            result = await self._executor.place_order(order)
            order.status = OrderStatus.PLACED
            order.placed_at = datetime.utcnow()
            self._consecutive_rejects = 0
            
            logger.info({
                "event_type": "order_placed",
                "order_id": order.id,
                "market_id": order.market_id,
                "side": order.side.value,
                "price": order.price,
                "size": order.size,
            })
            
            return OrderResult(success=True, order=order)
            
        except OrderRejectedError as e:
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)
            self._consecutive_rejects += 1
            
            logger.warning({
                "event_type": "order_rejected",
                "order_id": order.id,
                "reason": str(e),
            })
            
            return OrderResult(success=False, error=str(e), order=order)
            
        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error_message = str(e)
            self._consecutive_failures += 1
            
            logger.error({
                "event_type": "order_failed",
                "order_id": order.id,
                "error": str(e),
            })
            
            return OrderResult(success=False, error=str(e), order=order)
```

### 3.2 Cancel Operations

```python
    async def cancel(self, order_id: str) -> bool:
        """Cancel a specific order."""
        if order_id not in self._orders:
            return False
        
        order = self._orders[order_id]
        
        if order.status not in (OrderStatus.PLACED, OrderStatus.MATCHED):
            return False  # Can't cancel
        
        try:
            await self._executor.cancel_order(order_id)
            order.status = OrderStatus.CANCELLED
            
            logger.info({
                "event_type": "order_cancelled",
                "order_id": order_id,
            })
            
            return True
            
        except Exception as e:
            logger.error({
                "event_type": "cancel_failed",
                "order_id": order_id,
                "error": str(e),
            })
            return False
    
    async def cancel_all(self, market_id: Optional[str] = None) -> int:
        """Cancel all open orders, optionally filtered by market."""
        cancelled = 0
        
        for order_id, order in self._orders.items():
            if market_id and order.market_id != market_id:
                continue
            
            if order.status in (OrderStatus.PLACED, OrderStatus.MATCHED):
                if await self.cancel(order_id):
                    cancelled += 1
        
        return cancelled
```

### 3.3 Fill Handling

```python
    def on_fill(self, fill: UserFill) -> Optional[Order]:
        """Process a fill notification."""
        # Find the order
        order = self._find_order_by_fill(fill)
        if not order:
            logger.warning({
                "event_type": "orphan_fill",
                "fill_order_id": fill.order_id,
            })
            return None
        
        # Update order state
        order.filled_size += fill.size
        
        if fill.fill_type == FillType.FULL:
            order.status = OrderStatus.MATCHED
            order.matched_at = datetime.utcnow()
            order.avg_fill_price = fill.price
        else:
            # Partial fill - recalculate average
            old_value = (order.avg_fill_price or 0) * (order.filled_size - fill.size)
            new_value = old_value + (fill.price * fill.size)
            order.avg_fill_price = new_value / order.filled_size
        
        logger.info({
            "event_type": "order_fill",
            "order_id": order.id,
            "fill_type": fill.fill_type.value,
            "fill_size": fill.size,
            "fill_price": fill.price,
            "total_filled": order.filled_size,
            "avg_price": order.avg_fill_price,
        })
        
        return order
    
    def on_chain_confirmation(self, order_id: str, tx_hash: str):
        """Process chain confirmation."""
        if order_id not in self._orders:
            return
        
        order = self._orders[order_id]
        order.status = OrderStatus.CONFIRMED
        order.confirmed_at = datetime.utcnow()
        
        logger.info({
            "event_type": "order_confirmed",
            "order_id": order_id,
            "tx_hash": tx_hash,
        })
```

---

## 4. Slippage Protection

### 4.1 SlippageGuard

```python
@dataclass
class SlippageResult:
    allowed: bool
    expected_slippage_bps: float
    effective_price: float
    recommendation: Optional[str] = None

class SlippageGuard:
    """Protect against excessive slippage."""
    
    def __init__(self, config: SlippageConfig):
        self._config = config
    
    async def check(
        self,
        intent: OrderIntent,
        orderbook: OrderBook,
    ) -> SlippageResult:
        """
        Check if order would experience acceptable slippage.
        """
        # Calculate effective fill price
        effective_price = orderbook.effective_price_for_size(
            side=Side.BUY,  # We're always buying
            size_usdc=intent.size,
        )
        
        if effective_price == float('inf'):
            return SlippageResult(
                allowed=False,
                expected_slippage_bps=float('inf'),
                effective_price=effective_price,
                recommendation="Insufficient liquidity",
            )
        
        # Calculate slippage vs intended price
        slippage_bps = abs(effective_price - intent.price) / intent.price * 10000
        
        if slippage_bps > self._config.max_slippage_bps:
            return SlippageResult(
                allowed=False,
                expected_slippage_bps=slippage_bps,
                effective_price=effective_price,
                recommendation=f"Reduce size or increase price. Expected: {effective_price:.4f}",
            )
        
        # Warning threshold
        if slippage_bps > self._config.warn_slippage_bps:
            logger.warning({
                "event_type": "slippage_warning",
                "market_id": intent.market_id,
                "slippage_bps": slippage_bps,
            })
        
        return SlippageResult(
            allowed=True,
            expected_slippage_bps=slippage_bps,
            effective_price=effective_price,
        )
```

---

## 5. Executor Interface

### 5.1 Abstract Interface

```python
class ExecutorInterface(ABC):
    """Interface for order execution backends."""
    
    @abstractmethod
    async def place_order(self, order: Order) -> dict:
        """Place an order. Returns exchange response."""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        pass
    
    @abstractmethod
    async def get_open_orders(self, market_id: Optional[str] = None) -> list[Order]:
        """Get open orders."""
        pass
    
    @abstractmethod
    async def get_positions(self) -> dict[str, Position]:
        """Get current positions."""
        pass
```

### 5.2 Real Executor (py-clob-client)

```python
class RealExecutor(ExecutorInterface):
    """Production executor using py-clob-client."""
    
    def __init__(self, config: PolymarketConfig):
        self._client = ClobClient(
            host=config.host,
            key=config.private_key,
            chain_id=config.chain_id,
            signature_type=2,
        )
    
    async def place_order(self, order: Order) -> dict:
        """Place order on Polymarket."""
        try:
            result = await asyncio.to_thread(
                self._client.create_order,
                OrderArgs(
                    token_id=order.token_id,
                    price=order.price,
                    size=order.size,
                    side="BUY",
                )
            )
            return result
        except Exception as e:
            if "rejected" in str(e).lower():
                raise OrderRejectedError(str(e))
            raise
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Polymarket."""
        result = await asyncio.to_thread(
            self._client.cancel,
            order_id,
        )
        return result.get("success", False)
    
    async def get_open_orders(self, market_id: Optional[str] = None) -> list[Order]:
        """Fetch open orders from Polymarket."""
        result = await asyncio.to_thread(
            self._client.get_orders,
            OpenOrderParams(market=market_id) if market_id else None,
        )
        return [self._parse_order(o) for o in result]
    
    async def get_positions(self) -> dict[str, Position]:
        """Fetch positions from Polymarket."""
        result = await asyncio.to_thread(
            self._client.get_positions,
        )
        return {p["market"]: self._parse_position(p) for p in result}
```

### 5.3 Paper Executor

```python
class PaperExecutor(ExecutorInterface):
    """Paper trading executor for testing."""
    
    def __init__(self, orderbook_provider: OrderBookProvider):
        self._orderbook_provider = orderbook_provider
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._next_order_id = 1
    
    async def place_order(self, order: Order) -> dict:
        """Simulate order placement."""
        # Simulate fill based on current orderbook
        orderbook = await self._orderbook_provider.get(order.token_id)
        
        effective_price = orderbook.effective_price_for_size(
            Side.BUY,
            order.size,
        )
        
        # Simulate instant fill at effective price
        order.status = OrderStatus.CONFIRMED
        order.avg_fill_price = effective_price
        order.filled_size = order.size
        order.confirmed_at = datetime.utcnow()
        
        # Update position
        self._update_position(order)
        
        self._orders[order.id] = order
        
        return {"success": True, "order_id": order.id}
    
    async def cancel_order(self, order_id: str) -> bool:
        """Simulate cancel (always succeeds in paper mode)."""
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False
    
    async def get_open_orders(self, market_id: Optional[str] = None) -> list[Order]:
        """Return simulated open orders."""
        return [
            o for o in self._orders.values()
            if o.status in (OrderStatus.PLACED, OrderStatus.MATCHED)
            and (market_id is None or o.market_id == market_id)
        ]
    
    async def get_positions(self) -> dict[str, Position]:
        """Return simulated positions."""
        return self._positions.copy()
    
    def _update_position(self, order: Order):
        """Update position after fill."""
        key = f"{order.market_id}:{order.side.value}"
        
        if key not in self._positions:
            self._positions[key] = Position(
                market_id=order.market_id,
                side=order.side,
                qty=0,
                avg_price=0,
                cost=0,
            )
        
        pos = self._positions[key]
        old_value = pos.qty * pos.avg_price
        new_value = order.filled_size * order.avg_fill_price
        pos.qty += order.filled_size
        pos.cost += order.size
        pos.avg_price = (old_value + new_value) / pos.qty if pos.qty > 0 else 0
```

---

## 6. Reconciliation

### 6.1 Reconciler

```python
class Reconciler:
    """Compare expected vs actual state."""
    
    def __init__(self, executor: ExecutorInterface, tolerance: float = 0.01):
        self._executor = executor
        self._tolerance = tolerance
    
    async def reconcile_positions(
        self,
        expected: dict[str, PairPosition],
    ) -> ReconciliationResult:
        """
        Compare expected positions with actual from exchange.
        """
        actual = await self._executor.get_positions()
        
        mismatches = []
        
        for market_id, expected_pos in expected.items():
            actual_yes = actual.get(f"{market_id}:YES")
            actual_no = actual.get(f"{market_id}:NO")
            
            # Check YES leg
            expected_yes = expected_pos.q_yes
            actual_yes_qty = actual_yes.qty if actual_yes else 0
            
            if abs(expected_yes - actual_yes_qty) > self._tolerance:
                mismatches.append(PositionMismatch(
                    market_id=market_id,
                    side=Side.YES,
                    expected=expected_yes,
                    actual=actual_yes_qty,
                ))
            
            # Check NO leg
            expected_no = expected_pos.q_no
            actual_no_qty = actual_no.qty if actual_no else 0
            
            if abs(expected_no - actual_no_qty) > self._tolerance:
                mismatches.append(PositionMismatch(
                    market_id=market_id,
                    side=Side.NO,
                    expected=expected_no,
                    actual=actual_no_qty,
                ))
        
        result = ReconciliationResult(
            mismatches=mismatches,
            reconciled_at=datetime.utcnow(),
        )
        
        if mismatches:
            logger.error({
                "event_type": "reconciliation_mismatch",
                "mismatch_count": len(mismatches),
                "mismatches": [asdict(m) for m in mismatches],
            })
            
            # Alert
            metrics.reconcile_mismatch_count.inc(len(mismatches))
        
        return result
    
    async def reconcile_orders(
        self,
        expected: dict[str, Order],
    ) -> list[OrderMismatch]:
        """Compare expected open orders with actual."""
        actual = await self._executor.get_open_orders()
        actual_by_id = {o.id: o for o in actual}
        
        mismatches = []
        
        # Check for missing orders
        for order_id, expected_order in expected.items():
            if expected_order.status in (OrderStatus.PLACED, OrderStatus.MATCHED):
                if order_id not in actual_by_id:
                    mismatches.append(OrderMismatch(
                        order_id=order_id,
                        type="missing",
                        expected_status=expected_order.status,
                        actual_status=None,
                    ))
        
        # Check for unexpected orders
        for order_id in actual_by_id:
            if order_id not in expected:
                mismatches.append(OrderMismatch(
                    order_id=order_id,
                    type="unexpected",
                    expected_status=None,
                    actual_status=actual_by_id[order_id].status,
                ))
        
        return mismatches
```

---

## 7. Configuration

```yaml
# config/base.yaml

execution:
  # Slippage
  max_slippage_bps: 50
  warn_slippage_bps: 25
  
  # Timeouts
  order_timeout_ms: 30000
  cancel_timeout_ms: 10000
  
  # Retries
  max_retry_attempts: 3
  retry_base_delay_ms: 100
  
  # Reconciliation
  reconcile_interval_sec: 60
  position_tolerance: 0.01
  
  # Mode
  paper_trading: true  # Set false for live

polymarket:
  host: "https://clob.polymarket.com"
  chain_id: 137
  # private_key loaded from secrets
```

---

## 8. Testing Requirements

### 8.1 Unit Tests

- [ ] Idempotency prevents duplicate orders
- [ ] Risk check rejects invalid orders
- [ ] Slippage guard calculates correctly
- [ ] Fill handling updates order state
- [ ] Cancel operations work correctly

### 8.2 Integration Tests

- [ ] Full order flow with paper executor
- [ ] Partial fill handling
- [ ] Reconciliation detects mismatches
- [ ] Error handling and retries

### 8.3 E2E Tests (Paper Mode)

- [ ] Pair arb flow: intent → place → fill → confirm
- [ ] Temporal arb flow: signal → place → fill
- [ ] Cancel flow: place → cancel
- [ ] Multiple markets concurrently

---

*Spec Version: 1.0*
