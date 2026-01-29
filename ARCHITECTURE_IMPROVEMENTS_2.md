# Architecture Improvement Suggestions (Added Value)

This document reviews `ARCHITECTURE.md` and proposes concrete improvements to make the system **more correct**, **safer**, **more debuggable**, and **faster in production**.

---

## 0) High-impact improvements (do these first)

### A. Make “Truth” and “Trading” explicitly **separate state machines**
Right now the doc has a Truth Engine state machine, but trading/execution is described more procedurally. Add a second explicit state machine for **Execution & Exposure** per market:

**Per-market trading states**
- `IDLE` (watching)
- `BUILDING_PAIR` (pair arb accumulation)
- `LOCKED_PAIR` (guaranteed pnl > 0, no more buys)
- `TEMPORAL_ACTIVE` (temporal signal active; small exposure)
- `HALT` (risk kill / connectivity / manual)
- `FINALIZING` (truth says effectively_final; stop entries; only cancels)
- `RESOLVED` (final settlement detected / position closed)

This makes it much easier to:
- reason about allowed actions,
- implement clean tests,
- prevent “late orders” during finalization.

### B. Add **backpressure** and **event ordering** rules to the Event Bus
Your bus is a key dependency; specify:
- queue sizes per topic,
- what happens on overflow (drop, coalesce, halt),
- ordering guarantees (per-market ordering, per-source ordering),
- dedup keys for repeated events.

Recommend:
- per-market partitioning (one queue per `market_id`) to keep determinism and avoid cross-market head-of-line blocking.

### C. Make “Paper Trading” a first-class execution adapter
You mention paper trading via env var, but it’s not modeled as an adapter. Add:
- `adapters/polymarket/execution_real.py`
- `adapters/polymarket/execution_paper.py`

Both implement the same interface:
- `place_order()`, `cancel_order()`, `fetch_open_orders()`, `fetch_positions()`

This enables deterministic replay, CI integration tests, and safer iterations.

---

## 1) Esports data ingestion improvements (correctness + latency)

### 1.1 Treat Liquipedia as **confirmation**, not “live signal”
Liquipedia is not designed for sub-second live updates; it is rate-limited and often delayed. Represent it as:
- “results/confirmation source” rather than “live signal source”.

Add an explicit “Source Quality” rubric:
- **Tier A:** official league feed / GRID (low latency, reliable)
- **Tier B:** PandaScore (good but not always lowest-latency)
- **Tier C:** community/wiki sources (confirmation only)

### 1.2 Multi-source confirmation for “final”
For end-of-match events, require:
- 2 independent sources agree OR one Tier-A source emits terminal state.

Suggested config:
```yaml
truth_engine:
  confirm_threshold: 0.90
  max_wait_ms: 10000
  required_sources_for_final: 2
  tier_a_sources: ["grid", "official"]
```

### 1.3 Time synchronization
Latency/arbitrage decisions depend on timestamps. Add:
- NTP sync requirement on VPS
- monotonic clock usage in code (`time.monotonic_ns()`)

---

## 2) Polymarket / Polygon details to add (production-critical)

### 2.1 Wallet model & approvals
Production requires:
- USDC approval flow (allowance checks + spender approvals),
- gas management,
- nonce/rpc errors,
- chain confirmation policy.

Add a subsection:
- **Wallet & Funding**
  - EOA vs proxy wallet (if used)
  - USDC allowance check on startup
  - “dry-run startup checklist”

### 2.2 Order types and execution constraints
Add explicit selection rules:
- When to use `IOC` vs `FOK` vs `GTC`
- “max time in book” for GTC (cancel after 3–10s)
- Slippage guardrails: compute expected fill price from book depth, not only top-of-book.

### 2.3 Reconciliation cadence + invariants
Define:
- Every N seconds: reconcile positions and open orders with Polymarket
- Invariant: `expected_position ≈ actual_position` within tolerance
- If violated: halt that market, alert, rebuild state from truth + broker snapshot.

---

## 3) Binary Pair Arb: make profitability checks robust

### 3.1 Distinguish “top-of-book check” vs “cost-basis guarantee”
The rule `YES + NO < 0.98` is only valid when fills match your assumption. Improve spec:
- use **effective average fill price** at your intended size by integrating over book levels,
- include fees + expected slippage + safety margin.

