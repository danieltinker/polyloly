# Specification: Event Bus

> Partitioned async pub/sub with backpressure and dead letter queue

---

## 1. Purpose

The Event Bus:
- **Decouples** components for testability and replay
- **Partitions** events by market for ordering guarantees
- **Handles backpressure** to prevent memory exhaustion
- **Captures failed events** in a dead letter queue

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         EVENT BUS                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Publishers                                                      │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐               │
│  │ Esports │ │  Poly   │ │ Truth   │ │Strategy │               │
│  │ Adapter │ │ Adapter │ │ Engine  │ │ Engine  │               │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘               │
│       │           │           │           │                      │
│       └───────────┴───────────┴───────────┘                      │
│                       │                                          │
│                       ▼                                          │
│              ┌────────────────┐                                  │
│              │    Router      │                                  │
│              │ (by market_id) │                                  │
│              └───────┬────────┘                                  │
│                      │                                           │
│       ┌──────────────┼──────────────┐                           │
│       ▼              ▼              ▼                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                     │
│  │Market A │    │Market B │    │ Global  │                     │
│  │  Queue  │    │  Queue  │    │  Queue  │                     │
│  │(ordered)│    │(ordered)│    │(ordered)│                     │
│  └────┬────┘    └────┬────┘    └────┬────┘                     │
│       │              │              │                           │
│       └──────────────┴──────────────┘                           │
│                      │                                           │
│                      ▼                                           │
│              ┌────────────────┐                                  │
│              │   Dispatcher   │                                  │
│              │ (with retry)   │                                  │
│              └───────┬────────┘                                  │
│                      │                                           │
│       ┌──────────────┼──────────────┐                           │
│       ▼              ▼              ▼                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐     │
│  │Handler 1│    │Handler 2│    │Handler N│    │   DLQ   │     │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Components

### 3.1 Event Base Class

```python
@dataclass
class Event:
    """Base class for all events."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    market_id: Optional[str] = None  # None = global queue
    
    def partition_key(self) -> str:
        """Determine which queue this event goes to."""
        return self.market_id or "__global__"
```

### 3.2 Partitioned Event Bus

```python
@dataclass
class BusConfig:
    max_queue_size: int = 1000
    overflow_policy: Literal["drop", "coalesce", "block", "halt"] = "drop"
    handler_timeout_ms: float = 5000
    max_retry_attempts: int = 3
    retry_base_delay_ms: float = 100
    
class PartitionedEventBus:
    def __init__(self, config: BusConfig):
        self._config = config
        self._market_queues: dict[str, asyncio.Queue[Event]] = {}
        self._global_queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=config.max_queue_size
        )
        self._handlers: dict[Type[Event], list[Callable]] = defaultdict(list)
        self._dlq: asyncio.Queue[FailedEvent] = asyncio.Queue()
        self._running = False
    
    def _get_or_create_queue(self, partition_key: str) -> asyncio.Queue[Event]:
        if partition_key == "__global__":
            return self._global_queue
        
        if partition_key not in self._market_queues:
            self._market_queues[partition_key] = asyncio.Queue(
                maxsize=self._config.max_queue_size
            )
        
        return self._market_queues[partition_key]
```

---

## 4. Publishing

### 4.1 Publish Method

```python
async def publish(self, event: Event) -> bool:
    """
    Publish an event to the appropriate queue.
    Returns True if accepted, False if dropped.
    """
    partition_key = event.partition_key()
    queue = self._get_or_create_queue(partition_key)
    
    # Backpressure handling
    if queue.full():
        match self._config.overflow_policy:
            case "drop":
                logger.warning({
                    "event_type": "event_dropped",
                    "partition": partition_key,
                    "event_class": type(event).__name__,
                })
                metrics.events_dropped.labels(partition=partition_key).inc()
                return False
            
            case "coalesce":
                # Replace oldest similar event
                coalesced = await self._coalesce(queue, event)
                if coalesced:
                    return True
                # Fall through to drop if can't coalesce
                return False
            
            case "block":
                # Will block until space available
                await queue.put(event)
                return True
            
            case "halt":
                raise BackpressureError(
                    f"Queue full for partition {partition_key}"
                )
    
    await queue.put(event)
    metrics.events_published.labels(partition=partition_key).inc()
    return True
```

