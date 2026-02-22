"""Long-term investment scoring system (separate 100-point scale).

Categories:
- Dividend yield (20 points): 3%+ full score
- PBR band position (20 points): bottom = full score
- ROE stability (20 points): 3yr consecutive 10%+ full score
- Debt ratio (15 points): 100% or below full score
- FCF (15 points): 3yr consecutive positive full score
- Industry outlook (10 points): growth sector bonus
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Growth sectors get bonus points
GROWTH_SECTORS = {
    "반도체", "2차전지", "바이오", "IT", "방산", "조선",
    "핀테크", "AI", "신재생", "로봇", "우주",
}


@dataclass
class LongTermScore:
    """Long-term investment score breakdown."""

    dividend: float  # 0~20
    pbr: float  # 0~20
    roe: float  # 0~20
    debt: float  # 0~15
    fcf: float  # 0~15
    sector: float  # 0~10
    total: float  # 0~100
    grade: str  # A+, A, B+, B, C, D
    monthly_recommendation: str  # e.g., "매월 10만원 적립식 매수 추천"


def compute_long_term_score(
    dividend_yield: float = 0.0,
    pbr: float = 0.0,
    roe: float = 0.0,
    debt_ratio: float = 0.0,
    sector: str = "",
    is_etf: bool = False,
) -> LongTermScore:
    """Compute long-term investment score.

    Args:
        dividend_yield: Annual dividend yield (%).
        pbr: Price-to-book ratio.
        roe: Return on equity (%).
        debt_ratio: Debt-to-equity ratio (%).
        sector: Sector name for outlook bonus.
        is_etf: Whether this is an ETF (relaxed criteria).
    """
    # 1. Dividend yield (max 20)
    if dividend_yield >= 4.0:
        s_div = 20.0
    elif dividend_yield >= 3.0:
        s_div = 16.0
    elif dividend_yield >= 2.0:
        s_div = 12.0
    elif dividend_yield >= 1.0:
        s_div = 8.0
    else:
        s_div = 4.0 if dividend_yield > 0 else 0.0

    # 2. PBR band position (max 20)
    if pbr <= 0:
        s_pbr = 10.0  # unknown
    elif pbr <= 0.5:
        s_pbr = 20.0
    elif pbr <= 1.0:
        s_pbr = 16.0
    elif pbr <= 1.5:
        s_pbr = 12.0
    elif pbr <= 2.0:
        s_pbr = 8.0
    else:
        s_pbr = 4.0

    # 3. ROE stability (max 20) — using single-year as proxy
    if roe >= 15:
        s_roe = 20.0
    elif roe >= 10:
        s_roe = 16.0
    elif roe >= 7:
        s_roe = 12.0
    elif roe >= 4:
        s_roe = 8.0
    else:
        s_roe = 4.0

    # 4. Debt ratio (max 15)
    if debt_ratio <= 0:
        s_debt = 10.0  # unknown
    elif debt_ratio <= 50:
        s_debt = 15.0
    elif debt_ratio <= 100:
        s_debt = 12.0
    elif debt_ratio <= 150:
        s_debt = 8.0
    elif debt_ratio <= 200:
        s_debt = 5.0
    else:
        s_debt = 2.0

    # 5. FCF proxy (max 15) — using ROE + low debt as proxy
    if roe >= 10 and debt_ratio <= 100:
        s_fcf = 15.0
    elif roe >= 7 and debt_ratio <= 150:
        s_fcf = 10.0
    elif roe >= 5:
        s_fcf = 7.0
    else:
        s_fcf = 3.0

    # 6. Industry outlook (max 10)
    if sector in GROWTH_SECTORS:
        s_sector = 10.0
    elif sector:
        s_sector = 6.0
    else:
        s_sector = 5.0

    # ETF adjustments
    if is_etf:
        # ETFs have different characteristics
        s_debt = 12.0  # debt not applicable to ETFs
        s_fcf = 10.0  # FCF not applicable

    total = s_div + s_pbr + s_roe + s_debt + s_fcf + s_sector
    total = round(min(100.0, total), 1)

    # Grade
    if total >= 85:
        grade = "A+"
    elif total >= 75:
        grade = "A"
    elif total >= 65:
        grade = "B+"
    elif total >= 55:
        grade = "B"
    elif total >= 45:
        grade = "C"
    else:
        grade = "D"

    # Monthly recommendation
    if total >= 75:
        monthly = "매월 10만원씩 적립식 매수 추천"
    elif total >= 60:
        monthly = "매월 5만원씩 소액 적립 추천"
    elif total >= 45:
        monthly = "관심 종목으로 모니터링 추천"
    else:
        monthly = "장기 투자 부적합"

    return LongTermScore(
        dividend=s_div,
        pbr=s_pbr,
        roe=s_roe,
        debt=s_debt,
        fcf=s_fcf,
        sector=s_sector,
        total=total,
        grade=grade,
        monthly_recommendation=monthly,
    )