Add function:
- `effective_price_for_size(side, size_usdc) -> avg_price`

Then require:
- `eff_yes + eff_no < 1 - fee_rate - safety_margin`

### 3.2 Add a “leg imbalance” constraint
Prevent runaway one-sided exposure:
- `abs(cost_yes - cost_no) <= max_leg_imbalance_usdc`
or
- `abs(q_yes - q_no) <= max_leg_imbalance_shares`

This increases odds you can complete the hedge under liquidity shocks.

---

## 4) Temporal Arb: specify an edge model

### 4.1 Map truth state → implied probability
Instead of confidence-only, define `implied_p`:
- effectively_final => 0.99
- map advantage => 0.65–0.80 (tuned per title)
Trade when:
- `implied_p - market_mid >= edge_threshold`
Size via capped Kelly fraction + hard cap.

### 4.2 Add “staleness detection”
If truth feed stalls, the bot may trade incorrectly.
Add:
- per-source `stale_ms` threshold
- if stale: halt temporal strategy for that match.

---

## 5) Risk management hardening

### 5.1 Per-market circuit breaker
In addition to a global kill switch:
- `max_consecutive_rejects_per_market`
- `max_cancel_failures_per_market`
- `max_order_latency_ms`

Trip → halt that market only; keep others running.

### 5.2 Drawdown-based sizing + cool-down
Beyond a daily loss kill:
- scale down sizes as drawdown increases
- cool-down window after any kill event

### 5.3 Resolution risk filter
Esports can have DQs/remakes/rulebook reversals.
Filter markets:
- avoid ambiguous settlement criteria
- require clear oracle language

---

## 6) Observability (logging, metrics, replay)

### 6.1 Correlation IDs everywhere
Standard fields:
- `run_id`, `market_id`, `match_id`, `intent_id`, `order_id`
Every log line includes these.

### 6.2 Metrics that matter
Minimal Prometheus set:
- `event_lag_ms{source=...}`
- `truth_confidence{match_id}`
- `order_place_latency_ms`
- `order_fill_latency_ms`
- `slippage_bps`
- `reconcile_mismatch_count`
- `kill_switch_triggers_total{reason=...}`

### 6.3 Replay schema versioning
Specify:
- stable JSONL envelope
- `schema_version`
- deterministic ordering during replay

---

## 7) Config improvements

### 7.1 Put all thresholds into YAML with safe defaults
Examples:
- `pair_cost_cap`, `fee_rate`, `safety_margin`
- `max_leg_imbalance_usdc`, `min_liquidity_usdc`
- `stale_ms_by_source`

### 7.2 Production secrets management
Recommend:
- no production keys in `.env`
- use Doppler/Vault/SSM for production

---

## 8) Repo structure tweaks (small but valuable)

### 8.1 Add `src/domain/state_models/`
Separate immutable state from engines:
- `truth_state.py`
- `market_state.py`
- `execution_state.py`

### 8.2 Add `src/domain/pnl/`
Centralize fee-aware PnL accounting:
- realized/unrealized
- per-market attribution

### 8.3 Add `src/tools/healthcheck.py`
CLI verifies:
- WS connectivity
- RPC connectivity
- USDC allowance
- can fetch markets/orderbooks
- time sync sanity

---

## 9) Concrete additions to ARCHITECTURE.md (ready to paste)

### 9.1 Startup checklist
- Validate config + env
- NTP/time sync OK
- Connect esports feeds (all enabled)
- Connect Polymarket WS (market + user)
- Check USDC allowance
- Warm caches: market mappings, token IDs
- Start in `PAPER_TRADING` unless explicitly set live

### 9.2 Failure modes table
Include: detection + mitigation + alert
- WS disconnects
- partial fills
- stale truth
- chain congestion
- order status desync

---

## 10) Recommended next implementation steps
1. Add per-market trading state machine.
2. Specify bus backpressure + ordering + per-market partitioning.
3. Reclassify Liquipedia as confirmation only; prioritize Tier-A live feeds.
4. Add wallet/approval/nonce handling + reconciliation invariants.
5. Upgrade pair arb checks to use effective prices + leg imbalance caps.
6. Add probability model + staleness detection for temporal strategy.
7. Add correlation IDs, core metrics, replay schema versioning.
