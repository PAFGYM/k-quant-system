"""Profit protection system (Section 37 - 수익 종목 보호).

Prevents premature selling of profitable positions by applying
tier-based trailing stops and sell-blocking rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ProtectionRule:
    """Rule for a profit protection tier."""

    tier: str               # "A_high" / "A_mid" / "A_low" / "B"
    trailing_stop_pct: float  # trailing stop percentage (negative)
    max_sell_pct: float     # maximum allowed sell percentage (0 = no sell)
    message: str


@dataclass
class ProfitProtection:
    """Computed protection result for a single holding."""

    ticker: str
    name: str
    profit_pct: float
    tier: str
    trailing_stop_pct: float
    trailing_stop_price: float
    max_sell_pct: float
    additional_upside: dict = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Protection tier definitions
# ---------------------------------------------------------------------------
PROTECTION_TIERS: list[ProtectionRule] = [
    ProtectionRule(
        tier="A_high",
        trailing_stop_pct=-10.0,
        max_sell_pct=30.0,
        message="절대 전량 매도 금지",
    ),
    ProtectionRule(
        tier="A_mid",
        trailing_stop_pct=-8.0,
        max_sell_pct=50.0,
        message="홀딩 추천, 1차 익절 +10% 추가 시",
    ),
    ProtectionRule(
        tier="A_low",
        trailing_stop_pct=-7.0,
        max_sell_pct=0.0,
        message="홀딩, 수익 +5% 이하 시 경고",
    ),
    ProtectionRule(
        tier="B",
        trailing_stop_pct=0.0,
        max_sell_pct=0.0,
        message="좀 더 지켜보세요",
    ),
]


def _get_tier(profit_pct: float) -> ProtectionRule:
    """Return the protection rule matching the profit percentage.

    Tiers:
        +50% 이상  -> A_high: trailing -10%, max sell 30%
        +20~50%   -> A_mid:  trailing -8%,  max sell 50%
        +5~20%    -> A_low:  trailing -7%,  no sell
        0~5%      -> B:      no trailing,   no sell
    """
    if profit_pct >= 50:
        return PROTECTION_TIERS[0]  # A_high
    if profit_pct >= 20:
        return PROTECTION_TIERS[1]  # A_mid
    if profit_pct >= 5:
        return PROTECTION_TIERS[2]  # A_low
    return PROTECTION_TIERS[3]      # B


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------
def compute_protection(
    ticker: str,
    name: str,
    profit_pct: float,
    current_price: float,
    high_price: float,
    sector_trend: str = "neutral",
    ml_prob: float = 0.5,
) -> ProfitProtection:
    """Compute profit protection parameters for a holding.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        profit_pct: Current profit percentage.
        current_price: Current price of the stock.
        high_price: Highest price since purchase (for trailing stop).
        sector_trend: Sector trend ("bullish" / "neutral" / "bearish").
        ml_prob: ML-predicted probability of further upside (0~1).

    Returns:
        ProfitProtection with trailing stop price and recommendations.
    """
    rule = _get_tier(profit_pct)

    # Compute trailing stop price from the high
    if rule.trailing_stop_pct < 0 and high_price > 0:
        trailing_stop_price = round(high_price * (1 + rule.trailing_stop_pct / 100))
    else:
        trailing_stop_price = 0

    # Adjust trailing stop for sector trend
    adjusted_trailing_pct = rule.trailing_stop_pct
    if sector_trend == "bullish" and rule.trailing_stop_pct < 0:
        # Bullish sector: widen trailing stop by 2% (give more room)
        adjusted_trailing_pct = rule.trailing_stop_pct - 2.0
        trailing_stop_price = round(high_price * (1 + adjusted_trailing_pct / 100))
    elif sector_trend == "bearish" and rule.trailing_stop_pct < 0:
        # Bearish sector: tighten trailing stop by 2%
        adjusted_trailing_pct = rule.trailing_stop_pct + 2.0
        trailing_stop_price = round(high_price * (1 + adjusted_trailing_pct / 100))

    # Additional upside estimation
    additional_upside: dict[str, float] = {}
    if ml_prob > 0.7:
        additional_upside["ml_upside"] = round((ml_prob - 0.5) * 40, 1)
    if sector_trend == "bullish":
        additional_upside["sector_bonus"] = 5.0
    elif sector_trend == "bearish":
        additional_upside["sector_penalty"] = -5.0

    # Build message
    message = rule.message
    if trailing_stop_price > 0 and current_price <= trailing_stop_price:
        message = f"트레일링 스탑 도달! {name} 일부 매도 검토"
    elif profit_pct >= 50 and ml_prob > 0.6:
        message = "대박 종목 계속 들고 가세요"

    protection = ProfitProtection(
        ticker=ticker,
        name=name,
        profit_pct=profit_pct,
        tier=rule.tier,
        trailing_stop_pct=adjusted_trailing_pct,
        trailing_stop_price=trailing_stop_price,
        max_sell_pct=rule.max_sell_pct,
        additional_upside=additional_upside,
        message=message,
    )

    logger.info(
        "Protection: %s (%s) profit=%.1f%% tier=%s trailing_stop=%d",
        name, ticker, profit_pct, rule.tier, trailing_stop_price,
    )
    return protection


# ---------------------------------------------------------------------------
# Sell blocking
# ---------------------------------------------------------------------------
def should_block_sell(
    profit_pct: float,
    sell_pct: float,
) -> tuple[bool, str]:
    """Determine if a sell order should be blocked.

    Args:
        profit_pct: Current profit percentage of the holding.
        sell_pct: Percentage of holding the user wants to sell (0~100).

    Returns:
        Tuple of (should_block, reason_message).
        If should_block is True, the sell should be prevented or warned.
    """
    if profit_pct >= 50 and sell_pct > 30:
        return (
            True,
            "수익 +50% 종목은 최대 30%만 매도 가능",
        )

    if profit_pct >= 5 and sell_pct == 100:
        return (
            True,
            "수익 종목 전량 매도 비추천",
        )

    return (False, "")


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------
def format_profit_report(protections: list[ProfitProtection]) -> str:
    """Format profit protection report for Telegram.

    Example output:
        수익 종목 현황
        에코프로 +88%  트레일링 스탑: 154,000원
        → 계속 들고 가세요
    """
    if not protections:
        return ""

    lines: list[str] = []
    lines.append("\U0001f6e1\ufe0f 수익 종목 현황")
    lines.append("")

    # Sort by profit descending
    sorted_prots = sorted(protections, key=lambda p: p.profit_pct, reverse=True)

    for p in sorted_prots:
        # Emoji based on tier
        if p.tier == "A_high":
            emoji = "\U0001f525"  # fire
        elif p.tier == "A_mid":
            emoji = "\U0001f4aa"  # flexed biceps
        elif p.tier == "A_low":
            emoji = "\U0001f7e2"  # green circle
        else:
            emoji = "\u26aa"     # white circle

        profit_str = f"+{p.profit_pct:.0f}%" if p.profit_pct >= 0 else f"{p.profit_pct:.0f}%"

        line = f"{emoji} {p.name} {profit_str}"
        if p.trailing_stop_price > 0:
            line += f"  트레일링 스탑: {p.trailing_stop_price:,}원"
        lines.append(line)

        # Action message
        lines.append(f"  \u2192 {p.message}")

        # Max sell info
        if p.max_sell_pct > 0:
            lines.append(f"  \u2192 최대 매도: {p.max_sell_pct:.0f}%")

        lines.append("")

    return "\n".join(lines)
