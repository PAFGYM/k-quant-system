"""Short selling analysis module (공매도 분석).

Provides data structures, overheated stock detection, and score adjustments
based on short selling data from KRX/exchange data.
All functions are pure computation with no external API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ShortSellingData:
    """Single day's short selling data for a ticker."""

    ticker: str
    name: str
    date: str                    # "YYYY-MM-DD"
    short_volume: int = 0        # 공매도 거래량 (주)
    total_volume: int = 0        # 총 거래량 (주)
    short_ratio: float = 0.0     # 공매도 비중 (%) = short_volume / total_volume * 100
    short_balance: int = 0       # 공매도 잔고 (주)
    short_balance_ratio: float = 0.0  # 공매도 잔고 비율 (%) = balance / 상장주식수 * 100


@dataclass
class ShortSellingSignal:
    """Aggregated short selling signal with detected patterns."""

    ticker: str
    name: str
    patterns: list[str] = field(default_factory=list)
    score_adj: int = 0
    message: str = ""
    is_overheated: bool = False
    overheated_days: int = 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OVERHEATED_RATIO = 20.0          # 공매도 비중 >= 20% → 과열
_OVERHEATED_BALANCE_RATIO = 10.0  # 공매도 잔고 비율 >= 10% → 과열
_HIGH_SHORT_RATIO = 10.0          # 공매도 비중 >= 10% → 주의
_SHORT_SURGE_THRESHOLD = 50.0     # 공매도 비중 일간 증가 >= 50% → 급증
_SCORE_CAP = 15                   # ±15 cap for short selling score


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_avg_short_ratio(history: list[dict], days: int = 5) -> float:
    """Compute average short ratio over recent N days."""
    if not history:
        return 0.0
    recent = history[-days:]
    ratios = [e.get("short_ratio", 0.0) for e in recent]
    return sum(ratios) / len(ratios) if ratios else 0.0


def _count_overheated_days(history: list[dict], threshold: float = _OVERHEATED_RATIO) -> int:
    """Count how many recent consecutive days short ratio exceeds threshold."""
    count = 0
    for entry in reversed(history):
        if entry.get("short_ratio", 0.0) >= threshold:
            count += 1
        else:
            break
    return count


def _compute_balance_change_pct(history: list[dict], days: int = 5) -> float:
    """Compute short balance change percentage over N days."""
    if len(history) < 2:
        return 0.0
    start_idx = max(0, len(history) - days)
    start_balance = history[start_idx].get("short_balance", 0)
    end_balance = history[-1].get("short_balance", 0)
    if start_balance <= 0:
        return 0.0
    return round((end_balance - start_balance) / start_balance * 100, 2)


