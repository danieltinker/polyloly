# Specification: Adapters

> External API interfaces for Polymarket and esports data

---

## 1. Purpose

Adapters:
- **Isolate** external API complexity from business logic
- **Normalize** data into internal types
- **Handle** retries, rate limits, and errors
- **Support** both WebSocket and REST patterns

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         ADAPTERS LAYER                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      POLYMARKET                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │  │
│  │  │   Client    │  │  Orderbook  │  │    User     │       │  │
│  │  │   (REST)    │  │     WS      │  │     WS      │       │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │  │
│  │         │                │                │               │  │
│  │         └────────────────┴────────────────┘               │  │
│  │                          │                                 │  │
│  │                          ▼                                 │  │
│  │                   Circuit Breaker                          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      ESPORTS DATA                          │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │  │
│  │  │    GRID     │  │  OpenDota   │  │ Liquipedia  │       │  │
│  │  │  (Tier A)   │  │  (Tier B)   │  │  (Tier C)   │       │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │  │
│  │         │                │                │               │  │
│  │         └────────────────┴────────────────┘               │  │
│  │                          │                                 │  │
│  │                          ▼                                 │  │
│  │                 Provider Interface                         │  │
│  │                  (normalized events)                       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Polymarket Adapters

### 3.1 REST Client

```python
class PolymarketClient:
    """REST client for Polymarket CLOB."""
    
    def __init__(self, config: PolymarketConfig, circuit_breaker: CircuitBreaker):
        self._config = config
        self._cb = circuit_breaker
        self._client = ClobClient(
            host=config.host,
            key=config.private_key,
            chain_id=config.chain_id,
            signature_type=2,
        )
        self._rate_limiter = RateLimiter(
            max_requests=config.rate_limit_per_minute,
            period=timedelta(minutes=1),
        )
    
    async def get_market(self, market_id: str) -> Market:
        """Fetch market details."""
        async with self._rate_limiter:
            result = await self._cb.call(
                asyncio.to_thread,
                self._client.get_market,
                market_id,
            )
            return self._parse_market(result)
    
    async def get_orderbook(self, token_id: str) -> OrderBook:
        """Fetch orderbook snapshot."""
        async with self._rate_limiter:
            result = await self._cb.call(
                asyncio.to_thread,
                self._client.get_order_book,
                token_id,
            )
            return self._parse_orderbook(result)
    
    async def get_positions(self) -> dict[str, Position]:
        """Fetch user positions."""
        async with self._rate_limiter:
            result = await self._cb.call(
                asyncio.to_thread,
                self._client.get_positions,
            )
            return {p["market"]: self._parse_position(p) for p in result}
    
    async def get_open_orders(self) -> list[Order]:
        """Fetch open orders."""
        async with self._rate_limiter:
            result = await self._cb.call(
                asyncio.to_thread,
                self._client.get_orders,
            )
            return [self._parse_order(o) for o in result]
    
    def _parse_market(self, data: dict) -> Market:
        return Market(
            id=data["condition_id"],
            question=data["question"],
            yes_token_id=data["tokens"][0]["token_id"],
            no_token_id=data["tokens"][1]["token_id"],
            end_date=datetime.fromisoformat(data["end_date_iso"]),
        )
    
    def _parse_orderbook(self, data: dict) -> OrderBook:
        return OrderBook(
            token_id=data["asset_id"],
            bids=[OrderBookLevel(float(b["price"]), float(b["size"])) for b in data["bids"]],
            asks=[OrderBookLevel(float(a["price"]), float(a["size"])) for a in data["asks"]],
            timestamp_ms=int(time.time() * 1000),
        )
```

### 3.2 Orderbook WebSocket

