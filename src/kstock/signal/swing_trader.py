"""Swing trading signal evaluator (Section 44 - 스윙 트레이딩).

Evaluates short-term swing trade opportunities using technical
indicators (RSI, Bollinger Band %B, volume, MACD) combined with
confidence scoring and ML probability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SWING_RULES: dict = {
    "hold_period_days": {"min": 3, "max": 10},
    "target_pct": {"min": 5.0, "max": 15.0},
    "stop_pct": -5.0,
    "min_confidence": 100.0,
    "min_ml_prob": 0.65,
    "min_conditions": 3,
}
"""Swing trading rules.

- Hold period: 3-10 business days
- Target: +5~15%
- Stop: -5%
- Min confidence: 100 points
- ML probability: 65%+
- At least 3 qualifying conditions required.
"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SwingSignal:
    """Swing trade entry signal."""

    ticker: str
    name: str
    entry_price: float
    target_price: float
    stop_price: float
    target_pct: float
    stop_pct: float
    hold_days: int
    confidence: float
    reasons: list[str] = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_swing(
    ticker: str,
    name: str,
    current_price: float,
    rsi: float,
    bb_pctb: float,
    volume_ratio_20d: float,
    macd_signal_cross: int,
    confidence_score: float,
    ml_prob: float = 0.5,
) -> SwingSignal | None:
    """Evaluate whether *ticker* qualifies for a swing trade entry.

    A signal is generated when 3 or more of the following conditions
    are satisfied:

    1. RSI <= 30          (과매도 반등)
    2. BB %B <= 0.2       (볼린저 하단)
    3. volume_ratio >= 2.0 (거래량 급증)
    4. MACD signal cross = 1 (골든크로스)
    5. confidence >= 100
    6. ml_prob >= 0.65

    Args:
        ticker: Stock ticker code.
        name: Human-readable stock name.
        current_price: Current market price.
        rsi: RSI(14) value.
        bb_pctb: Bollinger Band %B (0-1 scale).
        volume_ratio_20d: Current volume / 20-day average volume.
        macd_signal_cross: 1 if MACD crossed above signal, 0 otherwise.
        confidence_score: Composite confidence score.
        ml_prob: ML model probability of positive return (0-1).

    Returns:
        SwingSignal if the ticker qualifies, None otherwise.
    """
    reasons: list[str] = []

    # Condition checks
    if rsi <= 30:
        reasons.append(f"과매도 반등 (RSI {rsi:.1f})")

    if bb_pctb <= 0.2:
        reasons.append(f"볼린저 하단 (%B {bb_pctb:.2f})")

    if volume_ratio_20d >= 2.0:
        reasons.append(f"거래량 급증 ({volume_ratio_20d:.1f}배)")

    if macd_signal_cross == 1:
        reasons.append("MACD 골든크로스")

    if confidence_score >= SWING_RULES["min_confidence"]:
        reasons.append(f"신뢰도 {confidence_score:.0f}점")

    if ml_prob >= SWING_RULES["min_ml_prob"]:
        reasons.append(f"ML 확률 {ml_prob:.0%}")

    # Need at least 3 conditions
    min_conditions = SWING_RULES["min_conditions"]
    if len(reasons) < min_conditions:
        logger.debug(
            "Swing %s(%s): %d/%d conditions met, skipping. reasons=%s",
            name, ticker, len(reasons), min_conditions, reasons,
        )
        return None

    # Compute target and stop
    target_pct = 10.0  # default +10%
    stop_pct = -5.0

    target_price = round(current_price * (1 + target_pct / 100))
    stop_price = round(current_price * (1 + stop_pct / 100))

    # Estimate hold period (5-7 days typical)
    hold_days = 7 if confidence_score >= 120 else 5

    signal = SwingSignal(
        ticker=ticker,
        name=name,
        entry_price=current_price,
        target_price=target_price,
        stop_price=stop_price,
        target_pct=target_pct,
        stop_pct=stop_pct,
        hold_days=hold_days,
        confidence=confidence_score,
        reasons=reasons,
    )
    signal.message = format_swing_alert(signal)

    logger.info(
        "Swing signal %s(%s): entry=%,.0f target=%,.0f(+%.0f%%) "
        "stop=%,.0f(%.0f%%) hold=%dd, %d reasons",
        name, ticker, current_price, target_price, target_pct,
        stop_price, stop_pct, hold_days, len(reasons),
    )

    return signal


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

def compute_swing_size(
    total_eval: float,
    swing_allocation_pct: float = 25.0,
) -> float:
    """Compute maximum amount for a single swing trade.

    Args:
        total_eval: Total portfolio evaluation (KRW).
        swing_allocation_pct: Percentage of portfolio allocated to swing
            trading (default 25%).

    Returns:
        Maximum KRW amount for one swing trade position.
    """
    max_amount = total_eval * (swing_allocation_pct / 100)

    logger.debug(
        "Swing size: total=%,.0f * %.0f%% = %,.0f",
        total_eval, swing_allocation_pct, max_amount,
    )

    return max_amount


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_swing_alert(signal: SwingSignal) -> str:
    """Format a SwingSignal as a Telegram alert message.

    Example output::

        스윙 매수 추천
        종목: SK하이닉스  목표 보유: 5~7일
        진입: 52,000원
        목표: 57,200원 (+10%)
        손절: 49,400원 (-5%)
        근거: 과매도 반등 (RSI 28.5), 볼린저 하단 (%B 0.15), 거래량 급증 (2.3배)

    No ** bold formatting is used.

    Args:
        signal: SwingSignal to format.

    Returns:
        Multi-line formatted string for Telegram.
    """
    hold_label = f"{signal.hold_days - 2}~{signal.hold_days}일"

    lines = [
        "스윙 매수 추천",
        f"종목: {signal.name}  목표 보유: {hold_label}",
        f"진입: {signal.entry_price:,.0f}원",
        f"목표: {signal.target_price:,.0f}원 (+{signal.target_pct:.0f}%)",
        f"손절: {signal.stop_price:,.0f}원 ({signal.stop_pct:.0f}%)",
    ]

    if signal.reasons:
        lines.append(f"근거: {', '.join(signal.reasons)}")

    return "\n".join(lines)
