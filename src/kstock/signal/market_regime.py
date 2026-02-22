"""Market regime detection including bubble attack mode for K-Quant v3.0."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.policy_engine import has_bullish_policy

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    """Market regime detection result."""

    mode: str  # "bubble_attack", "attack", "balanced", "defense"
    emoji: str
    label: str
    message: str
    allocations: dict
    profit_target_pct: float = 5.0
    trailing_stop_pct: float = -5.0


def detect_regime(
    macro: MacroSnapshot,
    kospi_60d_return: float = 0.0,
    foreign_consecutive_sell_days: int = 0,
    usdkrw_spike: bool = False,
    kospi_daily_drop: float = 0.0,
    today: date | None = None,
) -> RegimeResult:
    """Detect market regime including bubble attack mode.

    Bubble Attack conditions (all 3 must be met):
    1. KOSPI 60-day return > 15%
    2. Active bullish policy event
    3. VIX < 20

    Safety triggers:
    - KOSPI daily -3% -> balanced mode
    - VIX >= 25 -> defense mode
    - 3-day foreign sell + FX spike -> warning
    """
    # Safety overrides first
    if macro.vix >= 25 or macro.regime == "risk_off":
        return RegimeResult(
            mode="defense",
            emoji="\U0001f6e1\ufe0f",
            label="\ubc29\uc5b4 \ubaa8\ub4dc",
            message="\uc9c0\uae08\uc740 \uc0ac\uc9c0 \ub9c8\uc138\uc694. \uc778\ubc84\uc2a4\ub85c \ud5f7\uc9d5\ud558\uc138\uc694",
            allocations={
                "A": 5, "B": 25, "C": 15, "D": 5,
                "E": 15, "F": 0, "G": 0, "cash": 35,
            },
            profit_target_pct=3.0,
            trailing_stop_pct=-3.0,
        )

    if kospi_daily_drop <= -3.0:
        return RegimeResult(
            mode="balanced",
            emoji="\u26a0\ufe0f",
            label="\uae34\uae09 \uade0\ud615 \ubaa8\ub4dc",
            message="KOSPI \uae09\ub77d! \uc2e0\uaddc \ub9e4\uc218 \uc911\ub2e8, \ubcf4\uc720 \uc885\ubaa9 \uc810\uac80",
            allocations={
                "A": 10, "B": 10, "C": 20, "D": 10,
                "E": 15, "F": 5, "G": 5, "cash": 25,
            },
            profit_target_pct=3.0,
            trailing_stop_pct=-5.0,
        )

    # Bubble Attack check
    bullish_policy = has_bullish_policy(today)
    is_bubble = (
        kospi_60d_return > 15
        and bullish_policy
        and macro.vix < 20
    )

    if is_bubble:
        # Check warning conditions
        warning = ""
        if foreign_consecutive_sell_days >= 3 and usdkrw_spike:
            warning = "\n\u26a0\ufe0f \uc678\uc778 3\uc77c \uc5f0\uc18d \uc21c\ub9e4\ub3c4 + \ud658\uc728 \uae09\ub4f1 \uacbd\uace0!"

        return RegimeResult(
            mode="bubble_attack",
            emoji="\U0001f525\U0001f680",
            label="BUBBLE ATTACK",
            message=f"\ubc84\ube14\uc7a5 \uacf5\uaca9 \ubaa8\ub4dc! \ubaa8\uba58\ud140+\ub3cc\ud30c \uc804\ub7b5 \uac15\ud654{warning}",
            allocations={
                "A": 10, "B": 10, "C": 5, "D": 10,
                "E": 5, "F": 30, "G": 20, "cash": 5,
                "trailing_mode": True,
            },
            profit_target_pct=8.0,
            trailing_stop_pct=-7.0,
        )

    if macro.regime == "risk_on" or macro.vix < 15:
        return RegimeResult(
            mode="attack",
            emoji="\U0001f680",
            label="\uacf5\uaca9 \ubaa8\ub4dc",
            message="\uc2dc\uc7a5\uc774 \uc88b\uc2b5\ub2c8\ub2e4. \uc801\uadf9 \ub9e4\uc218 \uad6c\uac04",
            allocations={
                "A": 20, "B": 15, "C": 10, "D": 15,
                "E": 10, "F": 20, "G": 5, "cash": 5,
            },
            profit_target_pct=5.0,
            trailing_stop_pct=-5.0,
        )

    # Balanced (default)
    return RegimeResult(
        mode="balanced",
        emoji="\u2696\ufe0f",
        label="\uade0\ud615 \ubaa8\ub4dc",
        message="\uac1c\ubcc4\uc885\ubaa9 \ubc18\ub4f1 + \uc7a5\uae30 \uc801\ub9bd\uc2dd \ubcd1\ud589",
        allocations={
            "A": 15, "B": 10, "C": 20, "D": 10,
            "E": 15, "F": 10, "G": 5, "cash": 15,
        },
        profit_target_pct=5.0,
        trailing_stop_pct=-5.0,
    )
