# 04 — Simulating PnL vs Safety Margins

Run:
```
python pnl_simulation.py
```

It simulates many market episodes and prints summary stats per `pair_cost_cap`:
- mean/median pnl
- 5th/95th percentile
- % of episodes with positive guaranteed PnL
- mean pnl as % of capital spent

Tune:
- `slippage_bps`
- `volatility`
- `step_usdc`
- `max_usdc_per_market`
