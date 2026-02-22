"""Financial statement analyzer (Sections 51-52 - 재무제표 분석).

Scores stocks across four dimensions — growth, profitability, stability,
and valuation — and produces a composite financial score (0-100).
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
class FinancialData:
    """Raw financial data for a single ticker."""

    ticker: str
    name: str
    revenue: float            # 매출액 (억원)
    operating_income: float   # 영업이익 (억원)
    net_income: float         # 당기순이익 (억원)
    op_margin: float          # 영업이익률 (%)
    roe: float                # 자기자본이익률 (%)
    roa: float                # 총자산이익률 (%)
    debt_ratio: float         # 부채비율 (%)
    current_ratio: float      # 유동비율 (%)
    per: float                # 주가수익비율
    pbr: float                # 주가순자산비율
    eps: float                # 주당순이익 (원)
    bps: float                # 주당순자산 (원)
    dps: float                # 주당배당금 (원)
    fcf: float                # 잉여현금흐름 (억원)
    ebitda: float             # EBITDA (억원)


@dataclass
class FinancialScore:
    """Composite financial score result."""

    growth: int = 0           # 0-25
    profitability: int = 0    # 0-25
    stability: int = 0        # 0-25
    valuation: int = 0        # 0-25
    total: int = 0            # 0-100
    details: dict = field(default_factory=dict)  # breakdown per dimension
    score_bonus: int = 0      # bonus for composite scoring


# ---------------------------------------------------------------------------
# Scoring: Growth (0-25)
# ---------------------------------------------------------------------------


def score_growth(
    revenue_yoy: float,
    op_income_yoy: float,
    cagr_3y: float = 0.0,
) -> tuple[int, list[str]]:
    """Score growth dimension on a 0-25 scale.

    Scoring rules:
        Revenue YoY:
            +20% 이상: 10, +10%: 8, +5%: 6, 0%: 4, negative: 2
        Operating income YoY:
            +20% 이상: 10, +10%: 8, +5%: 6, 0%: 4, negative: 2
        3-year CAGR:
            +15% 이상: 5, +10%: 4, +5%: 3, 0%: 2, negative: 1

    Args:
        revenue_yoy: Revenue year-over-year growth (%).
        op_income_yoy: Operating income year-over-year growth (%).
        cagr_3y: 3-year revenue CAGR (%).

    Returns:
        Tuple of (score, detail_strings).
    """
    details: list[str] = []
    score = 0

    # Revenue YoY (max 10)
    if revenue_yoy >= 20:
        rev_score = 10
    elif revenue_yoy >= 10:
        rev_score = 8
    elif revenue_yoy >= 5:
        rev_score = 6
    elif revenue_yoy >= 0:
        rev_score = 4
    else:
        rev_score = 2
    score += rev_score
    details.append(f"매출 성장 YoY {revenue_yoy:+.1f}% ({rev_score}/10)")

    # Operating income YoY (max 10)
    if op_income_yoy >= 20:
        op_score = 10
    elif op_income_yoy >= 10:
        op_score = 8
    elif op_income_yoy >= 5:
        op_score = 6
    elif op_income_yoy >= 0:
        op_score = 4
    else:
        op_score = 2
    score += op_score
    details.append(f"영업이익 성장 YoY {op_income_yoy:+.1f}% ({op_score}/10)")

    # 3-year CAGR (max 5)
    if cagr_3y >= 15:
        cagr_score = 5
    elif cagr_3y >= 10:
        cagr_score = 4
    elif cagr_3y >= 5:
        cagr_score = 3
    elif cagr_3y >= 0:
        cagr_score = 2
    else:
        cagr_score = 1
    score += cagr_score
    details.append(f"3년 CAGR {cagr_3y:+.1f}% ({cagr_score}/5)")

    # Cap at 25
    score = min(25, score)

    return score, details


# ---------------------------------------------------------------------------
# Scoring: Profitability (0-25)
# ---------------------------------------------------------------------------


def score_profitability(
    roe: float,
    op_margin: float,
    fcf: float,
) -> tuple[int, list[str]]:
    """Score profitability dimension on a 0-25 scale.

    Scoring rules:
        ROE:
            15%+: 10, 10%+: 8, 5%+: 6, 0%+: 3, negative: 1
        Operating margin:
            20%+: 10, 10%+: 8, 5%+: 6, 0%+: 3, negative: 1
        FCF:
            positive: 5, zero/negative: 1

    Args:
        roe: Return on equity (%).
        op_margin: Operating margin (%).
        fcf: Free cash flow (억원).

    Returns:
        Tuple of (score, detail_strings).
    """
    details: list[str] = []
    score = 0

    # ROE (max 10)
    if roe >= 15:
        roe_score = 10
    elif roe >= 10:
        roe_score = 8
    elif roe >= 5:
        roe_score = 6
    elif roe >= 0:
        roe_score = 3
    else:
        roe_score = 1
    score += roe_score
    details.append(f"ROE {roe:.1f}% ({roe_score}/10)")

    # Operating margin (max 10)
    if op_margin >= 20:
        margin_score = 10
    elif op_margin >= 10:
        margin_score = 8
    elif op_margin >= 5:
        margin_score = 6
    elif op_margin >= 0:
        margin_score = 3
    else:
        margin_score = 1
    score += margin_score
    details.append(f"영업이익률 {op_margin:.1f}% ({margin_score}/10)")

    # FCF (max 5)
    if fcf > 0:
        fcf_score = 5
    else:
        fcf_score = 1
    score += fcf_score
    fcf_label = "양호" if fcf > 0 else "부진"
    details.append(f"FCF {fcf:,.0f}억원 ({fcf_label}, {fcf_score}/5)")

    score = min(25, score)

    return score, details


# ---------------------------------------------------------------------------
# Scoring: Stability (0-25)
# ---------------------------------------------------------------------------


def score_stability(
    debt_ratio: float,
    current_ratio: float,
    interest_coverage: float = 5.0,
) -> tuple[int, list[str]]:
    """Score stability dimension on a 0-25 scale.

    Scoring rules:
        Debt ratio:
            < 100%: 10, < 200%: 7, >= 200%: 3
        Current ratio:
            >= 200%: 10, >= 150%: 8, >= 100%: 6, < 100%: 3 (warning)
        Interest coverage:
            >= 10x: 5, >= 5x: 4, >= 3x: 3, < 3x: 1 (warning)

    Args:
        debt_ratio: Debt-to-equity ratio (%).
        current_ratio: Current assets / current liabilities (%).
        interest_coverage: Interest coverage ratio (times).

    Returns:
        Tuple of (score, detail_strings).
    """
    details: list[str] = []
    score = 0

    # Debt ratio (max 10)
    if debt_ratio < 100:
        debt_score = 10
    elif debt_ratio < 200:
        debt_score = 7
    else:
        debt_score = 3
    score += debt_score
    details.append(f"부채비율 {debt_ratio:.0f}% ({debt_score}/10)")
    if debt_ratio >= 200:
        details.append("  -> 부채비율 200% 이상 주의")

    # Current ratio (max 10)
    if current_ratio >= 200:
        curr_score = 10
    elif current_ratio >= 150:
        curr_score = 8
    elif current_ratio >= 100:
        curr_score = 6
    else:
        curr_score = 3
    score += curr_score
    details.append(f"유동비율 {current_ratio:.0f}% ({curr_score}/10)")
    if current_ratio < 100:
        details.append("  -> 유동비율 100% 미만 유동성 주의")

    # Interest coverage (max 5)
    if interest_coverage >= 10:
        int_score = 5
    elif interest_coverage >= 5:
        int_score = 4
    elif interest_coverage >= 3:
        int_score = 3
    else:
        int_score = 1
    score += int_score
    details.append(f"이자보상배율 {interest_coverage:.1f}x ({int_score}/5)")
    if interest_coverage < 3:
        details.append("  -> 이자보상배율 3x 미만 주의")

    score = min(25, score)

    return score, details


# ---------------------------------------------------------------------------
# Scoring: Valuation (0-25)
# ---------------------------------------------------------------------------


def score_valuation(
    per: float,
    sector_avg_per: float,
    pbr: float,
    hist_pbr_median: float = 1.0,
) -> tuple[int, list[str]]:
    """Score valuation dimension on a 0-25 scale.

    Scoring rules:
        PER vs sector average:
            PER < sector * 0.7: 12
            PER < sector * 0.85: 10
            PER < sector * 1.0: 7
            PER >= sector * 1.3: 3
            else: 5
        PBR vs historical median:
            PBR < median * 0.7: 8
            PBR < median * 0.85: 6
            PBR < median * 1.0: 5
            PBR >= median * 1.3: 2
            else: 4
        Negative PER (loss-making): PER score = 2, note added.
        PER/PBR base: additional 5 for very cheap (PER < 8 and PBR < 0.7).

    Args:
        per: Price-to-earnings ratio.
        sector_avg_per: Sector average PER for comparison.
        pbr: Price-to-book ratio.
        hist_pbr_median: Historical median PBR for this stock.

    Returns:
        Tuple of (score, detail_strings).
    """
    details: list[str] = []
    score = 0

    # PER scoring (max 12)
    if per <= 0:
        # Negative PER means loss-making
        per_score = 2
        details.append(f"PER {per:.1f} (적자, {per_score}/12)")
    elif sector_avg_per > 0:
        ratio = per / sector_avg_per
        if ratio < 0.7:
            per_score = 12
        elif ratio < 0.85:
            per_score = 10
        elif ratio < 1.0:
            per_score = 7
        elif ratio >= 1.3:
            per_score = 3
        else:
            per_score = 5
        details.append(
            f"PER {per:.1f} (섹터 평균 {sector_avg_per:.1f}, "
            f"비율 {ratio:.2f}, {per_score}/12)"
        )
    else:
        per_score = 5
        details.append(f"PER {per:.1f} (섹터 비교 불가, {per_score}/12)")

    score += per_score

    # PBR scoring (max 8)
    if hist_pbr_median > 0 and pbr > 0:
        pbr_ratio = pbr / hist_pbr_median
        if pbr_ratio < 0.7:
            pbr_score = 8
        elif pbr_ratio < 0.85:
            pbr_score = 6
        elif pbr_ratio < 1.0:
            pbr_score = 5
        elif pbr_ratio >= 1.3:
            pbr_score = 2
        else:
            pbr_score = 4
        details.append(
            f"PBR {pbr:.2f} (역사적 중간값 {hist_pbr_median:.2f}, "
            f"비율 {pbr_ratio:.2f}, {pbr_score}/8)"
        )
    elif pbr > 0:
        pbr_score = 4
        details.append(f"PBR {pbr:.2f} ({pbr_score}/8)")
    else:
        pbr_score = 2
        details.append(f"PBR {pbr:.2f} (비정상, {pbr_score}/8)")

    score += pbr_score

    # Absolute cheap bonus (max 5)
    if 0 < per < 8 and 0 < pbr < 0.7:
        cheap_bonus = 5
        details.append(f"절대 저평가 보너스 ({cheap_bonus}/5)")
    elif 0 < per < 10 and 0 < pbr < 1.0:
        cheap_bonus = 3
        details.append(f"상대 저평가 보너스 ({cheap_bonus}/5)")
    else:
        cheap_bonus = 0

    score += cheap_bonus

    score = min(25, score)

    return score, details


# ---------------------------------------------------------------------------
# Composite analysis
# ---------------------------------------------------------------------------


def analyze_financials(
    data: FinancialData,
    revenue_yoy: float = 0.0,
    op_income_yoy: float = 0.0,
    cagr_3y: float = 0.0,
    sector_avg_per: float = 20.0,
    interest_coverage: float = 5.0,
) -> FinancialScore:
    """Run full financial analysis and return composite score.

    The four sub-scores (growth, profitability, stability, valuation) each
    contribute up to 25 points for a total of 0-100.

    Score bonus for composite scoring:
        total >= 80: +15
        total >= 60: +10
        total >= 40: +5
        total < 30: -10

    Cap: +-20.

    Args:
        data: FinancialData for the ticker.
        revenue_yoy: Revenue year-over-year growth (%).
        op_income_yoy: Operating income year-over-year growth (%).
        cagr_3y: 3-year revenue CAGR (%).
        sector_avg_per: Sector average PER.
        interest_coverage: Interest coverage ratio.

    Returns:
        FinancialScore with sub-scores, total, details, and score bonus.
    """
    growth_score, growth_details = score_growth(revenue_yoy, op_income_yoy, cagr_3y)
    profit_score, profit_details = score_profitability(data.roe, data.op_margin, data.fcf)
    stab_score, stab_details = score_stability(
        data.debt_ratio, data.current_ratio, interest_coverage,
    )
    val_score, val_details = score_valuation(data.per, sector_avg_per, data.pbr)

    total = growth_score + profit_score + stab_score + val_score

    # Score bonus
    if total >= 80:
        bonus = 15
    elif total >= 60:
        bonus = 10
    elif total >= 40:
        bonus = 5
    elif total < 30:
        bonus = -10
    else:
        bonus = 0

    bonus = max(-20, min(20, bonus))

    details = {
        "growth": growth_details,
        "profitability": profit_details,
        "stability": stab_details,
        "valuation": val_details,
    }

    result = FinancialScore(
        growth=growth_score,
        profitability=profit_score,
        stability=stab_score,
        valuation=val_score,
        total=total,
        details=details,
        score_bonus=bonus,
    )

    logger.info(
        "Financial %s (%s): growth=%d profit=%d stab=%d val=%d total=%d bonus=%d",
        data.ticker, data.name,
        growth_score, profit_score, stab_score, val_score, total, bonus,
    )

    return result


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def _grade_label(score: int, max_score: int) -> str:
    """Map a sub-score to a grade label."""
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.8:
        return "우수"
    if ratio >= 0.6:
        return "양호"
    if ratio >= 0.4:
        return "보통"
    return "미흡"


def _grade_emoji(score: int, max_score: int) -> str:
    """Map a sub-score to an emoji indicator."""
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.8:
        return "\U0001f7e2"  # green
    if ratio >= 0.6:
        return "\U0001f7e1"  # yellow
    if ratio >= 0.4:
        return "\u26aa"      # white
    return "\U0001f534"      # red


def _total_grade(total: int) -> str:
    """Map total score to overall grade."""
    if total >= 80:
        return "A (우수)"
    if total >= 60:
        return "B (양호)"
    if total >= 40:
        return "C (보통)"
    if total >= 20:
        return "D (미흡)"
    return "F (위험)"


def format_financial_report(
    data: FinancialData,
    score: FinancialScore,
    revenue_yoy: float = 0.0,
    op_income_yoy: float = 0.0,
    cagr_3y: float = 0.0,
) -> str:
    """Format financial analysis report for Telegram /finance command.

    No ** bold markers. Report style without 주호님 greeting.

    Args:
        data: Raw financial data.
        score: Computed FinancialScore.
        revenue_yoy: Revenue YoY growth (%).
        op_income_yoy: Operating income YoY growth (%).
        cagr_3y: 3-year revenue CAGR (%).

    Returns:
        Multi-line formatted string.
    """
    lines: list[str] = []

    # Header
    lines.append(f"\U0001f4ca {data.name} ({data.ticker}) 재무 분석")
    lines.append(f"종합 점수: {score.total}/100 ({_total_grade(score.total)})")
    lines.append("")

    # Sub-scores bar
    dims = [
        ("성장성", score.growth, 25),
        ("수익성", score.profitability, 25),
        ("안정성", score.stability, 25),
        ("밸류에이션", score.valuation, 25),
    ]
    for label, sub_score, max_s in dims:
        emoji = _grade_emoji(sub_score, max_s)
        grade = _grade_label(sub_score, max_s)
        bar = _bar(sub_score, max_s)
        lines.append(f"{emoji} {label}: {sub_score}/{max_s} ({grade}) {bar}")

    lines.append("")

    # Key financials
    lines.append("주요 재무지표:")
    lines.append(f"  매출액: {data.revenue:,.0f}억원 (YoY {revenue_yoy:+.1f}%)")
    lines.append(f"  영업이익: {data.operating_income:,.0f}억원 (YoY {op_income_yoy:+.1f}%)")
    lines.append(f"  순이익: {data.net_income:,.0f}억원")
    lines.append(f"  영업이익률: {data.op_margin:.1f}%")
    lines.append(f"  ROE: {data.roe:.1f}%")
    lines.append(f"  부채비율: {data.debt_ratio:.0f}%")
    lines.append(f"  유동비율: {data.current_ratio:.0f}%")
    lines.append("")

    lines.append("밸류에이션:")
    lines.append(f"  PER: {data.per:.1f}")
    lines.append(f"  PBR: {data.pbr:.2f}")
    lines.append(f"  EPS: {data.eps:,.0f}원")
    lines.append(f"  BPS: {data.bps:,.0f}원")
    if data.dps > 0:
        lines.append(f"  DPS: {data.dps:,.0f}원")
    lines.append("")

    lines.append("현금흐름:")
    lines.append(f"  FCF: {data.fcf:,.0f}억원")
    lines.append(f"  EBITDA: {data.ebitda:,.0f}억원")
    if cagr_3y != 0.0:
        lines.append(f"  3년 매출 CAGR: {cagr_3y:+.1f}%")
    lines.append("")

    # Details breakdown
    for dim_key, dim_label in [
        ("growth", "성장성 상세"),
        ("profitability", "수익성 상세"),
        ("stability", "안정성 상세"),
        ("valuation", "밸류에이션 상세"),
    ]:
        dim_details = score.details.get(dim_key, [])
        if dim_details:
            lines.append(f"{dim_label}:")
            for d in dim_details:
                lines.append(f"  - {d}")
            lines.append("")

    # Score bonus
    if score.score_bonus > 0:
        lines.append(f"종합 스코어 보너스: +{score.score_bonus}점")
    elif score.score_bonus < 0:
        lines.append(f"종합 스코어 조정: {score.score_bonus}점")

    return "\n".join(lines)


def _bar(score: int, max_score: int, width: int = 10) -> str:
    """Create a simple text progress bar."""
    if max_score <= 0:
        return ""
    filled = int(score / max_score * width)
    filled = max(0, min(width, filled))
    return "\u2588" * filled + "\u2591" * (width - filled)
