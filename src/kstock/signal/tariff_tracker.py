"""US tariff impact tracker (Section 63 - 관세 영향 트래커).

Tracks current US tariff regimes affecting Korean stocks and computes
per-ticker impact scores.  Maintains a static mapping of active tariffs
and per-ticker exposure assessments.

All functions are pure computation with no external API calls at runtime.
Tariff data is maintained as module-level constants and can be updated
when policies change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tariff regime data
# ---------------------------------------------------------------------------

CURRENT_TARIFFS: dict[str, dict] = {
    "global_15": {
        "rate": 15,
        "basis": "무역법 122조",
        "expiry": "2026-07-24",
        "status": "유효",
        "description": "글로벌 기본 관세 15%",
        "category_kr": "글로벌 기본",
    },
    "auto_25": {
        "rate": 25,
        "basis": "무역확장법 232조",
        "expiry": None,
        "status": "유효",
        "description": "자동차 및 부품 관세 25%",
        "category_kr": "자동차",
    },
    "steel_50": {
        "rate": 50,
        "basis": "무역확장법 232조",
        "expiry": None,
        "status": "유효",
        "description": "철강 관세 50%",
        "category_kr": "철강",
    },
    "semiconductor_25": {
        "rate": 25,
        "basis": "232조",
        "expiry": None,
        "status": "유효",
        "description": "반도체 관세 25%",
        "category_kr": "반도체",
    },
    "reciprocal": {
        "rate": 0,
        "basis": "위헌 판결",
        "expiry": None,
        "status": "무효",
        "description": "상호 관세 (위헌 판결로 무효화)",
        "category_kr": "상호 관세",
    },
}
"""Current US tariff regimes affecting Korean exports.

Each key identifies a tariff category with:
    rate: Tariff rate percentage.
    basis: Legal basis.
    expiry: Expiration date (YYYY-MM-DD) or None.
    status: "유효" (active) or "무효" (void).
    description: Korean description.
    category_kr: Korean category label.
"""

TICKER_TARIFF_IMPACT: dict[str, dict] = {
    "086520": {
        "name": "에코프로",
        "impact": "제한적",
        "detail": "배터리 소재, 핵심광물 면제 가능성",
        "tariff_categories": ["global_15"],
    },
    "247540": {
        "name": "에코프로비엠",
        "impact": "제한적",
        "detail": "양극재, 핵심광물 면제 가능성",
        "tariff_categories": ["global_15"],
    },
    "005380": {
        "name": "현대차",
        "impact": "부정적",
        "detail": "자동차 25% 관세 직접 영향",
        "tariff_categories": ["auto_25"],
    },
    "005930": {
        "name": "삼성전자",
        "impact": "주의",
        "detail": "반도체 25% 관세, 미국 공장 투자로 일부 상쇄",
        "tariff_categories": ["semiconductor_25"],
    },
    "000660": {
        "name": "SK하이닉스",
        "impact": "주의",
        "detail": "반도체 25% 관세, HBM은 예외 가능성",
        "tariff_categories": ["semiconductor_25"],
    },
    "000270": {
        "name": "기아",
        "impact": "부정적",
        "detail": "자동차 25% 관세, 미국 생산 비중 낮음",
        "tariff_categories": ["auto_25"],
    },
    "005490": {
        "name": "POSCO홀딩스",
        "impact": "부정적",
        "detail": "철강 50% 관세 직접 영향",
        "tariff_categories": ["steel_50"],
    },
    "035420": {
        "name": "NAVER",
        "impact": "면제",
        "detail": "소프트웨어/서비스, 관세 무관",
        "tariff_categories": [],
    },
    "035720": {
        "name": "카카오",
        "impact": "면제",
        "detail": "소프트웨어/서비스, 관세 무관",
        "tariff_categories": [],
    },
}
"""Per-ticker tariff impact assessments.

Impact levels:
    "부정적": Direct negative impact from tariffs.
    "주의":   Moderate exposure, partial mitigation possible.
    "제한적": Minimal exposure or exemption likely.
    "면제":   Not subject to goods tariffs.
