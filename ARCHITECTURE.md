# PolyLOL Architecture & Development Plan

> Polymarket Esports Algo-Trader — Complete System Design

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Project Structure](#2-project-structure)
3. [System Architecture](#3-system-architecture)
4. [Data Flow](#4-data-flow)
5. [Core Components](#5-core-components)
6. [Trading Strategies](#6-trading-strategies)
7. [Risk Management](#7-risk-management)
8. [Development Phases](#8-development-phases)
9. [API Integrations](#9-api-integrations)
10. [Configuration & Deployment](#10-configuration--deployment)

---

## 1. Project Overview

### What We're Building

A production-grade algorithmic trading system that:

- **Ingests** real-time esports data (LoL, Dota 2) via WebSocket feeds
- **Processes** match events through a deterministic Truth Engine
- **Generates** trading signals for Polymarket prediction markets
- **Executes** orders via py-clob-client with full lifecycle tracking
- **Manages** risk with kill switches, position limits, and daily loss caps

### Target Markets

| Game | Data Sources | Market Type |
|------|--------------|-------------|
| **League of Legends** | Liquipedia, PandaScore | Match winner, Map winner |
| **Dota 2** | OpenDota API, GRID | Match winner, First blood, etc. |

### Trading Strategies

| Strategy | Edge Source | Expected Performance |
|----------|-------------|---------------------|
| **Binary Pair Arbitrage** | Price inefficiency (YES + NO < $0.98) | ~100% win rate, 0.3-1.5%/day |
| **Temporal Arbitrage** | Faster data than market | 85-98% win rate, spiky returns |

---

## 2. Project Structure

```
polyloly/
├── README.md
├── ARCHITECTURE.md              # ← This file
├── pyproject.toml               # Dependencies & project config
├── .env.example                 # Environment template
├── .gitignore
│
├── config/
│   ├── base.yaml                # Shared settings
│   ├── dev.yaml                 # Development overrides
│   └── prod.yaml                # Production settings
│
├── docs/
│   ├── 00_OVERVIEW.md
│   ├── 01_REPO_STRUCTURE.md
│   ├── 02_TRUTH_ENGINE_STATE_MACHINE.md
│   ├── 03_BINARY_PAIR_ARB_MATH.md
│   └── 04_PNL_SIMULATION_SAFETY_MARGINS.md
│
├── src/
│   ├── __init__.py
│   │
│   ├── bot/                     # Core application
│   │   ├── __init__.py
│   │   ├── main.py              # Entry point, wires components
│   │   ├── settings.py          # Config loading & validation
│   │   ├── bus.py               # Async event bus (pub/sub)
│   │   ├── clock.py             # Monotonic + wall clock helpers
│   │   ├── errors.py            # Custom exceptions
│   │   └── logging.py           # Structured JSON logging
│   │
│   ├── adapters/                # External system integrations
│   │   ├── __init__.py
│   │   │
│   │   ├── polymarket/          # Polymarket CLOB
│   │   │   ├── __init__.py
│   │   │   ├── client.py        # py-clob-client wrapper
│   │   │   ├── ws_orderbook.py  # Orderbook WebSocket
│   │   │   ├── ws_user.py       # User order/fill updates
│   │   │   └── models.py        # Order, Fill, Market models
│   │   │
│   │   └── esports/             # Esports data providers
│   │       ├── __init__.py
│   │       ├── base.py          # Abstract provider interface
│   │       ├── opendota.py      # Dota 2 via OpenDota API
│   │       ├── liquipedia.py    # LoL via Liquipedia
│   │       ├── grid.py          # GRID live feed (optional)
│   │       └── models.py        # Normalized match events
│   │
│   ├── domain/                  # Pure business logic (no I/O)
│   │   ├── __init__.py
│   │   ├── types.py             # Core dataclasses
│   │   ├── markets.py           # Market registry, esports↔poly mapping
│   │   ├── truth_engine.py      # Esports state machine
│   │   ├── signals.py           # Signal definitions
│   │   └── risk.py              # Risk rule evaluation
│   │
│   ├── strategies/              # Trading strategy implementations
│   │   ├── __init__.py
│   │   │
│   │   ├── pair_arb/            # Binary pair arbitrage
│   │   │   ├── __init__.py
│   │   │   ├── math.py          # Pure arb math (from interfaces.py)
│   │   │   ├── engine.py        # Decision loop
│   │   │   └── params.py        # Thresholds, sizing knobs
│   │   │
│   │   └── temporal_arb/        # Temporal arbitrage
│   │       ├── __init__.py
│   │       ├── engine.py        # Signal generation from truth deltas
│   │       └── params.py        # Latency thresholds, confidence reqs
│   │
│   ├── execution/               # Order management
│   │   ├── __init__.py
│   │   ├── order_manager.py     # Idempotent order placement
│   │   ├── state.py             # Position + open orders state
│   │   └── reconciliation.py    # Expected vs actual holdings
│   │
│   └── storage/                 # Persistence & logging
│       ├── __init__.py
│       ├── events_writer.py     # Append-only JSONL event log
│       └── sqlite.py            # Local state (optional)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Pytest fixtures
│   ├── test_truth_engine.py
│   ├── test_pair_math.py
│   ├── test_order_manager.py
│   └── test_strategies.py
│
├── scripts/
│   ├── pnl_simulation.py        # Safety margin simulation
│   ├── record_streams.py        # Record WS feeds for replay
│   └── replay_backtest.py       # Deterministic replay
│
└── docker/
    ├── Dockerfile
    └── compose.yaml
```

---

## 3. System Architecture

### High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              POLYLOLY SYSTEM                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐       │
│   │  ESPORTS DATA   │     │   POLYMARKET    │     │    STORAGE      │       │
│   │                 │     │                 │     │                 │       │
│   │ • OpenDota WS   │     │ • Orderbook WS  │     │ • Event Log     │       │
│   │ • Liquipedia    │     │ • User WS       │     │ • SQLite        │       │
│   │ • GRID API      │     │ • REST API      │     │ • Metrics       │       │
│   └────────┬────────┘     └────────┬────────┘     └────────▲────────┘       │
│            │                       │                       │                 │
│            ▼                       ▼                       │                 │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                          EVENT BUS (async queues)                    │   │
│   │  MatchEvent | OrderBookDelta | UserFill | ClockTick | TruthSignal   │   │
│   └──────────────────────────────┬──────────────────────────────────────┘   │
│                                  │                                           │
│            ┌─────────────────────┼─────────────────────┐                    │
│            ▼                     ▼                     ▼                    │
│   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐          │
│   │  TRUTH ENGINE   │   │   STRATEGIES    │   │   EXECUTION     │          │
│   │                 │   │                 │   │                 │          │
│   │ State Machine:  │──▶│ • Pair Arb      │──▶│ • Order Manager │          │
│   │ PRE_MATCH       │   │ • Temporal Arb  │   │ • Lifecycle     │          │
│   │ LIVE            │   │                 │   │ • Retries       │          │
│   │ PAUSED          │   │ Emits:          │   │                 │          │
│   │ PENDING_CONFIRM │   │ PlaceIntent     │   │ Uses:           │          │
│   │ FINAL           │   │ CancelIntent    │   │ py-clob-client  │          │
│   └─────────────────┘   └─────────────────┘   └────────┬────────┘          │
│            │                     │                     │                    │
│            └─────────────────────┴─────────────────────┘                    │
│                                  │                                           │
│                                  ▼                                           │
│                         ┌─────────────────┐                                 │
│                         │  RISK MANAGER   │                                 │
│                         │                 │                                 │
│                         │ • Position lim  │                                 │
│                         │ • Daily loss    │                                 │
│                         │ • Kill switch   │                                 │
│                         └─────────────────┘                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | I/O Allowed? |
|-----------|---------------|--------------|
| `adapters/*` | Talk to external APIs, normalize data | Yes |
| `domain/*` | Pure logic, state machines, math | No |
| `strategies/*` | Generate trading intents from signals | No (reads only) |
| `execution/*` | Apply intents via adapters | Yes |
| `storage/*` | Persist events, support replay | Yes |

---

## 4. Data Flow

### Event-Driven Loop

```
1. INGEST
   ├── Esports adapter receives match event (e.g., ROUND_ENDED)
   ├── Polymarket adapter receives orderbook delta
   └── Events published to bus

2. PROCESS
   ├── Truth Engine updates state, emits TruthDelta or TruthFinal
   ├── Market registry updates best bid/ask
   └── Events logged to storage

3. DECIDE
   ├── Pair Arb engine checks: can we improve guaranteed PnL?
   ├── Temporal Arb engine checks: is there a high-confidence edge?
   └── Strategy emits Intent (PlaceOrderIntent, CancelIntent, HaltIntent)

4. EXECUTE
   ├── Risk manager approves/rejects intent
   ├── Order manager places order via py-clob-client
   └── Tracks lifecycle: PLACED → MATCHED → MINED → CONFIRMED

5. RECONCILE
   ├── Compare expected vs actual positions
   ├── Handle discrepancies
   └── Update metrics
```

### Order Lifecycle

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│  PLACED  │───▶│ MATCHED  │───▶│  MINED   │───▶│ CONFIRMED │
└──────────┘    └──────────┘    └──────────┘    └───────────┘
     │               │               │
     ▼               ▼               ▼
  REJECTED       PARTIAL         FAILED
                  FILL           (revert)
```

---

## 5. Core Components

### 5.1 Truth Engine (State Machine)

Converts raw esports events into high-confidence trading signals.

```
States:
  PRE_MATCH ──────▶ LIVE ──────▶ POST_MATCH_PENDING ──────▶ FINAL
       │              │                   │
       ▼              ▼                   ▼
    PAUSED ◀────── PAUSED              (timeout)

Transitions:
  PRE_MATCH + MATCH_STARTED     → LIVE
  LIVE + PAUSED                 → PAUSED
  PAUSED + RESUMED              → LIVE
  LIVE + MATCH_ENDED            → POST_MATCH_PENDING (conf=0.80)
  POST_MATCH_PENDING + confirm  → FINAL (conf≥0.90 or timeout≥10s)
```

**Output Signals:**
- `TruthDelta(type, data)` — Score update, round end, map end
- `TruthFinal(winner_team_id)` — Match resolved, trigger position exit

### 5.2 Market Registry

Maps esports matches to Polymarket markets.

```python
@dataclass
class MarketMapping:
    match_id: str           # e.g., "dota2_12345"
    poly_market_id: str     # Polymarket market ID
    poly_yes_token: str     # Token ID for YES
    poly_no_token: str      # Token ID for NO
    team_a_id: str          # Maps to YES
    team_b_id: str          # Maps to NO
    resolution_time: datetime
```

### 5.3 Event Bus

Async pub/sub for decoupled components.

```python
class EventBus:
    async def publish(self, event: Event) -> None: ...
    async def subscribe(self, event_type: Type[Event], handler: Callable) -> None: ...

# Event types
MatchEvent          # From esports adapters
OrderBookDelta      # From Polymarket WS
UserFill            # From Polymarket user WS
TruthDelta          # From Truth Engine
TruthFinal          # From Truth Engine
PlaceOrderIntent    # From strategies
CancelOrderIntent   # From strategies
ClockTick           # Periodic heartbeat
```

---

## 6. Trading Strategies

### 6.1 Binary Pair Arbitrage

**Edge:** When YES price + NO price < 1.00 - fee (0.98), buying both locks in profit.

**Math (from `interfaces.py`):**

```python
# Guaranteed payout at resolution
q_min = min(q_yes, q_no)
payout_net = q_min * (1 - fee_rate)  # fee_rate = 0.02

# Guaranteed PnL
guaranteed_pnl = payout_net - (cost_yes + cost_no)

# Profitability condition
pair_cost_avg = avg_yes + avg_no
profitable when: pair_cost_avg < 0.98
```

**Decision Logic:**

```python
def should_buy_more(pos, side, amount, price, pair_cost_cap, max_total_cost):
    # 1. Don't exceed max capital
    if pos.total_cost() + amount > max_total_cost:
        return False
    
    # 2. Simulate the buy
    new_pos = pos.hypo_buy(side, amount, price)
    
    # 3. Check pair cost stays under cap
    if new_pos.pair_cost_avg() >= pair_cost_cap:
        return False
    
    # 4. Must improve guaranteed PnL
    return new_pos.guaranteed_pnl() > pos.guaranteed_pnl()
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pair_cost_cap` | 0.975 | Max avg cost for YES+NO pair |
| `max_total_cost` | $1,500 | Max capital per market |
| `step_usdc` | $25 | Size of each incremental buy |
| `fee_rate` | 0.02 | Polymarket winner fee |

**Execution Flow:**

```
1. Subscribe to orderbook for both YES and NO tokens
2. On each orderbook update:
   a. Calculate current best prices
   b. Determine which leg to buy (prefer balancing)
   c. Check should_buy_more()
   d. If yes, emit PlaceOrderIntent
3. On TruthFinal:
   a. Stop trading
   b. Position resolves automatically
```

### 6.2 Temporal Arbitrage

**Edge:** Act on match outcome faster than the market adjusts prices.

**Signal Sources:**

| Signal | Confidence | Typical Latency |
|--------|------------|-----------------|
| Final score from API | 0.95+ | 1-5 seconds |
| Map/round winner | 0.80 | 0.5-2 seconds |
| Kill/objective (live) | 0.60-0.75 | Real-time |

**Decision Logic:**

```python
def evaluate_temporal_signal(truth_engine, orderbook, params):
    # Only trade on high-confidence signals
    if truth_engine.confidence < params.min_confidence:
        return None
    
    # Check if market price lags behind our truth
    if truth_engine.status == FINAL:
        winner = truth_engine.winner_team_id
        # Market should price winner near 1.0
        if orderbook.best_ask(winner_token) < params.max_entry_price:
            return PlaceOrderIntent(
                side=winner_token,
                price=orderbook.best_ask(winner_token),
                size=params.fixed_size
            )
    
    return None
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_confidence` | 0.90 | Minimum truth confidence to act |
| `max_entry_price` | 0.95 | Don't buy above this price |
| `fixed_size` | $50 | Size per temporal signal |
| `max_exposure` | $200 | Max total temporal exposure |

---

## 7. Risk Management

### Risk Rules (Hard-Coded Limits)

```python
@dataclass
class RiskParams:
    # Per-market limits
    max_position_per_market: float = 1500.0    # USDC
    max_open_orders_per_market: int = 5
    
    # Global limits
    max_total_exposure: float = 5000.0         # USDC across all markets
    max_daily_loss: float = 200.0              # Triggers kill switch
    
    # Per-trade limits
    max_single_order: float = 100.0            # USDC
    min_order_size: float = 5.0                # USDC
    
    # Slippage
    max_slippage_bps: float = 50.0             # 0.5%
```

### Kill Switch Conditions

```python
class KillSwitch:
    """Halt all trading immediately when triggered."""
    
    def check(self, state: TradingState) -> bool:
        # Daily loss exceeded
        if state.daily_pnl < -self.params.max_daily_loss:
            return True
        
        # Execution errors exceed threshold
        if state.consecutive_exec_errors > 3:
            return True
        
        # Manual override
        if state.manual_halt:
            return True
        
        # API connectivity lost
        if state.polymarket_disconnected or state.esports_disconnected:
            return True
        
        return False
```

### Position Sizing (Kelly Fraction)

```python
def kelly_stake(edge: float, odds: float, fraction: float = 0.25) -> float:
    """
    Quarter-Kelly for safety.
    
    edge: our estimated advantage (e.g., 0.05 = 5%)
    odds: decimal odds
    fraction: Kelly multiplier (0.25 = quarter Kelly)
    """
    b = odds - 1
    kelly_full = edge / b
    return max(0, kelly_full * fraction)
```

---

## 8. Development Phases

### Phase 1: Foundation (Week 1)

| Task | Priority | Status |
|------|----------|--------|
| Project scaffolding (dirs, pyproject.toml) | P0 | ⬜ |
| Core types (`src/domain/types.py`) | P0 | ⬜ |
| Event bus (`src/bot/bus.py`) | P0 | ⬜ |
| Settings/config loading | P0 | ⬜ |
| Structured logging | P1 | ⬜ |

**Deliverable:** Runnable skeleton with event bus

### Phase 2: Truth Engine (Week 2)

| Task | Priority | Status |
|------|----------|--------|
| Truth Engine state machine | P0 | ⬜ |
| Normalized match event models | P0 | ⬜ |
| Unit tests for all transitions | P0 | ⬜ |
| Confidence accumulation logic | P1 | ⬜ |

**Deliverable:** Fully tested Truth Engine

### Phase 3: Adapters (Week 3)

| Task | Priority | Status |
|------|----------|--------|
| OpenDota API client | P0 | ⬜ |
| Polymarket CLOB client wrapper | P0 | ⬜ |
| Orderbook WebSocket subscriber | P0 | ⬜ |
| User fills WebSocket subscriber | P1 | ⬜ |
| Liquipedia client (LoL) | P2 | ⬜ |

**Deliverable:** Live data flowing through bus

### Phase 4: Pair Arb Strategy (Week 4)

| Task | Priority | Status |
|------|----------|--------|
| Move `interfaces.py` math to `strategies/pair_arb/math.py` | P0 | ⬜ |
| Pair arb engine (decision loop) | P0 | ⬜ |
| Integration with orderbook | P0 | ⬜ |
| Backtest with recorded data | P1 | ⬜ |

**Deliverable:** Pair arb generating intents

### Phase 5: Execution (Week 5)

| Task | Priority | Status |
|------|----------|--------|
| Order manager (idempotent placement) | P0 | ⬜ |
| Order lifecycle tracking | P0 | ⬜ |
| Retry/backoff logic | P0 | ⬜ |
| Reconciliation | P1 | ⬜ |

**Deliverable:** End-to-end order flow

### Phase 6: Risk & Hardening (Week 6)

| Task | Priority | Status |
|------|----------|--------|
| Risk manager implementation | P0 | ⬜ |
| Kill switch | P0 | ⬜ |
| Daily P&L tracking | P0 | ⬜ |
| Event logging (JSONL) | P1 | ⬜ |
| Alerts (Telegram/Discord) | P2 | ⬜ |

**Deliverable:** Production-safe system

### Phase 7: Temporal Arb (Week 7+)

| Task | Priority | Status |
|------|----------|--------|
| Temporal arb signal logic | P1 | ⬜ |
| Latency measurement | P1 | ⬜ |
| Integration with Truth Engine | P1 | ⬜ |

**Deliverable:** Second strategy operational

### Phase 8: Deployment (Week 8+)

| Task | Priority | Status |
|------|----------|--------|
| Dockerfile | P1 | ⬜ |
| Docker Compose (with Postgres/Redis) | P1 | ⬜ |
| Grafana dashboards | P2 | ⬜ |
| CI/CD pipeline | P2 | ⬜ |

---

## 9. API Integrations

### 9.1 Polymarket

**Library:** `py-clob-client`

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,  # Polygon
    signature_type=2,  # EIP-712
)

# Place order
order = client.create_order(OrderArgs(
    token_id=YES_TOKEN_ID,
    price=0.45,
    size=100,
    side="BUY",
))
```

**WebSocket Endpoints:**
- Orderbook: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- User: `wss://ws-subscriptions-clob.polymarket.com/ws/user`

### 9.2 OpenDota (Dota 2)

**Base URL:** `https://api.opendota.com/api`

```python
# Get live matches
GET /live

# Get match details
GET /matches/{match_id}

# Get team recent matches
GET /teams/{team_id}/matches
```

**Rate Limits:** 60 requests/minute (free tier)

### 9.3 Liquipedia (LoL)

**Base URL:** `https://liquipedia.net/leagueoflegends/api.php`

```python
# Query match results
GET ?action=query&titles=Match_Page&format=json
```

**Note:** Requires user-agent identification, rate limited.

### 9.4 GRID (Optional, Premium)

**WebSocket:** Live match state at 300ms intervals

```python
# Subscribe to series
ws.send({"action": "subscribe", "series_id": "12345"})

# Receive frames
{
    "type": "state_update",
    "timestamp": 1706500000000,
    "data": {
        "score": [1, 0],
        "map": 2,
        "round": 15,
        ...
    }
}
```

---

## 10. Configuration & Deployment

### Environment Variables

```bash
# .env.example

# Polymarket
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_API_KEY=...

# Esports Data
OPENDOTA_API_KEY=...
PANDASCORE_API_KEY=...
GRID_API_KEY=...

# Database (optional)
DATABASE_URL=postgresql://user:pass@localhost:5432/polyloly
REDIS_URL=redis://localhost:6379

# Alerts
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...

# Risk
MAX_DAILY_LOSS=200
MAX_POSITION_PER_MARKET=1500

# Mode
PAPER_TRADING=true
LOG_LEVEL=INFO
```

### YAML Config

```yaml
# config/base.yaml

bot:
  name: polyloly
  log_level: INFO

risk:
  max_daily_loss: 200.0
  max_position_per_market: 1500.0
  max_total_exposure: 5000.0
  kill_switch_enabled: true

strategies:
  pair_arb:
    enabled: true
    pair_cost_cap: 0.975
    step_usdc: 25.0
    fee_rate: 0.02
  
  temporal_arb:
    enabled: false
    min_confidence: 0.90
    fixed_size: 50.0

adapters:
  polymarket:
    host: https://clob.polymarket.com
    chain_id: 137
  
  esports:
    opendota:
      enabled: true
      poll_interval_ms: 5000
    liquipedia:
      enabled: true
    grid:
      enabled: false
```

### Docker Compose

```yaml
# docker/compose.yaml
version: '3.8'

services:
  bot:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    env_file: ../.env
    depends_on:
      - redis
    restart: unless-stopped
    volumes:
      - ../logs:/app/logs
      - ../data:/app/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

  # Optional: metrics
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana

volumes:
  redis_data:
  grafana_data:
```

---

## Quick Reference

### Key Files to Implement First

1. `src/domain/types.py` — Core dataclasses
2. `src/bot/bus.py` — Event bus
3. `src/domain/truth_engine.py` — State machine
4. `src/adapters/polymarket/client.py` — CLOB wrapper
5. `src/strategies/pair_arb/engine.py` — Pair arb logic
6. `src/execution/order_manager.py` — Order placement

### Commands

```bash
# Install dependencies
pip install -e .

# Run tests
pytest tests/ -v

# Run simulation
python scripts/pnl_simulation.py

# Start bot (paper trading)
PAPER_TRADING=true python -m src.bot.main

# Start bot (live)
python -m src.bot.main
```

---

*Document Version: 1.0 | Created: January 2025*
