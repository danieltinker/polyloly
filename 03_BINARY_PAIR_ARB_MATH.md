# 03 — Binary Pair Arbitrage Math Module (Pure, Testable)

## Definitions
- Buy YES and NO shares at prices in [0,1].
- At resolution: one side pays $1/share, the other pays $0.
- Polymarket winner fee reduces payout by `fee_rate` (e.g. 2%).

### Guaranteed payout
```
q_min = min(q_yes, q_no)
payout_net = q_min * (1 - fee_rate)
```

### Guaranteed PnL
```
guaranteed_pnl = payout_net - (c_yes + c_no)
```

You have a risk-free edge when `guaranteed_pnl > 0`.

---

## Average costs
```
avg_yes = c_yes / q_yes   (if q_yes>0)
avg_no  = c_no  / q_no    (if q_no>0)
pair_cost_avg = avg_yes + avg_no
```

If quantities are balanced (q_yes≈q_no), net profit-per-pair is:
```
(1 - fee_rate) - pair_cost_avg
```

So you typically require:
```
pair_cost_avg < 1 - fee_rate
```

With 2% fee:
```
pair_cost_avg < 0.98
```

---

## Decision rule for incremental buying

Given a candidate buy: `(side, usdc_amount, price)`:

1. Reject if `total_cost + usdc_amount > max_total_cost`.
2. Compute hypothetical new position after the buy.
3. If both legs exist:
   - reject if `pair_cost_avg >= (1 - fee_rate)`
   - reject if `pair_cost_avg >= pair_cost_cap` (stricter cap, e.g. 0.985)
4. Reject if `guaranteed_pnl` does not strictly improve.

This keeps you marching toward a *locked* positive PnL state.

---

## Implementation file
See `interfaces.py` for:
- `PairPosition`
- `should_buy_more(...)`
