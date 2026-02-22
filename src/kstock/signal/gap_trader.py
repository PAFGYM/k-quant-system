"""Gap trading signal detector (Section 61 - 갭 트레이딩 시그널).

Detects and classifies price gaps at market open, producing actionable
signals for trend-following or reversal strategies:

    - Gap up (+3%+) with volume surge -> trend-follow buy
    - Gap down (-5%+) with oversold conditions -> bounce buy candidate
    - Gap-up reversal (falls below open) -> gap fill / sell signal

All functions are pure computation with no external API calls at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAP_UP_THRESHOLD = 0.03
"""Minimum gap up percentage (+3%) to detect a significant gap up."""

GAP_DOWN_THRESHOLD = -0.05
"""Minimum gap down percentage (-5%) to detect a significant gap down."""

VOLUME_SURGE_RATIO = 1.5
"""Volume ratio threshold for confirming a gap-up trend follow."""

GAP_FILL_CHECK_RATIO = 1.0
"""If current price falls below open after a gap up, gap fill is triggered."""

SCORE_ADJ_MAP: dict[str, int] = {
    "갭업": 10,
    "갭다운": -5,
    "갭채우기": -8,
}
"""Score adjustments by gap type."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class GapSignal:
    """Represents a detected gap trading signal.

    Attributes:
        ticker: Stock ticker code.
        name: Stock name.
        gap_type: One of "갭업", "갭다운", "갭채우기".
        gap_pct: Gap percentage (positive for up, negative for down).
        prev_close: Previous day's closing price.
        open_price: Today's opening price.
        current_price: Current market price at evaluation time.
        volume_ratio: Current volume / average volume ratio.
        action: Recommended action in Korean.
        score_adj: Score adjustment to apply.
        message: Pre-formatted Telegram message.
    """

    ticker: str = ""
    name: str = ""
    gap_type: str = ""
    gap_pct: float = 0.0
    prev_close: float = 0.0
    open_price: float = 0.0
    current_price: float = 0.0
    volume_ratio: float = 0.0
    action: str = ""
    score_adj: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_gap(
    ticker: str,
    name: str,
    prev_close: float,
    open_price: float,
    current_price: float,
    volume_ratio: float,
) -> GapSignal | None:
    """Detect gap signals and produce a GapSignal.

    Signal logic:

    1. Gap up (+3% or more):
       - If volume_ratio >= 1.5: trend-follow buy ("추세 추종 매수")
       - If current_price < open_price: gap fill ("갭채우기", sell signal)
       - Otherwise: gap up detected but no strong confirmation yet.

    2. Gap down (-5% or more):
       - Treated as a bounce buy candidate if gap is significant.
       - More conservative since large gap downs indicate risk.

    3. Gap fill:
       - A gap up that reverses and price falls below the open price.
       - Sell/avoid signal.

    Returns None if no significant gap is detected.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        prev_close: Previous day's closing price.
        open_price: Today's opening price.
        current_price: Current market price.
        volume_ratio: Current volume / average volume ratio.

    Returns:
        GapSignal if a significant gap is detected, None otherwise.
    """
    if prev_close <= 0:
        logger.debug("Gap skip %s(%s): prev_close <= 0", name, ticker)
        return None

    gap_pct = (open_price - prev_close) / prev_close

    # Check for gap-up reversal first (gap fill)
    if gap_pct >= GAP_UP_THRESHOLD and current_price < open_price:
        signal = _build_gap_fill_signal(
            ticker, name, gap_pct, prev_close, open_price,
            current_price, volume_ratio,
        )
        return signal

    # Gap up with volume confirmation
    if gap_pct >= GAP_UP_THRESHOLD:
        if volume_ratio >= VOLUME_SURGE_RATIO:
            signal = _build_gap_up_signal(
                ticker, name, gap_pct, prev_close, open_price,
                current_price, volume_ratio,
            )
            return signal
        else:
            logger.debug(
                "Gap up %s(%s): +%.1f%% but volume_ratio %.1f < %.1f, skip",
                name, ticker, gap_pct * 100, volume_ratio, VOLUME_SURGE_RATIO,
            )
            return None

    # Gap down
    if gap_pct <= GAP_DOWN_THRESHOLD:
        signal = _build_gap_down_signal(
            ticker, name, gap_pct, prev_close, open_price,
            current_price, volume_ratio,
        )
        return signal

    # No significant gap
    logger.debug(
        "Gap skip %s(%s): gap=%.2f%% (threshold: +%.0f%% / %.0f%%)",
        name, ticker, gap_pct * 100,
        GAP_UP_THRESHOLD * 100, GAP_DOWN_THRESHOLD * 100,
    )
    return None


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _build_gap_up_signal(
    ticker: str,
    name: str,
    gap_pct: float,
    prev_close: float,
    open_price: float,
    current_price: float,
    volume_ratio: float,
) -> GapSignal:
    """Build a gap-up trend-follow buy signal.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        gap_pct: Gap percentage (positive).
        prev_close: Previous close price.
        open_price: Today's open price.
        current_price: Current price.
        volume_ratio: Volume ratio.

    Returns:
        Constructed GapSignal.
    """
    score_adj = SCORE_ADJ_MAP["갭업"]

    # Stronger gap gets a bonus
    if gap_pct >= 0.05:
        score_adj += 3

    signal = GapSignal(
        ticker=ticker,
        name=name,
        gap_type="갭업",
        gap_pct=round(gap_pct * 100, 2),
        prev_close=prev_close,
        open_price=open_price,
        current_price=current_price,
        volume_ratio=round(volume_ratio, 1),
        action="추세 추종 매수",
        score_adj=score_adj,
    )
    signal.message = format_gap_alert(signal)

    logger.info(
        "Gap up %s(%s): +%.1f%%, vol=%.1fx -> 추세 추종 매수",
        name, ticker, gap_pct * 100, volume_ratio,
    )

    return signal