```python
class PolymarketOrderbookWS:
    """WebSocket subscriber for orderbook updates."""
    
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(self, config: PolymarketConfig, bus: EventBus):
        self._config = config
        self._bus = bus
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_tokens: set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
    
    async def start(self):
        """Start WebSocket connection."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.error({
                    "event_type": "ws_error",
                    "feed": "orderbook",
                    "error": str(e),
                })
                await self._handle_disconnect()
    
    async def stop(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
    
    async def subscribe(self, token_id: str):
        """Subscribe to orderbook updates for a token."""
        self._subscribed_tokens.add(token_id)
        
        if self._ws and self._ws.open:
            await self._ws.send(json.dumps({
                "type": "subscribe",
                "channel": "book",
                "assets_ids": [token_id],
            }))
    
    async def unsubscribe(self, token_id: str):
        """Unsubscribe from a token."""
        self._subscribed_tokens.discard(token_id)
        
        if self._ws and self._ws.open:
            await self._ws.send(json.dumps({
                "type": "unsubscribe",
                "channel": "book",
                "assets_ids": [token_id],
            }))
    
    async def _connect(self):
        """Establish WebSocket connection."""
        async with websockets.connect(self.WS_URL) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset on successful connect
            
            logger.info({
                "event_type": "ws_connected",
                "feed": "orderbook",
            })
            
            # Resubscribe to all tokens
            for token_id in self._subscribed_tokens:
                await self._ws.send(json.dumps({
                    "type": "subscribe",
                    "channel": "book",
                    "assets_ids": [token_id],
                }))
            
            # Process messages
            async for message in ws:
                await self._handle_message(message)
    
    async def _handle_message(self, message: str):
        """Process incoming WebSocket message."""
        data = json.loads(message)
        
        if data.get("type") == "book":
            event = OrderBookDelta(
                token_id=data["asset_id"],
                bids=[OrderBookLevel(float(b["price"]), float(b["size"])) for b in data.get("bids", [])],
                asks=[OrderBookLevel(float(a["price"]), float(a["size"])) for a in data.get("asks", [])],
                is_snapshot=data.get("is_snapshot", False),
                market_id=self._token_to_market.get(data["asset_id"]),
            )
            
            await self._bus.publish(event)
    
    async def _handle_disconnect(self):
        """Handle disconnection with exponential backoff."""
        metrics.ws_reconnects.labels(feed="orderbook").inc()
        
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self._max_reconnect_delay,
        )
```

### 3.3 User WebSocket

```python
class PolymarketUserWS:
    """WebSocket subscriber for user order/fill updates."""
    
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    
    def __init__(self, config: PolymarketConfig, bus: EventBus):
        self._config = config
        self._bus = bus
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
    
    async def start(self):
        """Start WebSocket connection."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.error({
                    "event_type": "ws_error",
                    "feed": "user",
                    "error": str(e),
                })
                await asyncio.sleep(5)
    
    async def _connect(self):
        """Establish authenticated WebSocket connection."""
        # Generate auth signature
        auth = self._generate_auth()
        
        async with websockets.connect(
            self.WS_URL,
            extra_headers={"Authorization": auth},
        ) as ws:
            self._ws = ws
            
            logger.info({
                "event_type": "ws_connected",
                "feed": "user",
            })
            
            async for message in ws:
                await self._handle_message(message)
    
    async def _handle_message(self, message: str):
        """Process user update messages."""
        data = json.loads(message)
        
        match data.get("type"):
            case "fill":
                event = UserFill(
                    order_id=data["order_id"],
                    token_id=data["asset_id"],
                    side=Side(data["side"]),
                    price=float(data["price"]),
                    size=float(data["size"]),
                    fill_type=FillType.PARTIAL if data.get("partial") else FillType.FULL,
                    tx_hash=data.get("tx_hash"),
                    market_id=self._token_to_market.get(data["asset_id"]),
                )
                
                await self._bus.publish(event)
            
            case "order_update":
                # Handle order status changes
                pass
```

---

## 4. Esports Adapters

### 4.1 Provider Interface

```python
class EsportsProviderInterface(ABC):
    """Abstract interface for esports data providers."""
    
    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this source."""
        pass
    
    @property
    @abstractmethod
    def tier(self) -> DataSourceTier:
        """Data quality tier."""
        pass
    
    @abstractmethod
    async def start(self):
        """Start the provider."""
        pass
    
    @abstractmethod
    async def stop(self):
        """Stop the provider."""
        pass
    
    @abstractmethod
    async def subscribe_match(self, match_id: str):
        """Subscribe to updates for a match."""
        pass
    
    @abstractmethod
    async def unsubscribe_match(self, match_id: str):
        """Unsubscribe from a match."""
        pass
```

### 4.2 OpenDota Adapter (Tier B)

