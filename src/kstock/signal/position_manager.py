"""Position manager for staged buy/sell planning (Section 38 - 분할매수/분할매도 자동 계획).

Generates phased entry and exit plans based on confidence scores
and current position profitability.  Also provides portfolio
allocation templates by risk mode.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BuyPlan:
    """Phased buy plan for a single ticker.

    Each entry contains:
        phase (int): 1/2/3 phase number.
        price (float): Target entry price.
        quantity (int): Number of shares.
        amount (float): Total cost for this phase.
        condition (str): Trigger condition description.
    """

    ticker: str
    name: str
    total_budget: float
    entries: list[dict] = field(default_factory=list)
    message: str = ""


@dataclass
class SellPlan:
    """Phased sell plan for a single ticker.

    Each entry contains:
        phase (int): 1/2/3 phase number.
        quantity_pct (float): Percentage of total position to sell.
        condition (str): Trigger condition description.
        price (float | None): Target price, if applicable.
    """

    ticker: str
    name: str
    profit_pct: float
    entries: list[dict] = field(default_factory=list)
    message: str = ""


@dataclass
class PositionAllocation:
    """Portfolio allocation percentages by category."""

    core_holding_pct: float = 55.0
    swing_pct: float = 25.0
    leverage_pct: float = 15.0
    cash_pct: float = 5.0


# ---------------------------------------------------------------------------
# Buy planning
# ---------------------------------------------------------------------------

def plan_buy(
    ticker: str,
    name: str,
    current_price: float,
    budget: float,
    confidence_score: float = 100.0,
) -> BuyPlan:
    """Create a phased buy plan for *ticker*.

    Phase allocation ratios are adjusted by confidence:
        - confidence > 130  -> 70 / 20 / 10  (high conviction front-load)
        - confidence < 90   -> 30 / 30 / 40  (cautious, average down)
        - otherwise         -> 50 / 30 / 20  (default balanced)

    Args:
        ticker: Stock ticker code.
        name: Human-readable stock name.
        current_price: Current market price.
        budget: Total KRW budget for this position.
        confidence_score: Composite confidence score (0-200 scale).

    Returns:
        BuyPlan with up to three phased entries.
    """
    if confidence_score > 130:
        ratios = (0.70, 0.20, 0.10)
    elif confidence_score < 90:
        ratios = (0.30, 0.30, 0.40)
    else:
        ratios = (0.50, 0.30, 0.20)

    phase1_price = current_price
    phase2_price = round(current_price * 0.97)  # -3% pullback
    phase3_price = current_price  # additional confirmation at current level

    phase1_amount = budget * ratios[0]
    phase2_amount = budget * ratios[1]
    phase3_amount = budget * ratios[2]

    phase1_qty = max(1, math.floor(phase1_amount / phase1_price)) if phase1_price > 0 else 0
    phase2_qty = max(1, math.floor(phase2_amount / phase2_price)) if phase2_price > 0 else 0
    phase3_qty = max(1, math.floor(phase3_amount / phase3_price)) if phase3_price > 0 else 0

    entries = [
        {
            "phase": 1,
            "price": phase1_price,
            "quantity": phase1_qty,
            "amount": phase1_price * phase1_qty,
            "condition": "시그널 확인 시",
        },
        {
            "phase": 2,
            "price": phase2_price,
            "quantity": phase2_qty,
            "amount": phase2_price * phase2_qty,
            "condition": "풀백 시 -3%",
        },
        {
            "phase": 3,
            "price": phase3_price,
            "quantity": phase3_qty,
            "amount": phase3_price * phase3_qty,
            "condition": "추가 확인 후 진입",
        },
    ]

    total_used = sum(e["amount"] for e in entries)
    message = format_buy_plan(
        BuyPlan(ticker=ticker, name=name, total_budget=budget, entries=entries)
    )

    logger.info(
        "BuyPlan %s(%s): confidence=%.0f, budget=%,.0f, "
        "ratios=%.0f/%.0f/%.0f, total_used=%,.0f",
        name, ticker, confidence_score, budget,
        ratios[0] * 100, ratios[1] * 100, ratios[2] * 100,
        total_used,
    )

    return BuyPlan(
        ticker=ticker,
        name=name,
        total_budget=budget,
        entries=entries,
        message=message,
    )


# ---------------------------------------------------------------------------
# Sell planning
# ---------------------------------------------------------------------------

def plan_sell_profit(
    ticker: str,
    name: str,
    profit_pct: float,
    current_price: float,
    quantity: int,
) -> SellPlan:
    """Create a phased sell plan for *ticker* based on current profit.

    For profitable positions (profit_pct >= 0):
        Phase 1: 30% at target price (목표가 도달)
        Phase 2: 30% trailing (추가 상승 시 트레일링)
        Phase 3: 40% trailing stop protection (트레일링 스톱 보호)

    For losing positions (profit_pct < 0):
        Option A: 100% stop loss (전량 손절)
        Option B: 50% stop loss, hold rest (반 손절, 나머지 보유)

    Args:
        ticker: Stock ticker code.
        name: Human-readable stock name.
        profit_pct: Current unrealized profit/loss percentage.
        current_price: Current market price.
        quantity: Total number of shares held.

    Returns:
        SellPlan with phased exit entries.
    """
    entries: list[dict] = []

    if profit_pct >= 0:
        # Profitable position -- phased profit-taking
        entries = [
            {
                "phase": 1,
                "quantity_pct": 30.0,
                "condition": "목표가 도달",
                "price": current_price,
            },
            {
                "phase": 2,
                "quantity_pct": 30.0,
                "condition": "추가 상승 시 트레일링",
                "price": None,
            },
            {
                "phase": 3,
                "quantity_pct": 40.0,
                "condition": "트레일링 스톱 보호",
                "price": None,
            },
        ]
    else:
        # Losing position -- stop-loss options
        entries = [
            {
                "phase": 1,
                "quantity_pct": 100.0,
                "condition": "Option A: 전량 손절",
                "price": current_price,
            },
            {
                "phase": 2,
                "quantity_pct": 50.0,
                "condition": "Option B: 반 손절, 나머지 보유",
                "price": current_price,
            },
        ]

    message = format_sell_plan(
        SellPlan(ticker=ticker, name=name, profit_pct=profit_pct, entries=entries)
    )

    logger.info(
        "SellPlan %s(%s): profit=%.1f%%, quantity=%d, phases=%d",
        name, ticker, profit_pct, quantity, len(entries),
    )

    return SellPlan(
        ticker=ticker,
        name=name,
        profit_pct=profit_pct,
        entries=entries,
        message=message,
    )


# ---------------------------------------------------------------------------
# Portfolio allocation
# ---------------------------------------------------------------------------

def get_position_allocation(mode: str = "balanced") -> PositionAllocation:
    """Return portfolio allocation template by risk mode.

    Modes:
        balanced:   core 55, swing 25, leverage 0, cash 20
        aggressive: core 55, swing 25, leverage 15, cash 5
        defensive:  core 40, swing 10, leverage 0, cash 50

    Args:
        mode: One of "balanced", "aggressive", "defensive".

    Returns:
        PositionAllocation with category percentages summing to 100.
    """
    allocations = {
        "balanced": PositionAllocation(
            core_holding_pct=55.0,
            swing_pct=25.0,
            leverage_pct=0.0,
            cash_pct=20.0,
        ),
        "aggressive": PositionAllocation(
            core_holding_pct=55.0,
            swing_pct=25.0,
            leverage_pct=15.0,
            cash_pct=5.0,
        ),
        "defensive": PositionAllocation(
            core_holding_pct=40.0,
            swing_pct=10.0,
            leverage_pct=0.0,
            cash_pct=50.0,
        ),
    }

    allocation = allocations.get(mode)
    if allocation is None:
        logger.warning("Unknown allocation mode '%s', falling back to balanced.", mode)
        allocation = allocations["balanced"]

    logger.debug(
        "Allocation(%s): core=%.0f%% swing=%.0f%% leverage=%.0f%% cash=%.0f%%",
        mode,
        allocation.core_holding_pct,
        allocation.swing_pct,
        allocation.leverage_pct,
        allocation.cash_pct,
    )

    return allocation


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_buy_plan(plan: BuyPlan) -> str:
    """Format a BuyPlan as a Telegram message.

    Example output::

        매수 계획: SK하이닉스
        1차: 900,000원에 10주 (9,000,000원) - 지금
        2차: 870,000원에 5주 - 풀백 시
        총 예산: 15,000,000원

    Args:
        plan: BuyPlan to format.

    Returns:
        Multi-line formatted string for Telegram.
    """
    lines = [f"매수 계획: {plan.name}"]

    for entry in plan.entries:
        phase = entry["phase"]
        price = entry["price"]
        qty = entry["quantity"]
        amount = entry["amount"]
        condition = entry["condition"]

        phase_label = f"{phase}차"
        price_str = f"{price:,.0f}원"
        amount_str = f"{amount:,.0f}원"

        if phase == 1:
            lines.append(
                f"{phase_label}: {price_str}에 {qty}주 ({amount_str}) - 지금"
            )
        else:
            lines.append(
                f"{phase_label}: {price_str}에 {qty}주 - {condition}"
            )

    lines.append(f"총 예산: {plan.total_budget:,.0f}원")

    return "\n".join(lines)


def format_sell_plan(plan: SellPlan) -> str:
    """Format a SellPlan as a Telegram message.

    Args:
        plan: SellPlan to format.

    Returns:
        Multi-line formatted string for Telegram.
    """
    if plan.profit_pct >= 0:
        header = f"매도 계획 (수익 {plan.profit_pct:+.1f}%): {plan.name}"
    else:
        header = f"매도 계획 (손실 {plan.profit_pct:+.1f}%): {plan.name}"

    lines = [header]

    for entry in plan.entries:
        phase = entry["phase"]
        pct = entry["quantity_pct"]
        condition = entry["condition"]
        price = entry.get("price")

        phase_label = f"{phase}차"

        if price is not None:
            lines.append(
                f"{phase_label}: {pct:.0f}% 매도 ({price:,.0f}원) - {condition}"
            )
        else:
            lines.append(
                f"{phase_label}: {pct:.0f}% 매도 - {condition}"
            )

    return "\n".join(lines)
