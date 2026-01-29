# 02 — Truth Engine State Machine (Esports)

## Objective
Convert **low-level esports provider events** into **high-confidence truth signals** suitable for trading:
- Match started / paused / resumed
- Map / round winners
- Score deltas
- Match ended + confirmed winner (final)

The Truth Engine should be:
- Deterministic
- Idempotent (same event twice = same state)
- Tolerant to out-of-order events
- Provider-agnostic (normalized events in)

---

## Normalized input event schema

Each provider event is normalized into:

- `match_id: str`
- `ts_ms: int` (provider timestamp)
- `seq: int | None` (monotonic if provider offers it; else None)
- `type: MatchEventType`
- `payload: dict`

### Suggested event types
- `MATCH_CREATED`
- `MATCH_STARTED`
- `PAUSED`
- `RESUMED`
- `MAP_STARTED(map_index)`
- `ROUND_ENDED(map_index, round_index, winner_team_id)`
- `MAP_ENDED(map_index, winner_team_id)`
- `SCORE_UPDATE(team_a_score, team_b_score, map_index)`
- `MATCH_ENDED(winner_team_id)`
- `CORRECTION(original_event_ref, corrected_payload)`  (optional)

---

## State model

### Engine states
1. `PRE_MATCH`
2. `LIVE`
3. `PAUSED`
4. `POST_MATCH_PENDING_CONFIRM` (ended but waiting for confirmation threshold)
5. `FINAL`

### Core state fields
- `status`
- `match_id`
- `seen_event_ids` (for idempotency)
- `last_ts_ms`
- `last_seq (optional)`
- `map_index`
- `round_index`
- `score_a`, `score_b`
- `winner_team_id (optional)`
- `confidence` in [0,1]
- `ended_at_ms (optional)`
- `finalized_at_ms (optional)`

---

## Transition rules (high-level)

### PRE_MATCH
- on `MATCH_STARTED` -> `LIVE`
- on `PAUSED` -> `PAUSED` (rare but allowed)
- ignore round/map events until `MATCH_STARTED` (or buffer)

### LIVE
- on `PAUSED` -> `PAUSED`
- on `SCORE_UPDATE` -> stay `LIVE`, update score, emit `TruthDelta(SCORE)`
- on `MAP_STARTED` -> stay `LIVE`, set map_index
- on `ROUND_ENDED` -> stay `LIVE`, update round_index, emit `TruthDelta(ROUND)`
- on `MAP_ENDED` -> stay `LIVE`, emit `TruthDelta(MAP)`
- on `MATCH_ENDED` -> `POST_MATCH_PENDING_CONFIRM`, set winner, confidence=0.80

### PAUSED
- on `RESUMED` -> `LIVE`
- on `MATCH_ENDED` -> `POST_MATCH_PENDING_CONFIRM`
- on other events -> ignore (or buffer)

### POST_MATCH_PENDING_CONFIRM
- repeated `MATCH_ENDED` (same winner) increases confidence
- scoreboard/terminal condition confirms winner => increases confidence
- contradiction (winner changes) => revert to `LIVE` or reset pending

Finalize when:
- `confidence >= confirm_threshold` (recommend 0.90)
  OR
- `now - ended_at_ms >= max_wait_ms` (recommend 10_000)

Then -> `FINAL`, emit `TruthFinal(winner)` once.

### FINAL
- ignore everything except optional `CORRECTION`

---

## Confidence accumulation (practical)
- initial `MATCH_ENDED`: 0.80
- second consistent `MATCH_ENDED` within 5s: +0.10 (cap 0.95)
- final scoreboard snapshot consistent: +0.05 (cap 1.00)

---

## Determinism + ordering
1. If provider has `seq`, drop events with `seq <= last_seq`.
2. Else drop events with `ts_ms < last_ts_ms - allowed_skew_ms` (e.g. 2000ms).
3. Keep `seen_event_ids` if provider gives one; else hash `(type, ts_ms, payload)`.

---

## Minimal pseudocode

```
on_event(e):
  if dup(e): return
  if status == PRE_MATCH:
    if e.type == MATCH_STARTED: status=LIVE
    elif e.type == PAUSED: status=PAUSED
    else: return

  elif status == LIVE:
    if e.type == PAUSED: status=PAUSED
    elif e.type == MATCH_ENDED: status=PENDING; winner=...; conf=0.80
    else: update live state; emit TruthDelta

  elif status == PAUSED:
    if e.type == RESUMED: status=LIVE
    elif e.type == MATCH_ENDED: goto pending
    else: return

  elif status == PENDING:
    if consistent end: conf += ...
    if conf >= 0.90 or now-ended>=10s: status=FINAL; emit TruthFinal

  elif status == FINAL:
    return
```

---

## Strategy-facing helpers
Expose booleans:
- `is_live`, `is_paused`, `is_effectively_final`, `winner_if_final`
