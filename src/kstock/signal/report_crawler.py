"""Broker report data management (Section 47 - 증권사 리포트 관리).

Provides data structures and pure-computation utilities for processing
broker research reports. NO actual HTTP crawling — all functions work on
pre-fetched data for deduplication, change detection, alert classification,
and Telegram message formatting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BrokerReport:
    """Single broker research report entry."""

    source: str           # "naver", "hankyung", "dart"
    title: str
    broker: str           # 증권사명
    ticker: str
    target_price: float
    prev_target_price: float
    opinion: str          # "매수", "중립", "매도"
    prev_opinion: str
    date: str             # "2026-02-22" format
    pdf_url: str
    summary: str


# ---------------------------------------------------------------------------
# Opinion change constants
# ---------------------------------------------------------------------------

_OPINION_RANK: dict[str, int] = {
    "매수": 3,
    "중립": 2,
    "매도": 1,
}


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def is_duplicate(report: BrokerReport, existing: list[dict]) -> bool:
    """Check if report already exists in the collection.

    Uniqueness key: (title, broker, date). If all three match an existing
    entry the report is considered a duplicate.

    Args:
        report: Incoming report to check.
        existing: List of previously stored report dicts.

    Returns:
        True if a duplicate is found.
    """
    for entry in existing:
        if (
            entry.get("title") == report.title
            and entry.get("broker") == report.broker
            and entry.get("date") == report.date
        ):
            logger.debug(
                "Duplicate report: %s / %s / %s",
                report.title,
                report.broker,
                report.date,
            )
            return True
    return False


# ---------------------------------------------------------------------------
# Opinion / target price helpers
# ---------------------------------------------------------------------------


def parse_opinion_change(prev: str, current: str) -> str:
    """Detect and format opinion change between two ratings.

    Args:
        prev: Previous opinion string (e.g. "매수").
        current: Current opinion string.

    Returns:
        Change description such as "매수->중립" or "" if unchanged.
    """
    prev = prev.strip()
    current = current.strip()

    if not prev or not current:
        return ""

    if prev == current:
        return ""

    return f"{prev}->{current}"


def compute_target_change_pct(prev: float, current: float) -> float:
    """Compute target price change percentage.

    Args:
        prev: Previous target price.
        current: Current target price.

    Returns:
        Change percentage. Returns 0.0 if prev is zero or negative.
    """
    if prev <= 0:
        return 0.0
    return round((current - prev) / prev * 100, 2)


def _is_opinion_downgrade(prev: str, current: str) -> bool:
    """Return True if opinion was downgraded."""
    prev_rank = _OPINION_RANK.get(prev.strip(), 0)
    curr_rank = _OPINION_RANK.get(current.strip(), 0)
    return curr_rank < prev_rank and prev_rank > 0


def _is_opinion_upgrade(prev: str, current: str) -> bool:
    """Return True if opinion was upgraded."""
    prev_rank = _OPINION_RANK.get(prev.strip(), 0)
    curr_rank = _OPINION_RANK.get(current.strip(), 0)
    return curr_rank > prev_rank and prev_rank > 0


# ---------------------------------------------------------------------------
# Alert classification
# ---------------------------------------------------------------------------


def classify_alert_level(
    report: BrokerReport,
    portfolio_tickers: list[str],
    tenbagger_tickers: list[str],
    watch_sectors: list[str],
) -> str:
    """Classify the importance of a report for alerting.

    Alert levels:
        긴급: Portfolio ticker with target change >= 20% OR opinion downgrade.
        중요: Portfolio ticker new report, OR tenbagger ticker report.
        참고: Report belongs to a watched sector.
        "": Not relevant enough to alert.

    Args:
        report: The broker report to classify.
        portfolio_tickers: Tickers currently in the portfolio.
        tenbagger_tickers: Tickers on the tenbagger watchlist.
        watch_sectors: Sector keywords being watched.

    Returns:
        Alert level string: "긴급", "중요", "참고", or "".
    """
    ticker = report.ticker.strip()
    in_portfolio = ticker in portfolio_tickers
    in_tenbagger = ticker in tenbagger_tickers

    target_change = abs(compute_target_change_pct(
        report.prev_target_price, report.target_price,
    ))
    opinion_downgraded = _is_opinion_downgrade(report.prev_opinion, report.opinion)

    # 긴급: portfolio ticker + big target change OR opinion downgrade
    if in_portfolio:
        if target_change >= 20.0 or opinion_downgraded:
            logger.info(
                "긴급 alert: %s target_change=%.1f%% downgrade=%s",
                ticker, target_change, opinion_downgraded,
            )
            return "긴급"

    # 중요: portfolio ticker new report, or tenbagger ticker
    if in_portfolio:
        return "중요"
    if in_tenbagger:
        return "중요"

    # 참고: watch sector
    title_lower = report.title.lower()
    summary_lower = report.summary.lower()
    for sector in watch_sectors:
        sector_lower = sector.lower()
        if sector_lower in title_lower or sector_lower in summary_lower:
            return "참고"

    return ""


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

_LEVEL_EMOJI: dict[str, str] = {
    "긴급": "\U0001f6a8",   # rotating light
    "중요": "\U0001f4e2",   # loudspeaker
    "참고": "\U0001f4cb",   # clipboard
}


def format_report_alert(
    report: BrokerReport,
    alert_level: str,
    holding_profit_pct: float = 0.0,
) -> str:
    """Format a single report alert message for Telegram.

    No ** bold markers. Uses 주호님 greeting for high-priority alerts.

    Args:
        report: The broker report.
        alert_level: "긴급", "중요", or "참고".
        holding_profit_pct: Current holding profit % (only for portfolio tickers).

    Returns:
        Formatted multi-line Telegram message.
    """
    lines: list[str] = []

    level_emoji = _LEVEL_EMOJI.get(alert_level, "")

    # Header with greeting for urgent
    if alert_level == "긴급":
        lines.append(f"{level_emoji} 주호님, 긴급 리포트 알림!")
    elif alert_level == "중요":
        lines.append(f"{level_emoji} 주호님, 새 리포트가 나왔습니다.")
    else:
        lines.append(f"{level_emoji} 리포트 참고 알림")

    lines.append("")

    # Report details
    lines.append(f"종목: {report.ticker}")
    lines.append(f"증권사: {report.broker}")
    lines.append(f"제목: {report.title}")
    lines.append(f"날짜: {report.date}")
    lines.append("")

    # Opinion
    opinion_change = parse_opinion_change(report.prev_opinion, report.opinion)
    if opinion_change:
        direction = "하향" if _is_opinion_downgrade(report.prev_opinion, report.opinion) else "상향"
        lines.append(f"의견 변경: {opinion_change} ({direction})")
    else:
        lines.append(f"투자의견: {report.opinion}")

    # Target price
    target_change_pct = compute_target_change_pct(
        report.prev_target_price, report.target_price,
    )
    if report.prev_target_price > 0 and target_change_pct != 0.0:
        lines.append(
            f"목표가: {report.target_price:,.0f}원 "
            f"(이전 {report.prev_target_price:,.0f}원, {target_change_pct:+.1f}%)"
        )
    else:
        lines.append(f"목표가: {report.target_price:,.0f}원")

    lines.append("")

    # Summary
    if report.summary:
        lines.append(f"요약: {report.summary}")
        lines.append("")

    # Holding context
    if holding_profit_pct != 0.0:
        lines.append(f"현재 보유 수익률: {holding_profit_pct:+.1f}%")

    # Urgent action hint
    if alert_level == "긴급":
        if _is_opinion_downgrade(report.prev_opinion, report.opinion):
            lines.append("")
            lines.append("주호님, 의견 하향이니 대응 전략을 점검하세요.")
        elif abs(target_change_pct) >= 20.0:
            direction_text = "상향" if target_change_pct > 0 else "하향"
            lines.append("")
            lines.append(f"주호님, 목표가 대폭 {direction_text}입니다. 확인 필요합니다.")

    # PDF link
    if report.pdf_url:
        lines.append("")
        lines.append(f"리포트 원문: {report.pdf_url}")

    return "\n".join(lines)
