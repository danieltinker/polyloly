# 01 — Exact Repo Structure (E2E, production-oriented)

## Goals
- WebSocket-first ingestion (esports + Polymarket)
- Deterministic strategy core
- Execution isolated behind an adapter (easy to mock / test)
- Full observability (structured logs + metrics)
- Replayability (record streams -> replay -> backtest)

---

## Proposed layout

```
polymarket-bot/
  README.md
  pyproject.toml
  .env.example
  config/
    base.yaml
    dev.yaml
    prod.yaml
  docs/
    00_OVERVIEW.md
    01_REPO_STRUCTURE.md
    02_TRUTH_ENGINE_STATE_MACHINE.md
    03_BINARY_PAIR_ARB_MATH.md
    04_PNL_SIMULATION_SAFETY_MARGINS.md
  src/
    bot/
      __init__.py
      main.py                  # entrypoint: wires everything + runs event loop
      settings.py              # env + yaml config load, validation
      clock.py                 # monotonic + wall-clock helpers
      errors.py
      logging.py               # JSON logs, correlation IDs
      metrics.py               # Prometheus-compatible counters/histograms (optional)
      bus.py                   # async pub/sub (Queues), event types
    adapters/
      polymarket/
        __init__.py
        clob_client.py         # wraps py-clob-client calls
        ws_market.py           # market/orderbook WS subscriber
        ws_user.py             # user/order updates WS subscriber
        models.py              # token_id, market_id, order models
      esports/
        __init__.py
        provider_base.py       # normalized provider interface
        provider_pandascore.py # example (placeholder)
        provider_official.py   # example (placeholder)
        models.py              # normalized match events
    domain/
      __init__.py
      types.py                 # pure dataclasses: PriceLevel, Order, TradeFill, etc.
      markets.py               # market registry + mapping esports<->polymarket
      truth_engine.py          # esports state machine -> truth events
      signals.py               # signal definitions
      risk.py                  # limits, kill-switch decisions
    strategies/
      __init__.py
      pair_arb/
        __init__.py
        math.py                # binary pair arb math module (pure)
        engine.py              # decision loop (uses math + orderbook)
        params.py              # thresholds, safety margins, sizing knobs
      temporal_arb/
        __init__.py
        engine.py              # optional, future
        params.py
    execution/
      __init__.py
      order_manager.py         # idempotent order placement, cancels, retries
      state.py                 # position + open orders state
      reconciliation.py        # compare expected vs actual holdings/orders
    storage/
      __init__.py
      events_writer.py         # append-only event log (JSONL)
      sqlite.py                # simple local store (optional)
    tools/
      __init__.py
      record_streams.py        # record WS streams for replay
      replay_streams.py        # deterministic replay into strategies
  tests/
    test_pair_math.py
    test_truth_engine.py
  scripts/
    pnl_simulation.py
  docker/
    Dockerfile
    compose.yaml
```

---

## Responsibility boundaries (strict)

### `adapters/*`
- Talk to external systems (Polymarket CLOB, esports providers).
- Convert raw JSON -> normalized dataclasses.
- **No strategy logic**.

### `domain/*`
- Pure logic, deterministic.
- No network calls.
- Includes the esports **Truth Engine** state machine.

### `strategies/*`
- Use domain outputs (truth events + orderbooks) to generate **Intents**:
  - `PlaceOrderIntent`, `CancelOrderIntent`, `HaltIntent`, etc.
- No direct calls to py-clob-client.

### `execution/*`
- Takes Intents and performs them through adapters.
- Handles retries/backoff, idempotency, lifecycle tracking.

### `storage/*`
- Append-only logs + optional DB for analytics.
- Supports replay/backtest.

---

## Event-driven loop (core pattern)

1. Adapters push normalized events into `bot.bus`:
   - `OrderBookDelta`, `UserFill`, `MatchEvent`, `ClockTick`.
2. Domain updates state (truth engine, positions).
3. Strategy emits intents.
4. Execution applies intents.
5. Storage logs everything.