### 4.2 Coalescing Logic

```python
async def _coalesce(self, queue: asyncio.Queue, new_event: Event) -> bool:
    """
    Try to coalesce new event with existing one of same type.
    Used for high-frequency events like OrderBookDelta.
    """
    if not isinstance(new_event, (OrderBookDelta, ClockTick)):
        return False
    
    # Drain queue, replace matching event, re-add
    events = []
    coalesced = False
    
    while not queue.empty():
        try:
            event = queue.get_nowait()
            if type(event) == type(new_event) and not coalesced:
                events.append(new_event)  # Replace with newer
                coalesced = True
            else:
                events.append(event)
        except asyncio.QueueEmpty:
            break
    
    if not coalesced:
        events.append(new_event)
    
    for event in events:
        await queue.put(event)
    
    return True
```

---

## 5. Subscribing

### 5.1 Subscribe Method

```python
def subscribe(
    self,
    event_type: Type[Event],
    handler: Callable[[Event], Awaitable[None]],
    priority: int = 0,
) -> None:
    """
    Register a handler for an event type.
    Higher priority handlers execute first.
    """
    self._handlers[event_type].append((priority, handler))
    # Sort by priority (descending)
    self._handlers[event_type].sort(key=lambda x: -x[0])
    
    logger.info({
        "event_type": "handler_registered",
        "event_class": event_type.__name__,
        "handler": handler.__name__,
        "priority": priority,
    })

def unsubscribe(
    self,
    event_type: Type[Event],
    handler: Callable,
) -> None:
    """Remove a handler."""
    self._handlers[event_type] = [
        (p, h) for p, h in self._handlers[event_type]
        if h != handler
    ]
```

---

## 6. Dispatching

### 6.1 Main Loop

```python
async def start(self):
    """Start the event processing loops."""
    self._running = True
    
    # Start consumer for each queue
    tasks = [
        asyncio.create_task(self._consume_queue("__global__", self._global_queue))
    ]
    
    for partition_key, queue in self._market_queues.items():
        task = asyncio.create_task(self._consume_queue(partition_key, queue))
        tasks.append(task)
    
    await asyncio.gather(*tasks)

async def stop(self):
    """Gracefully stop processing."""
    self._running = False
    # Allow in-flight handlers to complete
    await asyncio.sleep(0.1)
```

### 6.2 Queue Consumer

```python
async def _consume_queue(self, partition_key: str, queue: asyncio.Queue):
    """Process events from a single queue in order."""
    while self._running:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        
        await self._dispatch(event, partition_key)
        queue.task_done()

async def _dispatch(self, event: Event, partition_key: str):
    """Dispatch event to all registered handlers with retry."""
    event_type = type(event)
    handlers = self._handlers.get(event_type, [])
    
    # Also check for base class handlers
    for base in event_type.__mro__:
        if base in self._handlers and base != event_type:
            handlers.extend(self._handlers[base])
    
    for priority, handler in handlers:
        success = await self._invoke_handler(event, handler)
        if not success:
            # Handler exhausted retries, event goes to DLQ
            break
```

### 6.3 Handler Invocation with Retry

```python
async def _invoke_handler(
    self,
    event: Event,
    handler: Callable,
) -> bool:
    """
    Invoke handler with timeout and retry.
    Returns True if successful, False if exhausted retries.
    """
    for attempt in range(self._config.max_retry_attempts):
        try:
            await asyncio.wait_for(
                handler(event),
                timeout=self._config.handler_timeout_ms / 1000
            )
            return True
        
        except asyncio.TimeoutError:
            logger.warning({
                "event_type": "handler_timeout",
                "handler": handler.__name__,
                "event_id": event.event_id,
                "attempt": attempt + 1,
            })
        
        except Exception as e:
            logger.error({
                "event_type": "handler_error",
                "handler": handler.__name__,
                "event_id": event.event_id,
                "attempt": attempt + 1,
                "error": str(e),
            })
        
        # Exponential backoff
        if attempt < self._config.max_retry_attempts - 1:
            delay = self._config.retry_base_delay_ms * (2 ** attempt) / 1000
            await asyncio.sleep(delay)
    
    # Exhausted retries - send to DLQ
    await self._send_to_dlq(event, handler)
    return False
```