def _build_gap_down_signal(
    ticker: str,
    name: str,
    gap_pct: float,
    prev_close: float,
    open_price: float,
    current_price: float,
    volume_ratio: float,
) -> GapSignal:
    """Build a gap-down bounce buy candidate signal.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        gap_pct: Gap percentage (negative).
        prev_close: Previous close price.
        open_price: Today's open price.
        current_price: Current price.
        volume_ratio: Volume ratio.

    Returns:
        Constructed GapSignal.
    """
    score_adj = SCORE_ADJ_MAP["갭다운"]

    # Very large gap down is more risky
    if gap_pct <= -0.10:
        score_adj -= 5

    # If current price is recovering above the open, slightly better outlook
    if current_price > open_price:
        action = "반등 매수 후보"
        score_adj += 3
    else:
        action = "반등 매수 후보"

    signal = GapSignal(
        ticker=ticker,
        name=name,
        gap_type="갭다운",
        gap_pct=round(gap_pct * 100, 2),
        prev_close=prev_close,
        open_price=open_price,
        current_price=current_price,
        volume_ratio=round(volume_ratio, 1),
        action=action,
        score_adj=score_adj,
    )
    signal.message = format_gap_alert(signal)

    logger.info(
        "Gap down %s(%s): %.1f%% -> %s",
        name, ticker, gap_pct * 100, action,
    )

    return signal


def _build_gap_fill_signal(
    ticker: str,
    name: str,
    gap_pct: float,
    prev_close: float,
    open_price: float,
    current_price: float,
    volume_ratio: float,
) -> GapSignal:
    """Build a gap-fill sell signal.

    A gap fill occurs when a gap-up reverses and the current price
    falls below the opening price, suggesting the initial gap
    lacked follow-through.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        gap_pct: Original gap percentage (positive).
        prev_close: Previous close price.
        open_price: Today's open price.
        current_price: Current price (below open).
        volume_ratio: Volume ratio.

    Returns:
        Constructed GapSignal.
    """
    score_adj = SCORE_ADJ_MAP["갭채우기"]

    signal = GapSignal(
        ticker=ticker,
        name=name,
        gap_type="갭채우기",
        gap_pct=round(gap_pct * 100, 2),
        prev_close=prev_close,
        open_price=open_price,
        current_price=current_price,
        volume_ratio=round(volume_ratio, 1),
        action="매도 시그널",
        score_adj=score_adj,
    )
    signal.message = format_gap_alert(signal)

    logger.info(
        "Gap fill %s(%s): gap was +%.1f%% but reversed below open, sell signal",
        name, ticker, gap_pct * 100,
    )

    return signal


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_GAP_TYPE_DESCRIPTION: dict[str, str] = {
    "갭업": "시가 갭업 발생",
    "갭다운": "시가 갭다운 발생",
    "갭채우기": "갭업 후 되돌림 발생",
}


def format_gap_alert(signal: GapSignal) -> str:
    """Format a GapSignal as a Telegram alert message.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        signal: GapSignal to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        [갭 분석] 삼성전자 - 갭업
        전일 종가: 70,000원
        시가: 72,100원 (+3.00%)
        현재가: 73,500원
        거래량: 1.8x (평균 대비)
        판단: 추세 추종 매수 (스코어 +10)

        주호님, 갭업 시그널입니다. 추세 추종 진입을 고려하세요.
    """
    desc = _GAP_TYPE_DESCRIPTION.get(signal.gap_type, signal.gap_type)

    lines = [
        f"[갭 분석] {signal.name} - {signal.gap_type}",
        f"전일 종가: {signal.prev_close:,.0f}원",
        f"시가: {signal.open_price:,.0f}원 ({signal.gap_pct:+.2f}%)",
        f"현재가: {signal.current_price:,.0f}원",
        f"거래량: {signal.volume_ratio:.1f}x (평균 대비)",
        f"판단: {signal.action} (스코어 {signal.score_adj:+d})",
    ]

    lines.append("")

    # User-facing closing with 주호님
    if signal.gap_type == "갭업":
        lines.append(
            "주호님, 갭업 시그널입니다. 추세 추종 진입을 고려하세요."
        )
    elif signal.gap_type == "갭다운":
        if signal.action == "반등 매수 후보":
            lines.append(
                "주호님, 갭다운이 발생했습니다. "
                "반등 여부를 지켜본 후 진입을 검토하세요."
            )
        else:
            lines.append(
                "주호님, 갭다운 발생입니다. 추가 하락에 주의하세요."
            )
    elif signal.gap_type == "갭채우기":
        lines.append(
            "주호님, 갭채우기 패턴입니다. "
            "보유 중이라면 매도를 검토하세요."
        )

    return "\n".join(lines)