```python
class OpenDotaAdapter(EsportsProviderInterface):
    """Dota 2 data from OpenDota API."""
    
    BASE_URL = "https://api.opendota.com/api"
    
    @property
    def source_id(self) -> str:
        return "opendota"
    
    @property
    def tier(self) -> DataSourceTier:
        return DataSourceTier.TIER_B
    
    def __init__(self, config: OpenDotaConfig, bus: EventBus):
        self._config = config
        self._bus = bus
        self._rate_limiter = RateLimiter(
            max_requests=60,  # Free tier: 60/minute
            period=timedelta(minutes=1),
        )
        self._subscribed_matches: set[str] = set()
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start polling loop."""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
    
    async def stop(self):
        """Stop polling."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
    
    async def subscribe_match(self, match_id: str):
        self._subscribed_matches.add(match_id)
    
    async def unsubscribe_match(self, match_id: str):
        self._subscribed_matches.discard(match_id)
    
    async def _poll_loop(self):
        """Poll for updates on subscribed matches."""
        while self._running:
            try:
                for match_id in list(self._subscribed_matches):
                    await self._poll_match(match_id)
                
                await asyncio.sleep(self._config.poll_interval_ms / 1000)
                
            except Exception as e:
                logger.error({
                    "event_type": "poll_error",
                    "source": self.source_id,
                    "error": str(e),
                })
                await asyncio.sleep(5)
    
    async def _poll_match(self, match_id: str):
        """Poll a single match for updates."""
        async with self._rate_limiter:
            async with aiohttp.ClientSession() as session:
                # Get match details
                url = f"{self.BASE_URL}/matches/{match_id}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        events = self._parse_match_data(match_id, data)
                        
                        for event in events:
                            await self._bus.publish(event)
    
    def _parse_match_data(self, match_id: str, data: dict) -> list[MatchEvent]:
        """Parse OpenDota response into normalized events."""
        events = []
        
        # Determine match state
        if data.get("radiant_win") is not None:
            # Match ended
            winner = "radiant" if data["radiant_win"] else "dire"
            events.append(MatchEvent(
                match_id=match_id,
                event_type=MatchEventType.MATCH_ENDED,
                source=self.source_id,
                source_tier=self.tier,
                payload={
                    "winner_team_id": winner,
                    "radiant_score": data.get("radiant_score", 0),
                    "dire_score": data.get("dire_score", 0),
                    "duration": data.get("duration", 0),
                },
            ))
        
        return events
    
    async def get_live_matches(self) -> list[dict]:
        """Fetch currently live matches."""
        async with self._rate_limiter:
            async with aiohttp.ClientSession() as session:
                url = f"{self.BASE_URL}/live"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return []
```

### 4.3 GRID Adapter (Tier A)

```python
class GRIDAdapter(EsportsProviderInterface):
    """Official esports data from GRID."""
    
    @property
    def source_id(self) -> str:
        return "grid"
    
    @property
    def tier(self) -> DataSourceTier:
        return DataSourceTier.TIER_A
    
    def __init__(self, config: GRIDConfig, bus: EventBus):
        self._config = config
        self._bus = bus
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_series: set[str] = set()
        self._running = False
    
    async def start(self):
        """Start WebSocket connection."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.error({
                    "event_type": "ws_error",
                    "source": self.source_id,
                    "error": str(e),
                })
                await asyncio.sleep(5)
    
    async def _connect(self):
        """Establish authenticated WebSocket connection."""
        url = f"{self._config.ws_url}?api_key={self._config.api_key}"
        
        async with websockets.connect(url) as ws:
            self._ws = ws
            
            logger.info({
                "event_type": "ws_connected",
                "source": self.source_id,
            })
            
            # Resubscribe
            for series_id in self._subscribed_series:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "series_id": series_id,
                }))
            
            async for message in ws:
                await self._handle_message(message)
    
    async def _handle_message(self, message: str):
        """Process GRID WebSocket messages."""
        data = json.loads(message)
        
        match data.get("type"):
            case "state_update":
                events = self._parse_state_update(data)
                for event in events:
                    await self._bus.publish(event)
            
            case "match_ended":
                event = MatchEvent(
                    match_id=data["series_id"],
                    event_type=MatchEventType.MATCH_ENDED,
                    source=self.source_id,
                    source_tier=self.tier,
                    payload={
                        "winner_team_id": data["winner"],
                        "score": data["score"],
                    },
                )
                await self._bus.publish(event)
    
    def _parse_state_update(self, data: dict) -> list[MatchEvent]:
        """Parse GRID state update into events."""
        events = []
        
        state = data.get("data", {})
        series_id = data.get("series_id")
        
        # Score update
        if "score" in state:
            events.append(MatchEvent(
                match_id=series_id,
                event_type=MatchEventType.SCORE_UPDATE,
                source=self.source_id,
                source_tier=self.tier,
                payload={
                    "team_a_score": state["score"][0],
                    "team_b_score": state["score"][1],
                    "map_index": state.get("map", 0),
                },
            ))
        
        return events
```

