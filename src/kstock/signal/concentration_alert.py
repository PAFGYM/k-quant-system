"""Portfolio concentration alert system (Section 36 - 포트폴리오 편중 경고).

Detects over-concentration in single stocks, sectors, or correlated
positions and suggests rebalancing actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector mapping for common Korean tickers
# ---------------------------------------------------------------------------
SECTOR_MAP: dict[str, str] = {
    # 반도체
    "005930": "반도체",   # 삼성전자
    "000660": "반도체",   # SK하이닉스
    # 2차전지
    "373220": "2차전지",  # LG에너지솔루션
    "006400": "2차전지",  # 삼성SDI
    "247540": "2차전지",  # 에코프로비엠
    "086520": "2차전지",  # 에코프로
    # 소프트웨어 / 플랫폼
    "035420": "소프트웨어",  # NAVER
    "035720": "소프트웨어",  # 카카오
    # 바이오
    "207940": "바이오",   # 삼성바이오로직스
    "068270": "바이오",   # 셀트리온
    # 자동차
    "005380": "자동차",   # 현대차
    "000270": "자동차",   # 기아
    # 금융
    "055550": "금융",     # 신한지주
    "105560": "금융",     # KB금융
    "316140": "금융",     # 우리금융지주
    # 철강 / 화학
    "005490": "철강",     # POSCO홀딩스
    "051910": "화학",     # LG화학
    # 통신
    "017670": "통신",     # SK텔레콤
    "030200": "통신",     # KT
    # 엔터
    "352820": "엔터",     # 하이브
    # 조선
    "009540": "조선",     # 한국조선해양
    # 방산
    "012450": "방산",     # 한화에어로스페이스
    # 유통
    "004170": "유통",     # 신세계
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ConcentrationAlert:
    """Single concentration alert."""

    alert_type: str   # "single_stock" / "single_sector" / "correlation" / "no_cash"
    severity: str     # "warning" / "danger"
    message: str
    suggestion: str


@dataclass
class ConcentrationReport:
    """Full concentration analysis report."""

    alerts: list[ConcentrationAlert] = field(default_factory=list)
    sector_weights: dict[str, float] = field(default_factory=dict)
    top_position_pct: float = 0.0
    cash_pct: float = 0.0
    score: int = 100  # 0-100, higher = better diversified


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------
def analyze_concentration(
    holdings: list[dict],
    cash: float = 0,
    total_eval: float = 0,
) -> ConcentrationReport:
    """Analyze portfolio concentration and generate alerts.

    Args:
        holdings: List of holding dicts, each with keys:
            ticker (str), name (str), eval_amount (float), profit_pct (float)
        cash: Cash amount in the portfolio.
        total_eval: Total evaluation amount. If 0, computed from holdings + cash.

    Returns:
        ConcentrationReport with alerts and metrics.
    """
    if not holdings:
        return ConcentrationReport(score=100)

    # Compute total evaluation if not provided
    holdings_total = sum(h.get("eval_amount", 0) for h in holdings)
    if total_eval <= 0:
        total_eval = holdings_total + cash

    if total_eval <= 0:
        return ConcentrationReport(score=100)

    alerts: list[ConcentrationAlert] = []
    penalty = 0

    # --- Per-holding weight ---
    position_weights: list[tuple[str, str, float]] = []
    for h in holdings:
        pct = h.get("eval_amount", 0) / total_eval * 100
        position_weights.append((h.get("ticker", ""), h.get("name", ""), pct))

    position_weights.sort(key=lambda x: x[2], reverse=True)
    top_position_pct = position_weights[0][2] if position_weights else 0.0

    # v5.2: 레거시 종목 제외 목록
    from kstock.core.risk_manager import LEGACY_EXEMPT_TICKERS

    # Single stock > 40% -> warning
    for ticker, name, pct in position_weights:
        if ticker in LEGACY_EXEMPT_TICKERS:
            continue  # v5.2: 레거시 종목은 비중 경고 제외
        if pct > 40:
            alerts.append(ConcentrationAlert(
                alert_type="single_stock",
                severity="warning",
                message=f"{name} 비중 {pct:.1f}% (40% 초과)",
                suggestion=f"{name} 일부 익절 후 다른 섹터로 분산 필요",
            ))
            penalty += 20

    # --- Sector weights ---
    sector_amounts: dict[str, float] = {}
    for h in holdings:
        ticker = h.get("ticker", "")
        sector = SECTOR_MAP.get(ticker, "기타")
        sector_amounts[sector] = sector_amounts.get(sector, 0) + h.get("eval_amount", 0)

    sector_weights: dict[str, float] = {}
    for sector, amount in sector_amounts.items():
        sector_weights[sector] = round(amount / total_eval * 100, 1)

    # Single sector > 50% -> danger
    for sector, pct in sector_weights.items():
        if pct > 50:
            alerts.append(ConcentrationAlert(
                alert_type="single_sector",
                severity="danger",
                message=f"{sector} 섹터 비중 {pct:.1f}% (50% 초과)",
                suggestion=f"{sector} 외 다른 섹터 종목 매수로 분산 필요",
            ))
            penalty += 25

    # --- Cash analysis ---
    cash_pct = cash / total_eval * 100 if total_eval > 0 else 0.0

    if cash_pct == 0:
        alerts.append(ConcentrationAlert(
            alert_type="no_cash",
            severity="warning",
            message="현금 비중 0%",
            suggestion="현금 확보 필요",
        ))
        penalty += 15
    elif cash_pct < 5:
        alerts.append(ConcentrationAlert(
            alert_type="no_cash",
            severity="warning",
            message=f"현금 비중 {cash_pct:.1f}% (5% 미만)",
            suggestion="추가 하락 대비 현금 5% 이상 확보 권장",
        ))
        penalty += 10

    # --- Diversification score ---
    # Start from 100 and deduct penalties
    score = max(0, min(100, 100 - penalty))

    # Additional penalty for too few holdings
    if len(holdings) <= 2:
        score = max(0, score - 10)

    report = ConcentrationReport(
        alerts=alerts,
        sector_weights=sector_weights,
        top_position_pct=round(top_position_pct, 1),
        cash_pct=round(cash_pct, 1),
        score=score,
    )

    logger.info(
        "Concentration analysis: score=%d, alerts=%d, top_pct=%.1f%%",
        report.score, len(alerts), top_position_pct,
    )
    return report


# ---------------------------------------------------------------------------
# Rebalance suggestions
# ---------------------------------------------------------------------------
def suggest_rebalance(
    report: ConcentrationReport,
    holdings: list[dict],
) -> list[str]:
    """Generate specific rebalance suggestions.

    Args:
        report: ConcentrationReport from analyze_concentration.
        holdings: Same holdings list used for analysis.

    Returns:
        List of actionable suggestion strings.
    """
    suggestions: list[str] = []

    if not holdings:
        return suggestions

    # Sort by eval_amount descending
    sorted_holdings = sorted(
        holdings, key=lambda h: h.get("eval_amount", 0), reverse=True,
    )

    # Collect sectors present
    present_sectors = set()
    for h in holdings:
        present_sectors.add(SECTOR_MAP.get(h.get("ticker", ""), "기타"))

    for alert in report.alerts:
        if alert.alert_type == "single_stock":
            # Find the over-weighted stock
            for h in sorted_holdings:
                name = h.get("name", "")
                if name in alert.message:
                    eval_amount = h.get("eval_amount", 0)
                    profit_pct = h.get("profit_pct", 0)
                    # Suggest selling portion if profitable
                    if profit_pct > 0:
                        sell_amount = int(eval_amount * 0.3)
                        sell_display = _format_krw(sell_amount)
                        suggestions.append(
                            f"{name} 일부 익절 → {sell_display} 현금 확보"
                        )
                    break

        elif alert.alert_type == "single_sector":
            # Suggest buying a different sector
            all_sectors = {"반도체", "2차전지", "바이오", "소프트웨어", "자동차", "금융"}
            missing = all_sectors - present_sectors
            if missing:
                alt_sector = sorted(missing)[0]
                suggestions.append(
                    f"{alt_sector} 섹터 종목 매수 → 섹터 분산"
                )

        elif alert.alert_type == "no_cash":
            # Find the most profitable stock to trim
            for h in sorted_holdings:
                if h.get("profit_pct", 0) > 10:
                    name = h.get("name", "")
                    eval_amount = h.get("eval_amount", 0)
                    sell_amount = int(eval_amount * 0.2)
                    sell_display = _format_krw(sell_amount)
                    suggestions.append(
                        f"{name} 일부 익절 → {sell_display} 현금 확보"
                    )
                    break

    return suggestions


def _format_krw(amount: int) -> str:
    """Format KRW amount in human-readable form (만원 / 억원)."""
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.1f}억원"
    if amount >= 10_000:
        return f"{amount / 10_000:,.0f}만원"
    return f"{amount:,}원"


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------
def format_concentration_report(report: ConcentrationReport) -> str:
    """Format ConcentrationReport for Telegram.

    Uses emojis for visual cues, avoids ** bold markdown.
    """
    lines: list[str] = []

    # Header
    if report.score >= 80:
        grade_emoji = "\U0001f7e2"  # green circle
    elif report.score >= 50:
        grade_emoji = "\U0001f7e1"  # yellow circle
    else:
        grade_emoji = "\U0001f534"  # red circle

    lines.append(f"\U0001f4ca 포트폴리오 편중 분석  {grade_emoji} {report.score}점")
    lines.append("")

    # Sector weights
    if report.sector_weights:
        lines.append("\U0001f4c1 섹터 비중")
        for sector, pct in sorted(
            report.sector_weights.items(), key=lambda x: x[1], reverse=True,
        ):
            bar = _bar_chart(pct)
            lines.append(f"  {sector} {bar} {pct:.1f}%")
        lines.append("")

    # Top position
    lines.append(f"\U0001f3af 최대 종목 비중: {report.top_position_pct:.1f}%")
    lines.append(f"\U0001f4b5 현금 비중: {report.cash_pct:.1f}%")
    lines.append("")

    # Alerts
    if report.alerts:
        lines.append("\u26a0\ufe0f 경고")
        for alert in report.alerts:
            severity_icon = "\U0001f534" if alert.severity == "danger" else "\U0001f7e1"
            lines.append(f"  {severity_icon} {alert.message}")
            lines.append(f"    \u2192 {alert.suggestion}")
        lines.append("")
    else:
        lines.append("\u2705 편중 경고 없음")
        lines.append("")

    return "\n".join(lines)


def _bar_chart(pct: float, width: int = 10) -> str:
    """Create a simple text bar chart."""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    return "\u2588" * filled + "\u2591" * (width - filled)
