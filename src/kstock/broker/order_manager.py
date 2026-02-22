"""Order management system stub (v4.0 준비).

Will implement fully automated order execution in future versions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutoOrderPlan:
    """Plan for automated order execution."""

    ticker: str = ""
    side: str = ""  # buy, sell
    quantity: int = 0
    price: float = 0
    order_type: str = "market"
    confirmed: bool = False


def execute_auto(plan: AutoOrderPlan) -> dict:
    """Execute an automated order plan.

    Stub for v4.0. Currently returns not-implemented result.
    """
    return {"status": "not_implemented", "message": "v4.0에서 완전 자동매매 구현 예정"}