### 4.4 Liquipedia Adapter (Tier C)

```python
class LiquipediaAdapter(EsportsProviderInterface):
    """LoL/Dota data from Liquipedia (confirmation only)."""
    
    BASE_URL = "https://liquipedia.net/leagueoflegends/api.php"
    
    @property
    def source_id(self) -> str:
        return "liquipedia"
    
    @property
    def tier(self) -> DataSourceTier:
        return DataSourceTier.TIER_C  # Confirmation only
    
    def __init__(self, config: LiquipediaConfig, bus: EventBus):
        self._config = config
        self._bus = bus
        self._rate_limiter = RateLimiter(
            max_requests=1,  # Very conservative
            period=timedelta(seconds=5),
        )
        self._subscribed_matches: dict[str, str] = {}  # match_id -> page_title
        self._running = False
    
    async def start(self):
        self._running = True
        asyncio.create_task(self._poll_loop())
    
    async def _poll_loop(self):
        """Poll for match results (infrequent, confirmation only)."""
        while self._running:
            try:
                for match_id, page_title in list(self._subscribed_matches.items()):
                    await self._check_match(match_id, page_title)
                
                # Long interval - this is for confirmation only
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error({
                    "event_type": "poll_error",
                    "source": self.source_id,
                    "error": str(e),
                })
                await asyncio.sleep(60)
    
    async def _check_match(self, match_id: str, page_title: str):
        """Check Liquipedia for match result."""
        async with self._rate_limiter:
            async with aiohttp.ClientSession() as session:
                params = {
                    "action": "query",
                    "titles": page_title,
                    "format": "json",
                }
                headers = {
                    "User-Agent": self._config.user_agent,
                }
                
                async with session.get(self.BASE_URL, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Parse result and emit event if match ended
                        # (Implementation depends on page structure)
```

---

## 5. Rate Limiting

```python
class RateLimiter:
    """Token bucket rate limiter."""
    
    def __init__(self, max_requests: int, period: timedelta):
        self._max_requests = max_requests
        self._period = period
        self._requests: deque[datetime] = deque()
        self._lock = asyncio.Lock()
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def acquire(self):
        """Wait until a request slot is available."""
        async with self._lock:
            now = datetime.utcnow()
            cutoff = now - self._period
            
            # Remove old requests
            while self._requests and self._requests[0] < cutoff:
                self._requests.popleft()
            
            # Wait if at limit
            if len(self._requests) >= self._max_requests:
                oldest = self._requests[0]
                wait_until = oldest + self._period
                wait_seconds = (wait_until - now).total_seconds()
                
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
            
            # Record this request
            self._requests.append(datetime.utcnow())
```

---

## 6. Configuration

```yaml
# config/base.yaml

adapters:
  polymarket:
    host: "https://clob.polymarket.com"
    chain_id: 137
    rate_limit_per_minute: 100
    ws_reconnect_delay_sec: 5
    ws_max_reconnect_delay_sec: 60
  
  esports:
    opendota:
      enabled: true
      poll_interval_ms: 5000
      rate_limit_per_minute: 60
    
    grid:
      enabled: false  # Requires API key
      ws_url: "wss://live.grid.gg/ws"
    
    liquipedia:
      enabled: true
      user_agent: "PolyLOL/1.0 (contact@example.com)"
      poll_interval_sec: 30
```

---

## 7. Testing Requirements

### 7.1 Unit Tests

- [ ] Rate limiter enforces limits
- [ ] Parser converts API responses to internal types
- [ ] Circuit breaker integration

### 7.2 Integration Tests (Mocked)

- [ ] Polymarket REST client
- [ ] Polymarket WebSocket reconnection
- [ ] OpenDota polling loop
- [ ] Multi-adapter coordination

### 7.3 E2E Tests (Live APIs, Sandbox)

- [ ] Polymarket testnet connection
- [ ] OpenDota live data fetch
- [ ] Full event flow through bus

---

*Spec Version: 1.0*
