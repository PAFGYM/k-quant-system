"""Volatility breakout signal (Section 60 - 변동성 돌파 전략).

Implements Larry Williams' volatility breakout strategy adapted for the
Korean stock market.  The breakout price is computed as:

    breakout_price = open + (prev_high - prev_low) * K

A buy signal is generated when the current price crosses above the
breakout price with sufficient volume confirmation.

All functions are pure computation with no external API calls at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_K = 0.5
"""Default K parameter for the volatility breakout formula."""

VOLUME_THRESHOLD = 1.5
"""Minimum volume ratio (vs average) for confirmation."""

MIN_MARKET_CAP = 1_000_000_000_000
"""Minimum market cap (1 trillion KRW) for eligibility when provided."""

STOP_PCT = -2.0
"""Default stop-loss percentage from the open price."""

TARGET_PCT = 5.0
"""Default target profit percentage from the breakout price."""

MIN_RANGE_RATIO = 0.01
"""Minimum prev_range / open ratio to avoid noise signals."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class BreakoutSignal:
    """Represents a volatility breakout entry signal.

    Attributes:
        ticker: Stock ticker code.
        name: Stock name.
        breakout_price: Computed breakout price threshold.
        current_price: Current market price at evaluation time.
        prev_high: Previous day's high price.
        prev_low: Previous day's low price.
        prev_range: Previous day's price range (high - low).
        k_value: K parameter used in computation.
        stop_price: Stop-loss price.
        target_price: Target profit price.
        volume_confirmed: Whether volume confirmation is satisfied.
        message: Pre-formatted Telegram message.
    """

    ticker: str = ""
    name: str = ""
    breakout_price: float = 0.0
    current_price: float = 0.0
    prev_high: float = 0.0
    prev_low: float = 0.0
    prev_range: float = 0.0
    k_value: float = DEFAULT_K
    stop_price: float = 0.0
    target_price: float = 0.0
    volume_confirmed: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_breakout_price(
    open_price: float,
    prev_high: float,
    prev_low: float,
    k: float = DEFAULT_K,
) -> float:
    """Compute the Larry Williams volatility breakout price.

    Formula:
        breakout = open_price + (prev_high - prev_low) * K

    Args:
        open_price: Today's opening price (KRW).
        prev_high: Previous day's high price (KRW).
        prev_low: Previous day's low price (KRW).
        k: Noise filter coefficient (0.0 ~ 1.0, default 0.5).

    Returns:
        Breakout price (KRW).  If prev_range is zero or negative,
        returns open_price (no breakout possible).
    """
    prev_range = prev_high - prev_low

    if prev_range <= 0:
        logger.debug(
            "Breakout: prev_range=%.0f <= 0, returning open=%.0f",
            prev_range, open_price,
        )
        return open_price

    breakout = open_price + prev_range * k

    logger.debug(
        "Breakout price: open=%.0f + (%.0f - %.0f) * %.2f = %.0f",
        open_price, prev_high, prev_low, k, breakout,
    )

    return round(breakout)


def evaluate_breakout(
    ticker: str,
    name: str,
    open_price: float,
    current_price: float,
    prev_high: float,
    prev_low: float,
    volume_ratio: float,
    market_cap: float = 0,
    k: float = DEFAULT_K,
) -> BreakoutSignal | None:
    """Evaluate whether a stock qualifies for a volatility breakout entry.

    Conditions for a signal:
        1. current_price >= breakout_price
        2. volume_ratio >= 1.5 (volume confirmation)
        3. market_cap >= 1 trillion KRW (if market_cap is provided and > 0)
        4. prev_range / open_price >= 1% (filter micro-noise)

    Stop-loss is set at open - 2%.
    Target is set at breakout_price + 5%.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        open_price: Today's opening price.
        current_price: Current market price.
        prev_high: Previous day's high.
        prev_low: Previous day's low.
        volume_ratio: Current volume / average volume ratio.
        market_cap: Market capitalization in KRW (0 to skip check).
        k: K parameter for breakout formula.

    Returns:
        BreakoutSignal if breakout detected, None otherwise.
    """
    # Market cap filter (only if provided)
    if market_cap > 0 and market_cap < MIN_MARKET_CAP:
        logger.debug(
            "Breakout skip %s(%s): market_cap %,.0f < %,.0f",
            name, ticker, market_cap, MIN_MARKET_CAP,
        )
        return None

    if open_price <= 0:
        logger.debug("Breakout skip %s(%s): open_price <= 0", name, ticker)
        return None

    prev_range = prev_high - prev_low

    # Noise filter: require at least 1% range relative to open
    if prev_range <= 0 or (prev_range / open_price) < MIN_RANGE_RATIO:
        logger.debug(
            "Breakout skip %s(%s): prev_range %.0f too small vs open %.0f",
            name, ticker, prev_range, open_price,
        )
        return None

    breakout_price = compute_breakout_price(open_price, prev_high, prev_low, k)

    # Price breakout check
    if current_price < breakout_price:
        logger.debug(
            "Breakout skip %s(%s): current %,.0f < breakout %,.0f",
            name, ticker, current_price, breakout_price,
        )
        return None

    # Volume confirmation check
    volume_confirmed = volume_ratio >= VOLUME_THRESHOLD
    if not volume_confirmed:
        logger.debug(
            "Breakout skip %s(%s): volume_ratio %.2f < %.2f threshold",
            name, ticker, volume_ratio, VOLUME_THRESHOLD,
        )
        return None

    # Compute stop and target
    stop_price = round(open_price * (1 + STOP_PCT / 100))
    target_price = round(breakout_price * (1 + TARGET_PCT / 100))

    signal = BreakoutSignal(
        ticker=ticker,
        name=name,
        breakout_price=breakout_price,
        current_price=current_price,
        prev_high=prev_high,
        prev_low=prev_low,
        prev_range=prev_range,
        k_value=k,
        stop_price=stop_price,
        target_price=target_price,
        volume_confirmed=volume_confirmed,
    )
    signal.message = format_breakout_signal(signal)

    logger.info(
        "Breakout signal %s(%s): current=%,.0f >= breakout=%,.0f, "
        "vol=%.1fx, target=%,.0f, stop=%,.0f",
        name, ticker, current_price, breakout_price,
        volume_ratio, target_price, stop_price,
    )

    return signal


