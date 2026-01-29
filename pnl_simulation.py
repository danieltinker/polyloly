"""
PnL simulation for Binary Pair Arbitrage safety margins.

- No external dependencies.
- Stylized price process: mean-reverting around 0.50 with shocky swings.
- Strategy buys cheaper side in fixed USDC steps, respecting pair_cost_cap.
"""
from __future__ import annotations
import random
import statistics
from dataclasses import dataclass
from typing import List, Tuple, Dict
from interfaces import PairPosition, Side, should_buy_more

@dataclass
class SimConfig:
    episodes: int = 2000
    steps_per_episode: int = 120
    fee_rate: float = 0.02
    step_usdc: float = 25.0
    max_usdc_per_market: float = 1500.0
    slippage_bps: float = 5.0
    volatility: float = 0.06
    mean_revert: float = 0.05

def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

def evolve_price(p_yes: float, cfg: SimConfig) -> float:
    shock = random.gauss(0.0, cfg.volatility)
    drift = (0.5 - p_yes) * cfg.mean_revert
    return clamp01(p_yes + drift + shock)

def apply_slippage(price: float, slippage_bps: float) -> float:
    return clamp01(price * (1.0 + slippage_bps / 10000.0))

def run_episode(pair_cost_cap: float, cfg: SimConfig) -> Tuple[float, float, float]:
    pos = PairPosition(fee_rate=cfg.fee_rate)
    p_yes = 0.5

    for _ in range(cfg.steps_per_episode):
        p_yes = evolve_price(p_yes, cfg)
        p_no = clamp01(1.0 - p_yes)

        # Prefer balancing legs; if balanced, buy cheaper side
        if pos.q_yes < pos.q_no:
            side, price = Side.YES, p_yes
        elif pos.q_no < pos.q_yes:
            side, price = Side.NO, p_no
        else:
            side = Side.YES if p_yes < p_no else Side.NO
            price = p_yes if side == Side.YES else p_no

        price = apply_slippage(price, cfg.slippage_bps)

        if should_buy_more(
            pos, side, cfg.step_usdc, price,
            pair_cost_cap=pair_cost_cap,
            max_total_cost=cfg.max_usdc_per_market,
            require_improve=True
        ):
            pos = pos.hypo_buy(side, cfg.step_usdc, price)

        # stop early if comfortably locked (optional)
        pc = pos.pair_cost_avg()
        if pc is not None and pos.guaranteed_pnl() > 0 and pc < (1.0 - cfg.fee_rate - 0.005):
            break

    spent = pos.total_cost()
    gpn = pos.guaranteed_pnl()
    pc = pos.pair_cost_avg()
    return spent, gpn, (pc if pc is not None else -1.0)

def summarize(results: List[Tuple[float,float,float]]) -> Dict[str, float]:
    pnl = [r[1] for r in results]
    spent = [r[0] for r in results]
    pnl_per_spent = [(r[1]/r[0]) if r[0] > 0 else 0.0 for r in results]

    def pct(xs, q):
        xs2 = sorted(xs)
        idx = int((len(xs2)-1) * q)
        return xs2[idx]

    return {
        "episodes": len(results),
        "mean_spent": statistics.mean(spent),
        "mean_pnl": statistics.mean(pnl),
        "median_pnl": statistics.median(pnl),
        "p5_pnl": pct(pnl, 0.05),
        "p95_pnl": pct(pnl, 0.95),
        "pos_pnl_rate": sum(1 for x in pnl if x > 0) / len(pnl),
        "mean_pnl_per_spent": statistics.mean(pnl_per_spent),
    }

def main():
    cfg = SimConfig()
    caps = [0.99, 0.985, 0.98, 0.975, 0.97]

    print("=== Binary Pair Arb Safety Margin Simulation ===")
    print(cfg)
    print()

    for cap in caps:
        results = [run_episode(cap, cfg) for _ in range(cfg.episodes)]
        s = summarize(results)
        print(
            f"pair_cost_cap={cap:.3f}  ->  "
            f"mean_pnl={s['mean_pnl']:.2f}  "
            f"median_pnl={s['median_pnl']:.2f}  "
            f"p5={s['p5_pnl']:.2f}  "
            f"pos_rate={s['pos_pnl_rate']*100:.1f}%  "
            f"mean_pnl/spent={s['mean_pnl_per_spent']*100:.2f}%  "
            f"mean_spent={s['mean_spent']:.2f}"
        )

if __name__ == "__main__":
    main()