"""

IMPACT_SCORE_MAP: dict[str, int] = {
    "부정적": -10,
    "주의": -5,
    "제한적": 0,
    "면제": 3,
}
"""Score adjustments by impact level."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class TariffChange:
    """Represents a tariff regime change.

    Attributes:
        category: Tariff category key (e.g. "auto_25").
        prev_rate: Previous tariff rate.
        new_rate: New tariff rate.
        effective_date: Effective date (YYYY-MM-DD).
        description: Korean description of the change.
        affected_tickers: List of affected ticker codes.
        message: Pre-formatted Telegram message.
    """

    category: str = ""
    prev_rate: float = 0.0
    new_rate: float = 0.0
    effective_date: str = ""
    description: str = ""
    affected_tickers: list[str] = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Impact evaluation
# ---------------------------------------------------------------------------

def evaluate_tariff_impact(
    ticker: str,
    name: str = "",
) -> dict:
    """Return tariff impact assessment for a ticker.

    Combines TICKER_TARIFF_IMPACT data with CURRENT_TARIFFS to produce
    a comprehensive assessment.

    Args:
        ticker: Stock ticker code.
        name: Stock name (used as fallback label if ticker not in mapping).

    Returns:
        Dict with keys:
            "ticker": ticker code
            "name": stock name
            "impact": impact level string
            "detail": Korean detail description
            "score_adj": integer score adjustment
            "applicable_tariffs": list of applicable tariff dicts
            "summary": Korean summary sentence
    """
    ticker_info = TICKER_TARIFF_IMPACT.get(ticker)

    if ticker_info is None:
        display_name = name or ticker
        logger.debug(
            "Tariff impact: %s not in TICKER_TARIFF_IMPACT, returning generic",
            display_name,
        )
        return {
            "ticker": ticker,
            "name": display_name,
            "impact": "미확인",
            "detail": "관세 영향 미확인. 섹터별 개별 확인 필요.",
            "score_adj": 0,
            "applicable_tariffs": [],
            "summary": f"{display_name}: 관세 영향 미확인",
        }

    display_name = ticker_info.get("name", name or ticker)
    impact = ticker_info.get("impact", "미확인")
    detail = ticker_info.get("detail", "")
    categories = ticker_info.get("tariff_categories", [])

    # Gather applicable tariff details
    applicable_tariffs: list[dict] = []
    for cat_key in categories:
        tariff = CURRENT_TARIFFS.get(cat_key)
        if tariff and tariff.get("status") == "유효":
            applicable_tariffs.append({
                "category": cat_key,
                "rate": tariff["rate"],
                "basis": tariff["basis"],
                "category_kr": tariff.get("category_kr", cat_key),
            })

    score_adj = IMPACT_SCORE_MAP.get(impact, 0)

    # Build summary
    if applicable_tariffs:
        rates_str = ", ".join(
            f"{t['category_kr']} {t['rate']}%"
            for t in applicable_tariffs
        )
        summary = f"{display_name}: {impact} ({rates_str})"
    else:
        summary = f"{display_name}: {impact}"

    logger.debug(
        "Tariff impact %s(%s): %s, score_adj=%+d, tariffs=%d",
        display_name, ticker, impact, score_adj, len(applicable_tariffs),
    )

    return {
        "ticker": ticker,
        "name": display_name,
        "impact": impact,
        "detail": detail,
        "score_adj": score_adj,
        "applicable_tariffs": applicable_tariffs,
        "summary": summary,
    }


def compute_tariff_score_adj(ticker: str) -> int:
    """Return score adjustment for tariff impact.

    Score adjustments:
        부정적: -10
        주의:   -5
        제한적:  0
        면제:   +3

    Args:
        ticker: Stock ticker code.

    Returns:
        Integer score adjustment.
    """
    ticker_info = TICKER_TARIFF_IMPACT.get(ticker)
    if ticker_info is None:
        return 0

    impact = ticker_info.get("impact", "미확인")
    adj = IMPACT_SCORE_MAP.get(impact, 0)

    logger.debug(
        "Tariff score adj %s: impact=%s -> %+d",
        ticker, impact, adj,
    )

    return adj


