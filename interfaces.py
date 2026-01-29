from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class Side(str, Enum):
    YES = "YES"
    NO = "NO"

@dataclass(frozen=True)
class Fill:
    side: Side
    qty: float          # shares
    price: float        # [0,1]
    ts_ms: int = 0

@dataclass
class PairPosition:
    fee_rate: float = 0.02
    q_yes: float = 0.0
    q_no: float = 0.0
    c_yes: float = 0.0
    c_no: float = 0.0

    def apply_fill(self, fill: Fill) -> None:
        if fill.qty <= 0:
            return
        if not (0.0 <= fill.price <= 1.0):
            raise ValueError("price must be in [0,1]")
        if fill.side == Side.YES:
            self.q_yes += fill.qty
            self.c_yes += fill.qty * fill.price
        else:
            self.q_no += fill.qty
            self.c_no += fill.qty * fill.price

    def total_cost(self) -> float:
        return self.c_yes + self.c_no

    def q_min(self) -> float:
        return min(self.q_yes, self.q_no)

    def payout_net(self) -> float:
        return self.q_min() * (1.0 - self.fee_rate)

    def guaranteed_pnl(self) -> float:
        return self.payout_net() - self.total_cost()

    def avg_yes(self) -> Optional[float]:
        return None if self.q_yes <= 0 else self.c_yes / self.q_yes

    def avg_no(self) -> Optional[float]:
        return None if self.q_no <= 0 else self.c_no / self.q_no

    def pair_cost_avg(self) -> Optional[float]:
        ay, an = self.avg_yes(), self.avg_no()
        if ay is None or an is None:
            return None
        return ay + an

    def copy(self) -> "PairPosition":
        return PairPosition(
            fee_rate=self.fee_rate,
            q_yes=self.q_yes, q_no=self.q_no,
            c_yes=self.c_yes, c_no=self.c_no
        )

    def hypo_buy(self, side: Side, usdc_amount: float, price: float) -> "PairPosition":
        if usdc_amount <= 0:
            return self.copy()
        if price <= 0:
            return self.copy()
        qty = usdc_amount / price
        nxt = self.copy()
        nxt.apply_fill(Fill(side=side, qty=qty, price=price))
        return nxt

def should_buy_more(
    pos: PairPosition,
    side: Side,
    usdc_amount: float,
    price: float,
    *,
    pair_cost_cap: float,
    max_total_cost: float,
    require_improve: bool = True,
) -> bool:
    if usdc_amount <= 0:
        return False
    if pos.total_cost() + usdc_amount > max_total_cost:
        return False

    nxt = pos.hypo_buy(side, usdc_amount, price)

    pc = nxt.pair_cost_avg()
    if pc is not None:
        net_cap = 1.0 - nxt.fee_rate
        if pc >= net_cap:
            return False
        if pc >= pair_cost_cap:
            return False

    if require_improve:
        return nxt.guaranteed_pnl() > pos.guaranteed_pnl()
    return True