---

## 7. Dead Letter Queue

### 7.1 DLQ Entry

```python
@dataclass
class FailedEvent:
    event: Event
    handler_name: str
    error_message: str
    failed_at: datetime
    attempt_count: int
    partition_key: str
```

### 7.2 DLQ Operations

```python
async def _send_to_dlq(self, event: Event, handler: Callable):
    """Send failed event to DLQ."""
    failed = FailedEvent(
        event=event,
        handler_name=handler.__name__,
        error_message="Exhausted retries",
        failed_at=datetime.utcnow(),
        attempt_count=self._config.max_retry_attempts,
        partition_key=event.partition_key(),
    )
    
    await self._dlq.put(failed)
    metrics.dlq_size.inc()
    
    logger.error({
        "event_type": "event_to_dlq",
        "event_id": event.event_id,
        "event_class": type(event).__name__,
        "handler": handler.__name__,
    })

async def get_dlq_events(self) -> list[FailedEvent]:
    """Get all events from DLQ (for manual inspection)."""
    events = []
    while not self._dlq.empty():
        try:
            events.append(self._dlq.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events

async def replay_dlq_event(self, failed: FailedEvent):
    """Attempt to reprocess a DLQ event."""
    await self.publish(failed.event)
```

---

## 8. Metrics

```python
# Prometheus metrics for the bus
events_published = Counter(
    'polyloly_bus_events_published_total',
    'Events published',
    ['partition']
)
events_dropped = Counter(
    'polyloly_bus_events_dropped_total',
    'Events dropped due to backpressure',
    ['partition']
)
handler_duration = Histogram(
    'polyloly_bus_handler_duration_seconds',
    'Handler execution duration',
    ['handler']
)
queue_depth = Gauge(
    'polyloly_bus_queue_depth',
    'Current queue depth',
    ['partition']
)
dlq_size = Gauge(
    'polyloly_bus_dlq_size',
    'Dead letter queue size'
)
```

---

## 9. Event Types Reference

### 9.1 Market-Scoped Events

| Event | Source | Description |
|-------|--------|-------------|
| `MatchEvent` | Esports adapters | Match state updates |
| `OrderBookDelta` | Polymarket WS | Orderbook changes |
| `UserFill` | Polymarket WS | Order fills |
| `TruthDelta` | Truth Engine | Truth state changes |
| `TruthFinal` | Truth Engine | Match finalized |
| `OrderIntent` | Strategies | Intent to place order |
| `CancelIntent` | Strategies | Intent to cancel order |

### 9.2 Global Events

| Event | Source | Description |
|-------|--------|-------------|
| `ClockTick` | Clock module | Periodic heartbeat |
| `SystemHalt` | Risk manager | Global halt signal |
| `ConfigReload` | Settings | Config hot reload |
| `ConnectionStatus` | Adapters | Connectivity changes |

---

## 10. Configuration

```yaml
# config/base.yaml

event_bus:
  max_queue_size: 1000
  overflow_policy: "drop"  # drop | coalesce | block | halt
  handler_timeout_ms: 5000
  max_retry_attempts: 3
  retry_base_delay_ms: 100
  
  # Per-partition overrides
  partitions:
    __global__:
      max_queue_size: 500
      overflow_policy: "block"  # Global events are important
```

---

## 11. Testing Requirements

### 11.1 Unit Tests

- [ ] Publish routes to correct partition
- [ ] Subscribe registers handler
- [ ] Handlers invoked in priority order
- [ ] Timeout triggers retry
- [ ] Exhausted retries → DLQ

### 11.2 Backpressure Tests

- [ ] Drop policy works
- [ ] Coalesce policy replaces old events
- [ ] Block policy waits for space
- [ ] Halt policy raises exception

### 11.3 Ordering Tests

- [ ] Events in same partition processed in order
- [ ] Different partitions are independent
- [ ] DLQ preserves event data

### 11.4 Performance Tests

- [ ] High throughput (10k events/sec)
- [ ] Memory bounded under load
- [ ] Graceful degradation

---

*Spec Version: 1.0*
