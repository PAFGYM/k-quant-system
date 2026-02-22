"""Earnings result tracker (Section 50 - 실적 발표 추적).

Evaluates quarterly earnings vs consensus expectations, determines
surprise/shock/inline verdicts, computes trading-day countdowns,
and formats Telegram messages. All functions are pure computation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EarningsResult:
    """Evaluated earnings result for a single ticker/period."""

    ticker: str
    name: str
    period: str              # "2026년 1분기"
    revenue: float
    revenue_consensus: float
    operating_income: float
    op_income_consensus: float
    op_margin: float
    prev_op_margin: float
    surprise_pct: float      # operating income surprise %
    verdict: str             # "서프라이즈", "쇼크", "인라인"
    details: list[str] = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Verdict thresholds
# ---------------------------------------------------------------------------

_SURPRISE_THRESHOLD = 10.0    # +10% for 서프라이즈
_SHOCK_THRESHOLD = -10.0      # -10% for 쇼크
_HIGH_REVENUE_GROWTH = 20.0   # YoY revenue growth for "매출 고성장"
_MARGIN_IMPROVEMENT = 2.0     # percentage-point improvement for "수익성 개선"


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


def evaluate_earnings(
    ticker: str,
    name: str,
    period: str,
    revenue: float,
    revenue_consensus: float,
    operating_income: float,
    op_income_consensus: float,
    op_margin: float,
    prev_op_margin: float,
    revenue_yoy_pct: float = 0.0,
) -> EarningsResult:
    """Evaluate earnings results against consensus.

    Verdict rules:
        - Operating income surprise >= +10%: "서프라이즈"
        - Operating income surprise <= -10%: "쇼크"
        - Otherwise: "인라인"

    Additional details:
        - revenue YoY >= +20%: "매출 고성장"
        - op_margin improvement >= 2pp: "수익성 개선"
        - op_margin deterioration >= 2pp: "수익성 악화"

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        period: Earnings period label (e.g. "2026년 1분기").
        revenue: Actual revenue (억원).
        revenue_consensus: Consensus revenue (억원).
        operating_income: Actual operating income (억원).
        op_income_consensus: Consensus operating income (억원).
        op_margin: Actual operating margin (%).
        prev_op_margin: Previous period operating margin (%).
        revenue_yoy_pct: Revenue year-over-year growth (%).

    Returns:
        EarningsResult with verdict, surprise percentage, and details.
    """
    details: list[str] = []

    # Operating income surprise
    if op_income_consensus > 0:
        surprise_pct = round(
            (operating_income - op_income_consensus) / op_income_consensus * 100, 2,
        )
    elif op_income_consensus < 0 and operating_income > op_income_consensus:
        # Both negative but improved — treat as positive surprise
        surprise_pct = round(
            abs(operating_income - op_income_consensus) / abs(op_income_consensus) * 100, 2,
        )
    else:
        surprise_pct = 0.0

    # Verdict
    if surprise_pct >= _SURPRISE_THRESHOLD:
        verdict = "서프라이즈"
        details.append(f"영업이익 서프라이즈 {surprise_pct:+.1f}% (컨센서스 대비)")
    elif surprise_pct <= _SHOCK_THRESHOLD:
        verdict = "쇼크"
        details.append(f"영업이익 쇼크 {surprise_pct:+.1f}% (컨센서스 대비)")
    else:
        verdict = "인라인"
        details.append(f"영업이익 인라인 {surprise_pct:+.1f}% (컨센서스 대비)")

    # Revenue surprise
    if revenue_consensus > 0:
        revenue_surprise = round(
            (revenue - revenue_consensus) / revenue_consensus * 100, 2,
        )
        if abs(revenue_surprise) >= 5.0:
            direction = "상회" if revenue_surprise > 0 else "하회"
            details.append(f"매출 컨센서스 {direction} {revenue_surprise:+.1f}%")

    # Revenue YoY growth
    if revenue_yoy_pct >= _HIGH_REVENUE_GROWTH:
        details.append(f"매출 고성장 YoY {revenue_yoy_pct:+.1f}%")
    elif revenue_yoy_pct <= -10.0:
        details.append(f"매출 역성장 YoY {revenue_yoy_pct:+.1f}%")

    # Margin comparison
    margin_change = op_margin - prev_op_margin
    if margin_change >= _MARGIN_IMPROVEMENT:
        details.append(
            f"수익성 개선 (영업이익률 {prev_op_margin:.1f}% -> {op_margin:.1f}%, "
            f"+{margin_change:.1f}%p)"
        )
    elif margin_change <= -_MARGIN_IMPROVEMENT:
        details.append(
            f"수익성 악화 (영업이익률 {prev_op_margin:.1f}% -> {op_margin:.1f}%, "
            f"{margin_change:+.1f}%p)"
        )

    # Build summary message
    message = f"{name} {period} 실적: {verdict}"
    if details:
        message += f" - {details[0]}"

    result = EarningsResult(
        ticker=ticker,
        name=name,
        period=period,
        revenue=revenue,
        revenue_consensus=revenue_consensus,
        operating_income=operating_income,
        op_income_consensus=op_income_consensus,
        op_margin=op_margin,
        prev_op_margin=prev_op_margin,
        surprise_pct=surprise_pct,
        verdict=verdict,
        details=details,
        message=message,
    )

    logger.info(
        "Earnings %s (%s) %s: verdict=%s surprise=%.1f%%",
        ticker, name, period, verdict, surprise_pct,
    )

    return result


# ---------------------------------------------------------------------------
# Earnings countdown
# ---------------------------------------------------------------------------

# Korean public holidays that are not weekends (simplified set)
# A production system would use a more complete calendar.
_KNOWN_HOLIDAYS: set[str] = {
    "2026-01-01",  # 신정
    "2026-01-27",  # 설날
    "2026-01-28",  # 설날
    "2026-01-29",  # 설날
    "2026-03-01",  # 삼일절
    "2026-05-05",  # 어린이날
    "2026-05-24",  # 석가탄신일
    "2026-06-06",  # 현충일
    "2026-08-15",  # 광복절
    "2026-09-24",  # 추석
    "2026-09-25",  # 추석
    "2026-09-26",  # 추석
    "2026-10-03",  # 개천절
    "2026-10-09",  # 한글날
    "2026-12-25",  # 성탄절
}


def _is_trading_day(d: datetime) -> bool:
    """Return True if the date is a trading day (not weekend or holiday)."""
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if d.strftime("%Y-%m-%d") in _KNOWN_HOLIDAYS:
        return False
    return True


def days_until_earnings(earnings_date: str, today: str = "") -> int:
    """Compute the number of trading days until the earnings date.

    Args:
        earnings_date: Earnings date string in "YYYY-MM-DD" format.
        today: Optional today's date string. If empty, uses current date.

    Returns:
        Number of trading days remaining. Returns 0 if the earnings date
        is today or in the past. Returns -1 on parse error.
    """
    try:
        target = datetime.strptime(earnings_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        logger.warning("Invalid earnings_date format: %s", earnings_date)
        return -1

    if today:
        try:
            current = datetime.strptime(today, "%Y-%m-%d")
        except (ValueError, TypeError):
            current = datetime.now()
    else:
        current = datetime.now()

    # Strip time component
    current = current.replace(hour=0, minute=0, second=0, microsecond=0)
    target = target.replace(hour=0, minute=0, second=0, microsecond=0)

    if target <= current:
        return 0

    trading_days = 0
    day = current + timedelta(days=1)
    while day <= target:
        if _is_trading_day(day):
            trading_days += 1
        day += timedelta(days=1)

    return trading_days


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def format_earnings_alert(
    result: EarningsResult,
    holding_profit_pct: float = 0.0,
) -> str:
    """Format earnings result alert for Telegram.

    No ** bold markers. Uses 주호님 greeting.

    Args:
        result: Evaluated EarningsResult.
        holding_profit_pct: Current holding profit % if applicable.

    Returns:
        Multi-line formatted Telegram message.
    """
    lines: list[str] = []

    # Header with verdict emoji
    if result.verdict == "서프라이즈":
        emoji = "\U0001f389"  # party popper
        lines.append(f"{emoji} 주호님, 실적 서프라이즈입니다!")
    elif result.verdict == "쇼크":
        emoji = "\U0001f6a8"  # rotating light
        lines.append(f"{emoji} 주호님, 실적 쇼크 주의!")
    else:
        emoji = "\U0001f4cb"  # clipboard
        lines.append(f"{emoji} 주호님, 실적 발표 결과입니다.")

    lines.append("")

    # Basic info
    lines.append(f"종목: {result.name} ({result.ticker})")
    lines.append(f"기간: {result.period}")
    lines.append("")

    # Revenue
    lines.append(f"매출액: {result.revenue:,.0f}억원 (컨센서스 {result.revenue_consensus:,.0f}억원)")

    # Operating income
    lines.append(
        f"영업이익: {result.operating_income:,.0f}억원 "
        f"(컨센서스 {result.op_income_consensus:,.0f}억원, "
        f"{result.surprise_pct:+.1f}%)"
    )

    # Margin
    margin_change = result.op_margin - result.prev_op_margin
    lines.append(
        f"영업이익률: {result.op_margin:.1f}% "
        f"(전기 {result.prev_op_margin:.1f}%, {margin_change:+.1f}%p)"
    )
    lines.append("")

    # Details
    if result.details:
        lines.append("주요 포인트:")
        for detail in result.details:
            lines.append(f"  - {detail}")
        lines.append("")

    # Holding context
    if holding_profit_pct != 0.0:
        lines.append(f"현재 보유 수익률: {holding_profit_pct:+.1f}%")
        lines.append("")

    # Action guidance
    if result.verdict == "서프라이즈":
        lines.append("실적 모멘텀이 긍정적입니다. 홀딩 유지를 추천드립니다.")
    elif result.verdict == "쇼크":
        if holding_profit_pct > 10:
            lines.append("수익 구간이지만 리스크 관리를 점검하세요.")
        else:
            lines.append("손절 라인과 리스크 관리를 점검하세요.")
    else:
        lines.append("컨센서스 부합 수준입니다. 기존 전략을 유지하세요.")

    return "\n".join(lines)


def format_earnings_countdown(
    ticker: str,
    name: str,
    days_left: int,
) -> str:
    """Format a D-N countdown message for upcoming earnings.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        days_left: Trading days until earnings.

    Returns:
        Formatted countdown string for Telegram. No ** bold.
    """
    if days_left <= 0:
        return f"\U0001f4c5 {name} ({ticker}) 실적 발표일입니다!"

    if days_left <= 3:
        emoji = "\u23f0"  # alarm clock
        urgency = "임박"
    elif days_left <= 7:
        emoji = "\U0001f4c5"  # calendar
        urgency = "주의"
    else:
        emoji = "\U0001f5d3\uFE0F"  # spiral calendar
        urgency = "예정"

    lines: list[str] = [
        f"{emoji} 실적 발표 D-{days_left} ({urgency})",
        f"종목: {name} ({ticker})",
    ]

    if days_left <= 3:
        lines.append("")
        lines.append("주호님, 실적 발표 전 포지션을 점검하세요.")
    elif days_left <= 7:
        lines.append("")
        lines.append("실적 컨센서스와 포지션을 미리 확인하세요.")

    return "\n".join(lines)