def get_affected_tickers(category: str) -> list[str]:
    """Return list of tickers affected by a tariff category.

    Args:
        category: Tariff category key (e.g. "auto_25", "semiconductor_25").

    Returns:
        List of ticker codes.
    """
    affected = []
    for ticker, info in TICKER_TARIFF_IMPACT.items():
        categories = info.get("tariff_categories", [])
        if category in categories:
            affected.append(ticker)
    return affected


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_tariff_change_alert(change: TariffChange) -> str:
    """Format a tariff change alert for Telegram.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        change: TariffChange to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        [관세 변동] 자동차 관세 변경
        이전: 25% -> 변경: 30%
        시행일: 2026-04-01
        내용: 자동차 관세 25%에서 30%로 인상
        영향 종목: 현대차, 기아

        주호님, 관세 변동이 발생했습니다.
        보유 종목 영향을 확인하세요.
    """
    tariff_info = CURRENT_TARIFFS.get(change.category, {})
    category_kr = tariff_info.get("category_kr", change.category)

    lines = [
        f"[관세 변동] {category_kr} 관세 변경",
        f"이전: {change.prev_rate:.0f}% -> 변경: {change.new_rate:.0f}%",
    ]

    if change.effective_date:
        lines.append(f"시행일: {change.effective_date}")

    if change.description:
        lines.append(f"내용: {change.description}")

    # List affected ticker names
    if change.affected_tickers:
        affected_names = []
        for t in change.affected_tickers:
            info = TICKER_TARIFF_IMPACT.get(t)
            if info:
                affected_names.append(info["name"])
            else:
                affected_names.append(t)
        lines.append(f"영향 종목: {', '.join(affected_names)}")

    # Rate direction analysis
    if change.new_rate > change.prev_rate:
        direction = "인상"
        advice = "해당 섹터 비중 축소를 검토하세요."
    elif change.new_rate < change.prev_rate:
        direction = "인하"
        advice = "해당 섹터 수혜 가능성을 확인하세요."
    else:
        direction = "유지"
        advice = "큰 변화 없이 유지됩니다."

    lines.append(f"방향: 관세 {direction}")
    lines.append("")
    lines.append(f"주호님, 관세 변동이 발생했습니다.")
    lines.append(advice)

    return "\n".join(lines)


def format_tariff_status() -> str:
    """Format current tariff status summary for Telegram.

    Produces a comprehensive overview of all active tariff regimes
    and their impact on tracked Korean stocks.

    Returns:
        Multi-line formatted string without bold (**) formatting.

    Example output::

        [관세 현황] 미국 대한국 관세 요약

        글로벌 기본: 15% (무역법 122조, 유효기간 ~2026-07-24)
        자동차: 25% (무역확장법 232조)
        철강: 50% (무역확장법 232조)
        반도체: 25% (232조)
        상호 관세: 무효 (위헌 판결)

        [종목별 영향]
        현대차: 부정적 - 자동차 25% 관세 직접 영향
        삼성전자: 주의 - 반도체 25% 관세, 미국 공장 투자로 일부 상쇄
        ...

        주호님, 관세 현황 참고하세요.
    """
    lines = [
        "[관세 현황] 미국 대한국 관세 요약",
        "",
    ]

    # Active tariffs
    for key, tariff in CURRENT_TARIFFS.items():
        category_kr = tariff.get("category_kr", key)
        rate = tariff["rate"]
        basis = tariff["basis"]
        status = tariff["status"]
        expiry = tariff.get("expiry")

        if status == "무효":
            lines.append(f"{category_kr}: 무효 ({basis})")
        else:
            line = f"{category_kr}: {rate}% ({basis}"
            if expiry:
                line += f", 유효기간 ~{expiry}"
            line += ")"
            lines.append(line)

    # Per-ticker impact summary
    lines.append("")
    lines.append("[종목별 영향]")

    # Sort by impact severity: 부정적 first, then 주의, 제한적, 면제
    impact_order = {"부정적": 0, "주의": 1, "제한적": 2, "면제": 3}
    sorted_tickers = sorted(
        TICKER_TARIFF_IMPACT.items(),
        key=lambda item: impact_order.get(item[1].get("impact", ""), 99),
    )

    for ticker, info in sorted_tickers:
        name = info["name"]
        impact = info["impact"]
        detail = info["detail"]
        lines.append(f"{name}: {impact} - {detail}")

    lines.append("")
    lines.append("주호님, 관세 현황 참고하세요.")

    return "\n".join(lines)
