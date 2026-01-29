# Polymarket Algo-Trader Bot — Design Docs Pack

This folder contains **copy/paste-ready** design documentation you can drop into your project.

## Contents
- `01_REPO_STRUCTURE.md` — exact repo layout + module responsibilities
- `02_TRUTH_ENGINE_STATE_MACHINE.md` — esports truth engine (state machine + events + transitions)
- `03_BINARY_PAIR_ARB_MATH.md` — math module spec (formulas + invariants + pseudocode)
- `04_PNL_SIMULATION_SAFETY_MARGINS.md` — how to simulate PnL vs safety margin
- `pnl_simulation.py` — runnable simulation script (no external deps)
- `interfaces.py` — minimal interfaces / dataclasses you can start from

## Conventions
- Prices in **[0, 1]** (e.g. 0.63 == $0.63)
- Costs in **USDC**
- "YES" and "NO" are complementary outcomes for a binary market.
