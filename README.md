## PolyLOL Planing

Our bot will focus 2 main games: LOL, DOTA 2

### APIs:

LOL: 

- liquipedia [link](https://liquipedia.net/api#api-site-contact)

- paid services like PandaScore [link](https://www.pandascore.co/pricing)

DOTA 2: 
- OpenDota API, [link](https://docs.opendota.com)

- GRID Open Access(Need to be approved) [link](https://grid.gg/get-access/) ,[API documentation](https://portal.grid.gg/documentation/api-reference/live-data-feed/api-reference-series-state-api)

### PolyMarket

We will use the Polymarket API: https://docs.polymarket.com/quickstart/first-order



Esports WS feeds
        ↓
Event Normalizer (Rust / Go)
        ↓
Truth Engine (state machine)
        ↓
Signal Engine (Python / Rust)
        ↓
Order Executor (py-clob-client)
        ↓
Risk + Capital Manager
        ↓
Storage + Metrics



| Layer      | Tech                      |
| ---------- | ------------------------- |
| Ingestion  | Rust (tokio, tungstenite) |
| Strategy   | Python (asyncio) or Rust  |
| Execution  | Python + py-clob-client   |
| RPC        | Polygon via Alchemy       |
| DB         | Postgres + Timescale      |
| Metrics    | Prometheus + Grafana      |
| Alerts     | Discord / Telegram        |
| Deployment | Docker + VPS (EU-West)    |


Polymarket Integration (Critical Details)

Using Polymarket

py-clob-client is the de-facto execution layer

Use WebSocket order book streams, not REST snapshots

Prefer IOC / FOK orders

Always pre-check:

Order book depth

Slippage at target size

Competing orders (front-run risk)

Order Lifecycle

PLACED → MATCHED → MINED → CONFIRMED


Bots that don’t track this precisely leak PnL.

Market Selection (Where Money Is)

Best characteristics:

Mid liquidity (not top-crowded)

Binary outcomes

Fast resolution (<24h)

Clear oracle / rules

Live or semi-live events

Avoid:

Subjective resolution markets

Long-dated political bets

Thin order books with fake depth

Capital Allocation

Binary pair arb: Kelly-fractioned, capped

Temporal arb: fixed small size per signal

Global max exposure per market

Daily loss circuit breaker (hard kill)

Performance Expectations (Realistic)
Strategy	Win Rate	Risk
Temporal Arb	85–98%	Medium
Binary Pair Arb	~100%	Low

Returns

0.3–1.5% per day on deployed capital (binary arb)

Temporal arb is spiky but additive

Key Risks (And Mitigations)
Risk	Mitigation
Market resolution disputes	Avoid ambiguous rules
Liquidity vanishes	Partial builds + safety margin
API downtime	Multi-feed redundancy
Rate limits	Queue + backoff
Over-competition	Market scoring + rotation
Open-Source Patterns (What You’ll See Repeated)

Event-driven loops

State machines for matches

Separate execution process

Heavy logging + replayability

Kill switches everywhere

Most public repos are partial, but patterns are consistent.

Prime Cursor Prompt (Use This)

Paste this directly into Cursor

You are a senior low-latency trading systems engineer.

Build a Polymarket-specific arbitrage trading system focused on:
1) Temporal arbitrage using live esports data
2) Binary YES/NO pair arbitrage with guaranteed edge

Requirements:
- Event-driven, WebSocket-first architecture
- Esports live data ingestion (assume WebSocket feeds)
- Truth engine that converts match events into probabilistic signals
- Binary pair arbitrage engine that accumulates YES/NO positions when avg cost < 1.00 – safety margin
- Execution via py-clob-client on Polymarket
- Full order lifecycle tracking (PLACED → MATCHED → MINED → CONFIRMED)
- Risk management: position limits, kill switch, daily loss cap
- Async, production-grade code
- Modular repo structure
- Docker-ready
- Extensive logging and metrics hooks

Use:
- Rust or Go for data ingestion if needed
- Python asyncio for strategy + execution
- Clear separation between signal generation and order execution

Start by designing:
1) Architecture diagram
2) Core modules and interfaces
3) Data models
4) Execution flow pseudocode

Then implement incrementally.


If you want, next we can:

Design the exact repo structure

Write the truth engine state machine

Implement the binary pair arb math module

Or simulate PnL under different safety margins

Just say the word.