def compute_adaptive_k(
    daily_ranges: list[float],
    daily_opens: list[float],
) -> float:
    """Compute an adaptive K value based on recent volatility.

    When recent ranges are large relative to opening prices, K is
    reduced to filter more noise.  When ranges are small, K is
    increased to capture smaller breakouts.

    Args:
        daily_ranges: List of recent (prev_high - prev_low) values.
        daily_opens: List of corresponding opening prices.

    Returns:
        Adaptive K value between 0.3 and 0.7.
    """
    if not daily_ranges or not daily_opens or len(daily_ranges) != len(daily_opens):
        return DEFAULT_K

    range_ratios: list[float] = []
    for rng, opn in zip(daily_ranges, daily_opens):
        if opn > 0:
            range_ratios.append(rng / opn)

    if not range_ratios:
        return DEFAULT_K

    avg_range_ratio = sum(range_ratios) / len(range_ratios)

    # High volatility -> lower K (more selective)
    # Low volatility -> higher K (more inclusive)
    if avg_range_ratio > 0.05:
        adaptive_k = 0.3
    elif avg_range_ratio > 0.03:
        adaptive_k = 0.4
    elif avg_range_ratio > 0.02:
        adaptive_k = 0.5
    elif avg_range_ratio > 0.01:
        adaptive_k = 0.6
    else:
        adaptive_k = 0.7

    logger.debug(
        "Adaptive K: avg_range_ratio=%.4f -> K=%.2f (%d days)",
        avg_range_ratio, adaptive_k, len(range_ratios),
    )

    return adaptive_k


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_breakout_signal(signal: BreakoutSignal) -> str:
    """Format a BreakoutSignal for Telegram.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        signal: BreakoutSignal to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        [변동성 돌파] 삼성전자
        현재가: 72,500원
        돌파가: 71,800원 (K=0.50)
        전일 고가: 72,000원 / 저가: 70,200원
        전일 레인지: 1,800원
        거래량 확인: 충족 (1.8x)
        목표가: 75,390원 (+5%)
        손절가: 69,384원 (-2%)

        주호님, 변동성 돌파 시그널입니다. 분할 매수 고려하세요.
    """
    vol_label = "충족" if signal.volume_confirmed else "미충족"

    # Compute percentages for display
    if signal.breakout_price > 0:
        target_pct = ((signal.target_price - signal.breakout_price)
                      / signal.breakout_price * 100)
    else:
        target_pct = TARGET_PCT

    if signal.current_price > 0:
        stop_pct = ((signal.stop_price - signal.current_price)
                    / signal.current_price * 100)
    else:
        stop_pct = STOP_PCT

    lines = [
        f"[변동성 돌파] {signal.name}",
        f"현재가: {signal.current_price:,.0f}원",
        f"돌파가: {signal.breakout_price:,.0f}원 (K={signal.k_value:.2f})",
        f"전일 고가: {signal.prev_high:,.0f}원 / 저가: {signal.prev_low:,.0f}원",
        f"전일 레인지: {signal.prev_range:,.0f}원",
        f"거래량 확인: {vol_label}",
        f"목표가: {signal.target_price:,.0f}원 (+{target_pct:.0f}%)",
        f"손절가: {signal.stop_price:,.0f}원 ({stop_pct:.0f}%)",
        "",
        "주호님, 변동성 돌파 시그널입니다. 분할 매수 고려하세요.",
    ]

    return "\n".join(lines)
