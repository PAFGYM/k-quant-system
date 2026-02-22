"""Broker report alert evaluation (Section 48 - 리포트 알림 평가).

Evaluates a batch of broker research reports against portfolio holdings,
tenbagger watchlist, and sector interests. Generates prioritized alerts
and summary messages for Telegram delivery.

All functions are pure computation — no network or API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ReportAlert:
    """Evaluated alert for a single broker report."""

    report: dict          # BrokerReport fields as dict
    alert_level: str      # "긴급", "중요", "참고"
    holding_info: dict    # current profit, qty, etc.
    message: str


# ---------------------------------------------------------------------------
# Alert level priority for sorting
# ---------------------------------------------------------------------------

_LEVEL_PRIORITY: dict[str, int] = {
    "긴급": 0,
    "중요": 1,
    "참고": 2,
}

_OPINION_RANK: dict[str, int] = {
    "매수": 3,
    "중립": 2,
    "매도": 1,
}

_LEVEL_EMOJI: dict[str, str] = {
    "긴급": "\U0001f6a8",
    "중요": "\U0001f4e2",
    "참고": "\U0001f4cb",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_target_change_pct(prev: float, current: float) -> float:
    """Compute target price change percentage."""
    if prev <= 0:
        return 0.0
    return round((current - prev) / prev * 100, 2)


def _is_opinion_downgrade(prev: str, current: str) -> bool:
    """Return True if opinion was downgraded."""
    prev_rank = _OPINION_RANK.get(prev.strip(), 0)
    curr_rank = _OPINION_RANK.get(current.strip(), 0)
    return curr_rank < prev_rank and prev_rank > 0


def _parse_opinion_change(prev: str, current: str) -> str:
    """Format opinion change string."""
    prev = prev.strip()
    current = current.strip()
    if not prev or not current or prev == current:
        return ""
    return f"{prev}->{current}"


def _classify_alert_level(
    report: dict,
    portfolio_tickers: list[str],
    tenbagger_tickers: list[str],
    watch_sectors: list[str],
) -> str:
    """Classify alert level for a single report dict.

    See report_crawler.classify_alert_level for full logic description.
    """
    ticker = report.get("ticker", "").strip()
    in_portfolio = ticker in portfolio_tickers
    in_tenbagger = ticker in tenbagger_tickers

    prev_target = report.get("prev_target_price", 0.0)
    curr_target = report.get("target_price", 0.0)
    target_change = abs(_compute_target_change_pct(prev_target, curr_target))

    prev_opinion = report.get("prev_opinion", "")
    curr_opinion = report.get("opinion", "")
    downgraded = _is_opinion_downgrade(prev_opinion, curr_opinion)

    if in_portfolio and (target_change >= 20.0 or downgraded):
        return "긴급"
    if in_portfolio or in_tenbagger:
        return "중요"

    title_lower = report.get("title", "").lower()
    summary_lower = report.get("summary", "").lower()
    for sector in watch_sectors:
        if sector.lower() in title_lower or sector.lower() in summary_lower:
            return "참고"

    return ""


def _find_holding_info(ticker: str, portfolio: list[dict]) -> dict:
    """Find holding info for a ticker in the portfolio.

    Args:
        ticker: Stock ticker code.
        portfolio: List of holding dicts with keys: ticker, name, profit_pct, etc.

    Returns:
        Matching holding dict or empty dict.
    """
    for holding in portfolio:
        if holding.get("ticker", "").strip() == ticker.strip():
            return holding
    return {}


def _format_single_alert(report: dict, alert_level: str, holding_info: dict) -> str:
    """Format a single report into a Telegram message.

    No ** bold. Uses 주호님 greeting for urgent/important alerts.
    """
    lines: list[str] = []
    level_emoji = _LEVEL_EMOJI.get(alert_level, "")

    # Header
    if alert_level == "긴급":
        lines.append(f"{level_emoji} 주호님, 긴급 리포트!")
    elif alert_level == "중요":
        lines.append(f"{level_emoji} 주호님, 새 리포트 알림")
    else:
        lines.append(f"{level_emoji} 리포트 참고")

    lines.append("")

    # Core info
    ticker = report.get("ticker", "")
    broker = report.get("broker", "")
    title = report.get("title", "")
    date = report.get("date", "")

    lines.append(f"종목: {ticker}")
    lines.append(f"증권사: {broker}")
    lines.append(f"제목: {title}")
    lines.append(f"날짜: {date}")
    lines.append("")

    # Opinion
    prev_opinion = report.get("prev_opinion", "")
    opinion = report.get("opinion", "")
    change_str = _parse_opinion_change(prev_opinion, opinion)
    if change_str:
        direction = "하향" if _is_opinion_downgrade(prev_opinion, opinion) else "상향"
        lines.append(f"투자의견: {change_str} ({direction})")
    else:
        lines.append(f"투자의견: {opinion}")

    # Target price
    prev_target = report.get("prev_target_price", 0.0)
    curr_target = report.get("target_price", 0.0)
    change_pct = _compute_target_change_pct(prev_target, curr_target)
    if prev_target > 0 and change_pct != 0.0:
        lines.append(
            f"목표가: {curr_target:,.0f}원 "
            f"(이전 {prev_target:,.0f}원, {change_pct:+.1f}%)"
        )
    elif curr_target > 0:
        lines.append(f"목표가: {curr_target:,.0f}원")

    # Summary
    summary = report.get("summary", "")
    if summary:
        lines.append("")
        lines.append(f"요약: {summary}")

    # Holding context
    profit_pct = holding_info.get("profit_pct", 0.0)
    if profit_pct != 0.0:
        lines.append("")
        lines.append(f"현재 보유 수익률: {profit_pct:+.1f}%")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_reports(
    reports: list[dict],
    portfolio: list[dict],
    tenbagger_tickers: list[str],
    watch_sectors: list[str],
) -> list[ReportAlert]:
    """Evaluate a list of new reports and generate prioritized alerts.

    Args:
        reports: List of report dicts (BrokerReport fields).
        portfolio: List of holding dicts with keys: ticker, name, profit_pct, etc.
        tenbagger_tickers: Tickers on the tenbagger watchlist.
        watch_sectors: Sector keywords being watched.

    Returns:
        List of ReportAlert sorted by priority (긴급 first, then 중요, then 참고).
        Reports that do not match any alert level are excluded.
    """
    portfolio_tickers = [h.get("ticker", "").strip() for h in portfolio]
    alerts: list[ReportAlert] = []

    for report in reports:
        level = _classify_alert_level(
            report, portfolio_tickers, tenbagger_tickers, watch_sectors,
        )
        if not level:
            continue

        ticker = report.get("ticker", "").strip()
        holding_info = _find_holding_info(ticker, portfolio)
        message = _format_single_alert(report, level, holding_info)

        alerts.append(ReportAlert(
            report=report,
            alert_level=level,
            holding_info=holding_info,
            message=message,
        ))

    # Sort by priority
    alerts.sort(key=lambda a: _LEVEL_PRIORITY.get(a.alert_level, 99))

    logger.info(
        "Evaluated %d reports -> %d alerts (긴급=%d, 중요=%d, 참고=%d)",
        len(reports),
        len(alerts),
        sum(1 for a in alerts if a.alert_level == "긴급"),
        sum(1 for a in alerts if a.alert_level == "중요"),
        sum(1 for a in alerts if a.alert_level == "참고"),
    )

    return alerts


def format_opinion_downgrade_alert(report: dict, consensus_info: dict) -> str:
    """Format a special alert for opinion downgrade with consensus context.

    Includes other brokers' opinions for comparison so the user can gauge
    whether this is a single-broker anomaly or a broader trend.

    Args:
        report: Report dict with opinion downgrade.
        consensus_info: Dict with keys:
            - avg_target_price (float): Average target across brokers.
            - opinions (dict): e.g. {"매수": 4, "중립": 1, "매도": 0}
            - total_brokers (int): Total broker coverage count.

    Returns:
        Formatted Telegram message without ** bold.
    """
    lines: list[str] = []

    ticker = report.get("ticker", "")
    broker = report.get("broker", "")
    prev_opinion = report.get("prev_opinion", "")
    opinion = report.get("opinion", "")

    lines.append("\U0001f6a8 주호님, 투자의견 하향 알림!")
    lines.append("")
    lines.append(f"종목: {ticker}")
    lines.append(f"증권사: {broker}")
    lines.append(f"의견 변경: {prev_opinion}->{opinion}")
    lines.append("")

    # Consensus context
    opinions = consensus_info.get("opinions", {})
    total = consensus_info.get("total_brokers", 0)
    avg_target = consensus_info.get("avg_target_price", 0.0)

    if total > 0:
        lines.append("타 증권사 의견 현황:")
        for op_type in ("매수", "중립", "매도"):
            count = opinions.get(op_type, 0)
            if count > 0:
                lines.append(f"  {op_type}: {count}개사")
        lines.append(f"  평균 목표가: {avg_target:,.0f}원")
        lines.append(f"  커버리지: {total}개사")
    else:
        lines.append("타 증권사 의견 정보 없음")

    lines.append("")

    # Guidance
    buy_count = opinions.get("매수", 0)
    if total > 0 and buy_count / total >= 0.7:
        lines.append("대다수 증권사가 여전히 매수 의견입니다.")
        lines.append("단독 하향일 수 있으니 추이를 지켜보세요.")
    elif total > 0:
        lines.append("주호님, 의견 하향 추세가 확산될 수 있습니다.")
        lines.append("리스크 관리를 점검하세요.")
    else:
        lines.append("주호님, 해당 종목 의견 변경을 주시하세요.")

    return "\n".join(lines)


def format_report_summary(alerts: list[ReportAlert]) -> str:
    """Format a summary of recent report alerts for menu display.

    Groups alerts by level and shows counts with representative tickers.

    Args:
        alerts: List of ReportAlert (already evaluated).

    Returns:
        Formatted summary string for Telegram. No ** bold.
    """
    if not alerts:
        return "\U0001f4cb 최근 리포트 알림이 없습니다."

    lines: list[str] = []
    lines.append("\U0001f4f0 리포트 알림 요약")
    lines.append("")

    # Group by level
    by_level: dict[str, list[ReportAlert]] = {
        "긴급": [],
        "중요": [],
        "참고": [],
    }
    for alert in alerts:
        level = alert.alert_level
        if level in by_level:
            by_level[level].append(alert)

    for level in ("긴급", "중요", "참고"):
        group = by_level[level]
        if not group:
            continue
        emoji = _LEVEL_EMOJI.get(level, "")
        tickers = [a.report.get("ticker", "") for a in group[:5]]
        ticker_str = ", ".join(tickers)
        if len(group) > 5:
            ticker_str += f" 외 {len(group) - 5}건"
        lines.append(f"{emoji} {level} ({len(group)}건): {ticker_str}")

    lines.append("")

    # Total
    total = len(alerts)
    lines.append(f"총 {total}건의 리포트 알림")

    return "\n".join(lines)
