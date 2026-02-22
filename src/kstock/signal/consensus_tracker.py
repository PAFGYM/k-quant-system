"""Consensus target price tracker (Section 49 - 컨센서스 추적).

Aggregates broker target prices and opinions to compute consensus data,
score bonuses, and formatted Telegram messages. All functions are pure
computation with no external API calls.
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
class ConsensusData:
    """Aggregated consensus data for a single ticker."""

    ticker: str
    name: str
    avg_target_price: float
    current_price: float
    upside_pct: float
    opinions: dict = field(default_factory=dict)   # {"매수": 4, "중립": 1, "매도": 0}
    total_brokers: int = 0
    target_trend: str = "유지"                     # "상향", "하향", "유지"
    target_trend_pct: float = 0.0
    score_bonus: int = 0


# ---------------------------------------------------------------------------
# Opinion constants
# ---------------------------------------------------------------------------

_OPINION_RANK: dict[str, int] = {
    "매수": 3,
    "중립": 2,
    "매도": 1,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_avg_target(reports: list[dict]) -> float:
    """Compute simple average target price from reports.

    Only considers reports with a positive target_price.
    """
    prices = [r.get("target_price", 0.0) for r in reports if r.get("target_price", 0.0) > 0]
    if not prices:
        return 0.0
    return round(sum(prices) / len(prices), 0)


def _count_opinions(reports: list[dict]) -> dict[str, int]:
    """Count opinion types across reports."""
    counts: dict[str, int] = {"매수": 0, "중립": 0, "매도": 0}
    for r in reports:
        opinion = r.get("opinion", "").strip()
        if opinion in counts:
            counts[opinion] += 1
    return counts


def _compute_target_trend(reports: list[dict], recent_days: int = 30) -> tuple[str, float]:
    """Determine target price trend over the recent period.

    Compares average target price from the most recent ``recent_days``
    against the average of older reports.

    Args:
        reports: List of report dicts with 'date' and 'target_price' keys.
        recent_days: Number of days to consider as 'recent'.

    Returns:
        Tuple of (trend_label, trend_pct). trend_label is "상향", "하향", or "유지".
    """
    if len(reports) < 2:
        return "유지", 0.0

    # Parse dates; handle reports without valid dates gracefully
    now = datetime.now()
    cutoff = now - timedelta(days=recent_days)

    recent_prices: list[float] = []
    older_prices: list[float] = []

    for r in reports:
        target = r.get("target_price", 0.0)
        if target <= 0:
            continue
        date_str = r.get("date", "")
        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            # Cannot parse date; treat as recent
            recent_prices.append(target)
            continue

        if report_date >= cutoff:
            recent_prices.append(target)
        else:
            older_prices.append(target)

    if not recent_prices or not older_prices:
        return "유지", 0.0

    avg_recent = sum(recent_prices) / len(recent_prices)
    avg_older = sum(older_prices) / len(older_prices)

    if avg_older <= 0:
        return "유지", 0.0

    change_pct = round((avg_recent - avg_older) / avg_older * 100, 2)

    if change_pct >= 3.0:
        return "상향", change_pct
    elif change_pct <= -3.0:
        return "하향", change_pct
    else:
        return "유지", change_pct


def _detect_opinion_downgrade_trend(reports: list[dict], recent_days: int = 30) -> bool:
    """Check if there is a recent trend of opinion downgrades.

    Returns True if 2 or more reports within the recent period contain
    a downgrade (prev_opinion rank > opinion rank).
    """
    now = datetime.now()
    cutoff = now - timedelta(days=recent_days)
    downgrade_count = 0

    for r in reports:
        prev_op = r.get("prev_opinion", "").strip()
        curr_op = r.get("opinion", "").strip()
        prev_rank = _OPINION_RANK.get(prev_op, 0)
        curr_rank = _OPINION_RANK.get(curr_op, 0)

        if prev_rank <= 0 or curr_rank >= prev_rank:
            continue

        date_str = r.get("date", "")
        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            downgrade_count += 1
            continue

        if report_date >= cutoff:
            downgrade_count += 1

    return downgrade_count >= 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_consensus(reports: list[dict], current_price: float) -> ConsensusData:
    """Compute consensus data from a list of broker reports for one ticker.

    Scoring rules:
        - upside > 30%: +15
        - upside > 15%: +10
        - target_trend "상향" recent 1 month: +5
        - target_trend "하향": -10
        - 매수 ratio 80%+: +5
        - opinion downgrade trend: -10

    Score bonus is capped at -20 to +20.

    Args:
        reports: List of report dicts for a single ticker. Expected keys:
            target_price, opinion, prev_opinion, date.
        current_price: Current market price.

    Returns:
        ConsensusData with computed metrics and score bonus.
    """
    if not reports:
        return ConsensusData(
            ticker="",
            name="",
            avg_target_price=0.0,
            current_price=current_price,
            upside_pct=0.0,
            score_bonus=0,
        )

    # Use ticker/name from the first report
    ticker = reports[0].get("ticker", "")
    name = reports[0].get("name", ticker)

    avg_target = _compute_avg_target(reports)
    opinions = _count_opinions(reports)
    total_brokers = sum(opinions.values())
    target_trend, target_trend_pct = _compute_target_trend(reports)

    # Upside
    if current_price > 0 and avg_target > 0:
        upside_pct = round((avg_target - current_price) / current_price * 100, 2)
    else:
        upside_pct = 0.0

    # Score bonus calculation
    bonus = 0

    # Upside bonus
    if upside_pct > 30:
        bonus += 15
    elif upside_pct > 15:
        bonus += 10

    # Target trend bonus
    if target_trend == "상향":
        bonus += 5
    elif target_trend == "하향":
        bonus -= 10

    # 매수 ratio
    buy_count = opinions.get("매수", 0)
    if total_brokers > 0 and (buy_count / total_brokers) >= 0.8:
        bonus += 5

    # Opinion downgrade trend
    if _detect_opinion_downgrade_trend(reports):
        bonus -= 10

    # Cap at +-20
    bonus = max(-20, min(20, bonus))

    consensus = ConsensusData(
        ticker=ticker,
        name=name,
        avg_target_price=avg_target,
        current_price=current_price,
        upside_pct=upside_pct,
        opinions=opinions,
        total_brokers=total_brokers,
        target_trend=target_trend,
        target_trend_pct=target_trend_pct,
        score_bonus=bonus,
    )

    logger.info(
        "Consensus %s (%s): avg_target=%.0f upside=%.1f%% trend=%s bonus=%d",
        ticker, name, avg_target, upside_pct, target_trend, bonus,
    )

    return consensus


def compute_consensus_score(consensus: ConsensusData) -> int:
    """Return the score bonus from consensus data, capped at +-20.

    This is a convenience accessor that re-validates the cap.

    Args:
        consensus: Precomputed ConsensusData.

    Returns:
        Integer score bonus in range [-20, 20].
    """
    return max(-20, min(20, consensus.score_bonus))


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def format_consensus(consensus: ConsensusData) -> str:
    """Format consensus data for Telegram /consensus command.

    No ** bold markers. Korean text throughout.

    Args:
        consensus: Computed ConsensusData.

    Returns:
        Multi-line formatted string.
    """
    lines: list[str] = []

    lines.append(f"\U0001f4ca {consensus.name} ({consensus.ticker}) 컨센서스")
    lines.append("")

    # Price comparison
    lines.append(f"현재가: {consensus.current_price:,.0f}원")
    lines.append(f"평균 목표가: {consensus.avg_target_price:,.0f}원")

    # Upside with emoji
    if consensus.upside_pct > 0:
        upside_emoji = "\U0001f7e2"  # green
    elif consensus.upside_pct < -10:
        upside_emoji = "\U0001f534"  # red
    else:
        upside_emoji = "\u26aa"      # white
    lines.append(f"상승여력: {upside_emoji} {consensus.upside_pct:+.1f}%")
    lines.append("")

    # Opinions breakdown
    lines.append(f"커버리지: {consensus.total_brokers}개사")
    opinions = consensus.opinions
    opinion_parts: list[str] = []
    for op_type in ("매수", "중립", "매도"):
        count = opinions.get(op_type, 0)
        if count > 0:
            opinion_parts.append(f"{op_type} {count}")
    if opinion_parts:
        lines.append(f"투자의견: {' / '.join(opinion_parts)}")

    # Buy ratio
    buy_count = opinions.get("매수", 0)
    if consensus.total_brokers > 0:
        buy_ratio = buy_count / consensus.total_brokers * 100
        lines.append(f"매수 비율: {buy_ratio:.0f}%")

    lines.append("")

    # Target trend
    trend_emoji = {
        "상향": "\u2B06\uFE0F",
        "하향": "\u2B07\uFE0F",
        "유지": "\u27A1\uFE0F",
    }.get(consensus.target_trend, "\u27A1\uFE0F")
    lines.append(
        f"목표가 추세: {trend_emoji} {consensus.target_trend} "
        f"({consensus.target_trend_pct:+.1f}%)"
    )

    # Score bonus
    if consensus.score_bonus > 0:
        lines.append(f"스코어 보너스: +{consensus.score_bonus}점")
    elif consensus.score_bonus < 0:
        lines.append(f"스코어 조정: {consensus.score_bonus}점")

    return "\n".join(lines)


def format_consensus_from_dict(data: dict) -> str:
    """Format consensus from a DB dict (consensus table row)."""
    cd = ConsensusData(
        ticker=data.get("ticker", ""),
        name=data.get("name", ""),
        avg_target_price=data.get("avg_target_price", 0),
        current_price=data.get("current_price", 0),
        upside_pct=data.get("upside_pct", 0),
        opinions={
            "매수": data.get("buy_count", 0),
            "중립": data.get("hold_count", 0),
            "매도": data.get("sell_count", 0),
        },
        total_brokers=(
            data.get("buy_count", 0)
            + data.get("hold_count", 0)
            + data.get("sell_count", 0)
        ),
        target_trend=data.get("target_trend", "유지"),
        target_trend_pct=data.get("target_trend_pct", 0),
        score_bonus=data.get("score_bonus", 0),
    )
    return format_consensus(cd)
