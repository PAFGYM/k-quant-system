"""Seed position manager for future technology stocks - K-Quant v3.5.

Manages small "seed" positions in future tech stocks with strict limits:
- Max 15% total future tech exposure
- Max 5% per sector
- Max 2% per stock
- Scale-up at +15%, cut loss at -10%

Rules:
- No ** bold, no Markdown parse_mode
- Korean responses only
- "주호님" personalized greeting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kstock.signal.future_tech import (
    FUTURE_SECTORS,
    TIER_CONFIG,
    get_ticker_info,
)

logger = logging.getLogger(__name__)

USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEED_CONFIG = {
    "max_future_ratio": 0.15,          # 미래기술 전체 최대 15%
    "max_per_sector": 0.05,            # 섹터당 최대 5%
    "max_per_stock": 0.02,             # 종목당 최대 2%
    "min_seed_amount": 500_000,        # 최소 50만원
    "max_seed_amount": 3_000_000,      # 최대 300만원 (씨앗)
    "scale_up_trigger": 0.15,          # +15% 수익 시 추가매수 검토
    "cut_loss_trigger": -0.10,         # -10% 손절
}

TIER_WEIGHT_LIMITS = {
    "tier1_platform": {"min": 0.03, "max": 0.05},
    "tier2_core": {"min": 0.01, "max": 0.03},
    "tier3_emerging": {"min": 0.005, "max": 0.01},
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SeedPosition:
    """A seed position in a future tech stock."""

    ticker: str = ""
    name: str = ""
    sector: str = ""
    tier: str = ""
    avg_price: float = 0.0
    current_price: float = 0.0
    quantity: int = 0
    eval_amount: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0


@dataclass
class SeedAction:
    """Recommended action for a seed position."""

    ticker: str = ""
    name: str = ""
    action: str = ""  # scale_up, cut_loss, hold, initial_buy
    urgency: str = "normal"  # normal, high, critical
    message: str = ""
    details: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Limit checking
# ---------------------------------------------------------------------------

def check_future_limits(
    total_portfolio_value: float,
    future_positions: list[SeedPosition],
    new_ticker: str | None = None,
    new_amount: float = 0.0,
) -> dict[str, Any]:
    """Check if future tech position limits are satisfied.

    Returns dict with:
      - allowed: bool
      - total_future_pct: float
      - sector_pcts: dict
      - violations: list[str]
    """
    if total_portfolio_value <= 0:
        return {
            "allowed": False,
            "total_future_pct": 0,
            "sector_pcts": {},
            "violations": ["포트폴리오 가치가 0입니다"],
        }

    # Calculate current exposure
    sector_totals: dict[str, float] = {}
    stock_totals: dict[str, float] = {}
    total_future = 0.0

    for pos in future_positions:
        total_future += pos.eval_amount
        sector_totals[pos.sector] = sector_totals.get(pos.sector, 0) + pos.eval_amount
        stock_totals[pos.ticker] = stock_totals.get(pos.ticker, 0) + pos.eval_amount

    # Add proposed new position
    if new_ticker and new_amount > 0:
        info = get_ticker_info(new_ticker)
        new_sector = info["sector"] if info else ""
        total_future += new_amount
        if new_sector:
            sector_totals[new_sector] = sector_totals.get(new_sector, 0) + new_amount
        stock_totals[new_ticker] = stock_totals.get(new_ticker, 0) + new_amount

    total_future_pct = total_future / total_portfolio_value
    sector_pcts = {s: v / total_portfolio_value for s, v in sector_totals.items()}
    stock_pcts = {t: v / total_portfolio_value for t, v in stock_totals.items()}

    violations: list[str] = []

    # Check total limit
    if total_future_pct > SEED_CONFIG["max_future_ratio"]:
        violations.append(
            f"미래기술 총 비중 {total_future_pct:.1%} > 한도 {SEED_CONFIG['max_future_ratio']:.0%}"
        )

    # Check per-sector limit
    for sector, pct in sector_pcts.items():
        if pct > SEED_CONFIG["max_per_sector"]:
            sector_name = FUTURE_SECTORS.get(sector, {}).get("name", sector)
            violations.append(
                f"{sector_name} 비중 {pct:.1%} > 한도 {SEED_CONFIG['max_per_sector']:.0%}"
            )

    # Check per-stock limit
    for ticker, pct in stock_pcts.items():
        if pct > SEED_CONFIG["max_per_stock"]:
            info = get_ticker_info(ticker)
            name = info["name"] if info else ticker
            violations.append(
                f"{name} 비중 {pct:.1%} > 한도 {SEED_CONFIG['max_per_stock']:.0%}"
            )

    # Check seed amount limits for new position
    if new_amount > 0:
        if new_amount < SEED_CONFIG["min_seed_amount"]:
            violations.append(
                f"최소 씨앗 금액 {SEED_CONFIG['min_seed_amount']:,}원 미만"
            )
        if new_amount > SEED_CONFIG["max_seed_amount"]:
            violations.append(
                f"최대 씨앗 금액 {SEED_CONFIG['max_seed_amount']:,}원 초과"
            )

    return {
        "allowed": len(violations) == 0,
        "total_future_pct": total_future_pct,
        "sector_pcts": sector_pcts,
        "violations": violations,
    }


def compute_seed_amount(
    ticker: str,
    total_portfolio_value: float,
) -> dict[str, Any]:
    """Compute recommended seed buy amount based on tier limits.

    Returns dict with min_amount, max_amount, recommended.
    """
    info = get_ticker_info(ticker)
    tier = info["tier"] if info else "tier3_emerging"

    tier_limits = TIER_WEIGHT_LIMITS.get(tier, TIER_WEIGHT_LIMITS["tier3_emerging"])
    min_pct = tier_limits["min"]
    max_pct = tier_limits["max"]

    raw_min = total_portfolio_value * min_pct
    raw_max = total_portfolio_value * max_pct

    # Clamp to seed config
    final_min = max(SEED_CONFIG["min_seed_amount"], raw_min)
    final_max = min(SEED_CONFIG["max_seed_amount"], raw_max)
    recommended = round((final_min + final_max) / 2, -4)  # Round to 10k

    return {
        "min_amount": final_min,
        "max_amount": final_max,
        "recommended": recommended,
        "tier": tier,
        "weight_range": f"{min_pct*100:.1f}~{max_pct*100:.1f}%",
    }


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def evaluate_seed_position(
    position: SeedPosition,
    future_score: int = 0,
    trigger_active: bool = False,
    sector_weight_pct: float = 0.0,
) -> SeedAction:
    """Evaluate an existing seed position and recommend action."""
    gain = position.unrealized_pnl_pct

    if gain >= SEED_CONFIG["scale_up_trigger"]:
        # Scale-up evaluation
        details = [
            f"씨앗 매수가: {position.avg_price:,.0f}원",
            f"현재가: {position.current_price:,.0f}원",
            f"수익: +{position.unrealized_pnl:,.0f}원 ({gain:+.1%})",
            f"미래기술 스코어: {future_score}/100",
            f"트리거 지속: {'예' if trigger_active else '아니오'}",
            f"섹터 비중: {sector_weight_pct:.1%} / 한도 5%",
        ]
        return SeedAction(
            ticker=position.ticker,
            name=position.name,
            action="scale_up",
            urgency="normal",
            message=(
                f"{USER_NAME}, {position.name} 씨앗이 자라고 있습니다!\n"
                f"수익 {gain:+.1%} 달성. 추가매수로 키울지, 익절할지 결정하세요."
            ),
            details=details,
        )

    elif gain <= SEED_CONFIG["cut_loss_trigger"]:
        # Cut loss evaluation
        trigger_status = "유효" if trigger_active else "소멸"
        if trigger_active:
            action = "hold"
            urgency = "high"
            msg = (
                f"{USER_NAME}, {position.name} 손실 {gain:+.1%}이지만 "
                f"트리거가 아직 유효합니다. 홀딩 유지하세요."
            )
        else:
            action = "cut_loss"
            urgency = "critical"
            msg = (
                f"{USER_NAME}, {position.name} 손실 {gain:+.1%}. "
                f"트리거도 소멸되었습니다. 손절을 권장합니다."
            )

        details = [
            f"매수가: {position.avg_price:,.0f}원",
            f"현재가: {position.current_price:,.0f}원",
            f"손실: {position.unrealized_pnl:,.0f}원",
            f"트리거 상태: {trigger_status}",
        ]

        return SeedAction(
            ticker=position.ticker,
            name=position.name,
            action=action,
            urgency=urgency,
            message=msg,
            details=details,
        )

    else:
        # Hold
        return SeedAction(
            ticker=position.ticker,
            name=position.name,
            action="hold",
            urgency="normal",
            message=f"{position.name} 씨앗 유지 중 ({gain:+.1%})",
            details=[
                f"현재 수익: {gain:+.1%}",
                f"추가매수 트리거: +{SEED_CONFIG['scale_up_trigger']:.0%}",
                f"손절 트리거: {SEED_CONFIG['cut_loss_trigger']:.0%}",
            ],
        )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_seed_alert(action: SeedAction) -> str:
    """Format seed position alert for Telegram."""
    urgency_emoji = {
        "normal": "\U0001f331",
        "high": "\u26a0\ufe0f",
        "critical": "\U0001f6a8",
    }
    action_labels = {
        "scale_up": "씨앗 성장",
        "cut_loss": "씨앗 손절",
        "hold": "씨앗 유지",
        "initial_buy": "씨앗 매수",
    }

    emoji = urgency_emoji.get(action.urgency, "\U0001f331")
    label = action_labels.get(action.action, action.action)

    lines: list[str] = [
        f"{emoji} [{label}] {action.name}",
        "",
    ]

    for detail in action.details:
        lines.append(f"  {detail}")

    lines.append("")
    lines.append(action.message)

    return "\n".join(lines)


def format_seed_overview(
    positions: list[SeedPosition],
    total_portfolio_value: float,
) -> str:
    """Format overview of all seed positions."""
    if not positions:
        return (
            "\U0001f331 미래기술 씨앗 포지션\n\n"
            "현재 씨앗 포지션이 없습니다.\n"
            "/future 로 워치리스트를 확인하세요."
        )

    total_eval = sum(p.eval_amount for p in positions)
    total_pnl = sum(p.unrealized_pnl for p in positions)
    total_pct = total_eval / total_portfolio_value if total_portfolio_value > 0 else 0

    lines: list[str] = [
        "\U0001f331 미래기술 씨앗 포지션",
        "",
        f"총 씨앗 금액: {total_eval:,.0f}원 (포트폴리오의 {total_pct:.1%})",
        f"총 수익: {total_pnl:+,.0f}원",
        "",
    ]

    # Group by sector
    by_sector: dict[str, list[SeedPosition]] = {}
    for p in positions:
        by_sector.setdefault(p.sector, []).append(p)

    for sector_key, sector_positions in by_sector.items():
        sector = FUTURE_SECTORS.get(sector_key, {})
        sector_name = sector.get("name", sector_key)
        emoji = sector.get("emoji", "")
        sector_eval = sum(p.eval_amount for p in sector_positions)
        sector_pct = sector_eval / total_portfolio_value if total_portfolio_value > 0 else 0

        lines.append(f"{emoji} {sector_name} (비중 {sector_pct:.1%}/5%)")
        for p in sector_positions:
            pnl_emoji = "\U0001f7e2" if p.unrealized_pnl_pct >= 0 else "\U0001f534"
            lines.append(
                f"  {pnl_emoji} {p.name}: {p.unrealized_pnl_pct:+.1%} "
                f"({p.eval_amount:,.0f}원)"
            )
        lines.append("")

    lines.append(f"미래기술 비중 한도: {total_pct:.1%} / {SEED_CONFIG['max_future_ratio']:.0%}")

    return "\n".join(lines)