def _compute_volume_surge(history: list[dict]) -> float:
    """Compute short volume surge: latest vs 5-day average.

    Returns ratio (e.g., 2.0 = 200% of average).
    """
    if len(history) < 2:
        return 1.0
    avg_window = history[-6:-1] if len(history) > 5 else history[:-1]
    avg_vol = sum(e.get("short_volume", 0) for e in avg_window) / max(1, len(avg_window))
    latest_vol = history[-1].get("short_volume", 0)
    if avg_vol <= 0:
        return 1.0
    return round(latest_vol / avg_vol, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_overheated(history: list[dict], ticker: str = "", name: str = "") -> bool:
    """Detect if a stock is in overheated short selling territory.

    Args:
        history: List of daily short selling dicts sorted by date ascending.
        ticker: Stock ticker code.
        name: Stock name.

    Returns:
        True if the stock is overheated.
    """
    if not history:
        return False

    latest = history[-1]
    short_ratio = latest.get("short_ratio", 0.0)
    balance_ratio = latest.get("short_balance_ratio", 0.0)

    return short_ratio >= _OVERHEATED_RATIO or balance_ratio >= _OVERHEATED_BALANCE_RATIO


def analyze_short_selling(
    history: list[dict],
    ticker: str = "",
    name: str = "",
) -> ShortSellingSignal:
    """Analyze short selling data and produce a signal.

    Patterns detected:
        1. 공매도 과열: short_ratio >= 20% or balance_ratio >= 10%
        2. 공매도 비중 주의: short_ratio >= 10%
        3. 공매도 잔고 급증: balance change >= 50% over 5 days
        4. 공매도 잔고 급감: balance change <= -50% over 5 days
        5. 공매도 거래량 폭증: short volume > 2x 5-day average

    Score adjustment capped at ±15.

    Args:
        history: List of daily short selling dicts sorted by date ascending.
        ticker: Stock ticker code.
        name: Stock name.

    Returns:
        ShortSellingSignal with patterns, score adjustment, and message.
    """
    patterns: list[str] = []
    score_adj = 0

    if not history:
        return ShortSellingSignal(
            ticker=ticker,
            name=name,
            patterns=[],
            score_adj=0,
            message="공매도 데이터 없음",
        )

    latest = history[-1]
    short_ratio = latest.get("short_ratio", 0.0)
    balance_ratio = latest.get("short_balance_ratio", 0.0)

    # Pattern 1: Overheated
    is_overheated = short_ratio >= _OVERHEATED_RATIO or balance_ratio >= _OVERHEATED_BALANCE_RATIO
    overheated_days = _count_overheated_days(history)

    if is_overheated:
        patterns.append(f"공매도 과열 (비중 {short_ratio:.1f}%, {overheated_days}일 연속)")
        score_adj -= 10

    # Pattern 2: High short ratio warning
    elif short_ratio >= _HIGH_SHORT_RATIO:
        patterns.append(f"공매도 비중 주의 ({short_ratio:.1f}%)")
        score_adj -= 5

    # Pattern 3 & 4: Balance change
    balance_change = _compute_balance_change_pct(history)
    if balance_change >= _SHORT_SURGE_THRESHOLD:
        patterns.append(f"공매도 잔고 급증 ({balance_change:+.0f}%, 5일간)")
        score_adj -= 5
    elif balance_change <= -_SHORT_SURGE_THRESHOLD:
        patterns.append(f"공매도 잔고 급감 ({balance_change:+.0f}%, 5일간)")
        score_adj += 5

    # Pattern 5: Volume surge
    volume_surge = _compute_volume_surge(history)
    if volume_surge >= 2.0:
        patterns.append(f"공매도 거래량 폭증 (평균 대비 {volume_surge:.1f}배)")
        score_adj -= 3

    # Cap score
    score_adj = max(-_SCORE_CAP, min(_SCORE_CAP, score_adj))

    # Build message
    if patterns:
        message = f"{name} 공매도 신호: " + ", ".join(patterns)
    else:
        message = f"{name} 공매도 특이사항 없음"

    signal = ShortSellingSignal(
        ticker=ticker,
        name=name,
        patterns=patterns,
        score_adj=score_adj,
        message=message,
        is_overheated=is_overheated,
        overheated_days=overheated_days,
    )

    logger.info(
        "Short selling %s (%s): patterns=%d score_adj=%d overheated=%s",
        ticker, name, len(patterns), score_adj, is_overheated,
    )

    return signal


def compute_short_score(signal: ShortSellingSignal) -> int:
    """Return score adjustment from the signal, capped at ±15."""
    return max(-_SCORE_CAP, min(_SCORE_CAP, signal.score_adj))


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_short_alert(
    signal: ShortSellingSignal,
    history: list[dict] | None = None,
) -> str:
    """Format short selling alert for Telegram.

    No ** bold markers. Korean text throughout.
    """
    lines: list[str] = []

    # Header
    if signal.is_overheated:
        header_emoji = "\U0001f6a8"  # siren
    elif signal.score_adj <= -5:
        header_emoji = "\U0001f534"  # red
    elif signal.score_adj >= 5:
        header_emoji = "\U0001f7e2"  # green
    else:
        header_emoji = "\U0001f4ca"  # chart

    lines.append(f"{header_emoji} {signal.name} ({signal.ticker}) 공매도 분석")
    lines.append("")

    # Overheated warning
    if signal.is_overheated:
        lines.append(f"\U0001f6a8 공매도 과열 종목 ({signal.overheated_days}일 연속)")
        lines.append("")

    # Patterns
    if signal.patterns:
        lines.append("감지된 패턴:")
        for pattern in signal.patterns:
            if any(neg in pattern for neg in ("과열", "급증", "폭증", "주의")):
                p_emoji = "\U0001f534"
            elif any(pos in pattern for pos in ("급감",)):
                p_emoji = "\U0001f7e2"
            else:
                p_emoji = "\u26aa"
            lines.append(f"  {p_emoji} {pattern}")
        lines.append("")
    else:
        lines.append("공매도 특이사항 없음")
        lines.append("")

    # Recent daily breakdown
    if history and len(history) > 0:
        recent = history[-5:]
        lines.append("최근 공매도 현황:")
        for entry in recent:
            date = entry.get("date", "")
            short_ratio = entry.get("short_ratio", 0.0)
            short_vol = entry.get("short_volume", 0)
            total_vol = entry.get("total_volume", 0)
            ratio_emoji = "\U0001f534" if short_ratio >= _HIGH_SHORT_RATIO else "\u26aa"
            lines.append(
                f"  {date} | {ratio_emoji} 비중 {short_ratio:.1f}% "
                f"({short_vol:,.0f}/{total_vol:,.0f}주)"
            )
        lines.append("")

    # Balance info
    if history and len(history) > 0:
        latest = history[-1]
        balance = latest.get("short_balance", 0)
        balance_ratio = latest.get("short_balance_ratio", 0.0)
        if balance > 0:
            b_emoji = "\U0001f534" if balance_ratio >= _OVERHEATED_BALANCE_RATIO else "\u26aa"
            lines.append(
                f"공매도 잔고: {b_emoji} {balance:,.0f}주 (비율 {balance_ratio:.1f}%)"
            )
            lines.append("")

    # Score
    if signal.score_adj > 0:
        lines.append(f"공매도 스코어: +{signal.score_adj}점")
    elif signal.score_adj < 0:
        lines.append(f"공매도 스코어: {signal.score_adj}점")
    else:
        lines.append("공매도 스코어: 0점 (중립)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inverse ETF mapping
# ---------------------------------------------------------------------------

INVERSE_ETF_SECTORS: dict[str, list[dict]] = {
    "코스피": [
        {"ticker": "114800", "name": "KODEX 인버스"},
        {"ticker": "252670", "name": "KODEX 200선물인버스2X"},
    ],
    "코스닥": [
        {"ticker": "251340", "name": "KODEX 코스닥150선물인버스"},
    ],
    "2차전지": [
        {"ticker": "466920", "name": "KODEX 2차전지산업인버스"},
    ],
    "반도체": [
        {"ticker": "400590", "name": "TIGER 차이나반도체FACTSET인버스"},
    ],
}


def get_inverse_etf_for_sector(sector: str) -> list[dict]:
    """Return inverse ETFs for a given sector."""
    return INVERSE_ETF_SECTORS.get(sector, [])


def get_all_inverse_etfs() -> list[dict]:
    """Return all tracked inverse ETFs with their sectors."""
    result = []
    for sector, etfs in INVERSE_ETF_SECTORS.items():
        for etf in etfs:
            result.append({**etf, "sector": sector})
    return result
