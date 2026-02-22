"""Tenbagger candidate scanner (Section 42 - 텐배거 후보 발굴).

Systemizes the pattern that led to the 에코프로 success
(bought at 90,700 -> 170,900, +88%) by screening for deeply
discounted stocks with policy tailwinds, revenue growth,
foreign accumulation, and volume reversal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Tenbagger theme sectors eligible for +2 bonus
TENBAGGER_THEMES: set[str] = {"AI", "로봇", "우주", "2차전지", "바이오"}

# Five core conditions — need 4+ to qualify as a tenbagger candidate
TENBAGGER_CONDITIONS: list[dict] = [
    {
        "key": "price_vs_52w_high",
        "label": "고점 대비 50%+ 하락",
        "check": "drop_from_high_pct <= -50",
    },
    {
        "key": "sector_policy_support",
        "label": "정책 수혜 섹터",
        "check": "policy_support is True",
    },
    {
        "key": "revenue_growth",
        "label": "매출 성장 중 (10%+)",
        "check": "revenue_growth_pct > 10",
    },
    {
        "key": "foreign_accumulation",
        "label": "외인 조용한 매집 (10일 중 7일+ 순매수)",
        "check": "foreign_buy_days_in_10 >= 7",
    },
    {
        "key": "volume_bottom_reversal",
        "label": "거래량 바닥 반전 (20일 평균 대비 150%+)",
        "check": "volume_ratio_20d >= 1.5",
    },
]


@dataclass
class TenbaggerCandidate:
    """A stock that passes tenbagger screening criteria."""

    ticker: str
    name: str
    current_price: float
    high_52w: float
    drop_from_high_pct: float
    conditions_met: int
    conditions_total: int
    conditions_detail: list[str] = field(default_factory=list)
    ml_prob: float = 0.5
    sentiment_pct: float = 50.0
    score_bonus: int = 0
    message: str = ""


def _evaluate_conditions(
    drop_from_high_pct: float,
    policy_support: bool,
    revenue_growth_pct: float,
    foreign_buy_days_in_10: int,
    volume_ratio_20d: float,
) -> tuple[int, list[str]]:
    """Evaluate the five tenbagger conditions.

    Returns:
        Tuple of (conditions_met, list of detail strings).
    """
    met = 0
    details: list[str] = []

    # 1. 고점 대비 50%+ 하락
    if drop_from_high_pct <= -50:
        met += 1
        details.append(f"고점 대비 {drop_from_high_pct:+.1f}% 하락 (충족)")
    else:
        details.append(f"고점 대비 {drop_from_high_pct:+.1f}% 하락 (미충족, 기준 -50%)")

    # 2. 정책 수혜 섹터
    if policy_support:
        met += 1
        details.append("정책 수혜 섹터 (충족)")
    else:
        details.append("정책 수혜 섹터 (미충족)")

    # 3. 매출 성장 > 10%
    if revenue_growth_pct > 10:
        met += 1
        details.append(f"매출 성장 {revenue_growth_pct:+.1f}% (충족)")
    else:
        details.append(f"매출 성장 {revenue_growth_pct:+.1f}% (미충족, 기준 10%+)")

    # 4. 외인 조용한 매집: 10일 중 7일+ 순매수
    if foreign_buy_days_in_10 >= 7:
        met += 1
        details.append(f"외인 매집 {foreign_buy_days_in_10}/10일 순매수 (충족)")
    else:
        details.append(f"외인 매집 {foreign_buy_days_in_10}/10일 순매수 (미충족, 기준 7일+)")

    # 5. 거래량 바닥 반전: 20일 평균 대비 150%+
    if volume_ratio_20d >= 1.5:
        met += 1
        details.append(f"거래량 반전 {volume_ratio_20d:.1f}x (충족)")
    else:
        details.append(f"거래량 반전 {volume_ratio_20d:.1f}x (미충족, 기준 1.5x+)")

    return met, details


def _compute_bonus(
    conditions_met: int,
    market_cap: float,
    market: str,
    sector: str,
) -> int:
    """Compute score bonus from conditions met and additional criteria.

    Bonus rules:
        - 5/5 conditions -> +20 base
        - 4/5 conditions -> +15 base
        - 시총 1조~10조 -> +2 (적정 규모)
        - 코스닥 -> +1 (변동성 유리)
        - 테마: AI/로봇/우주/2차전지/바이오 -> +2
    """
    # Base bonus from conditions
    if conditions_met >= 5:
        bonus = 20
    elif conditions_met >= 4:
        bonus = 15
    else:
        bonus = 0

    # 시총 1조~10조 (적정 규모)
    if 1_000_000_000_000 <= market_cap <= 10_000_000_000_000:
        bonus += 2

    # 코스닥 (변동성 유리)
    if market.upper() in ("KOSDAQ", "코스닥"):
        bonus += 1

    # 테마 섹터 보너스
    if sector in TENBAGGER_THEMES:
        bonus += 2

    return bonus


def scan_tenbagger(
    ticker: str,
    name: str,
    current_price: float,
    high_52w: float,
    market_cap: float,
    market: str,
    sector: str,
    revenue_growth_pct: float,
    foreign_buy_days_in_10: int,
    volume_ratio_20d: float,
    policy_support: bool,
    ml_prob: float = 0.5,
    sentiment_pct: float = 50.0,
) -> TenbaggerCandidate | None:
    """Scan a stock for tenbagger potential.

    Requires 4 or more of the 5 core conditions to be met.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        current_price: Current stock price (KRW).
        high_52w: 52-week high price (KRW).
        market_cap: Market capitalization (KRW).
        market: Market name (KOSPI / KOSDAQ).
        sector: Sector name.
        revenue_growth_pct: Year-over-year revenue growth (%).
        foreign_buy_days_in_10: Days with foreign net buying in last 10 days.
        volume_ratio_20d: Current volume / 20-day average volume ratio.
        policy_support: Whether the sector has active policy support.
        ml_prob: ML model buy probability (0.0~1.0).
        sentiment_pct: News sentiment positive percentage (0~100).

    Returns:
        TenbaggerCandidate if 4+ conditions met, None otherwise.
    """
    if high_52w <= 0:
        logger.warning("Invalid 52w high for %s (%s): %.0f", ticker, name, high_52w)
        return None

    drop_from_high_pct = ((current_price - high_52w) / high_52w) * 100

    conditions_met, conditions_detail = _evaluate_conditions(
        drop_from_high_pct=drop_from_high_pct,
        policy_support=policy_support,
        revenue_growth_pct=revenue_growth_pct,
        foreign_buy_days_in_10=foreign_buy_days_in_10,
        volume_ratio_20d=volume_ratio_20d,
    )

    if conditions_met < 4:
        logger.debug(
            "Tenbagger skip %s (%s): %d/5 conditions met",
            ticker, name, conditions_met,
        )
        return None

    score_bonus = _compute_bonus(conditions_met, market_cap, market, sector)

    # Build summary message
    if conditions_met == 5:
        message = f"{name} 텐배거 후보! 5/5 조건 완벽 충족. 강력 주목!"
    else:
        message = f"{name} 텐배거 후보. {conditions_met}/5 조건 충족. 관찰 요망."

    logger.info(
        "Tenbagger candidate found: %s (%s) %d/5, bonus=%d",
        ticker, name, conditions_met, score_bonus,
    )

    return TenbaggerCandidate(
        ticker=ticker,
        name=name,
        current_price=current_price,
        high_52w=high_52w,
        drop_from_high_pct=round(drop_from_high_pct, 1),
        conditions_met=conditions_met,
        conditions_total=5,
        conditions_detail=conditions_detail,
        ml_prob=ml_prob,
        sentiment_pct=sentiment_pct,
        score_bonus=score_bonus,
        message=message,
    )


def format_tenbagger_alert(candidate: TenbaggerCandidate) -> str:
    """Format a tenbagger candidate alert for Telegram.

    Args:
        candidate: Tenbagger candidate to format.

    Returns:
        Formatted multi-line string without ** bold markers.
    """
    lines = [
        "텐배거 후보 발견!",
        f"종목: {candidate.name} ({candidate.ticker})",
        f"현재가: {candidate.current_price:,.0f}원  "
        f"52주 고점 대비 {candidate.drop_from_high_pct:+.0f}%",
        "",
        f"충족 조건 {candidate.conditions_met}/{candidate.conditions_total}:",
    ]

    for detail in candidate.conditions_detail:
        lines.append(f"  - {detail}")

    lines.append("")

    if candidate.ml_prob > 0:
        lines.append(f"ML 매수 확률: {candidate.ml_prob * 100:.0f}%")
    if candidate.sentiment_pct > 0:
        lines.append(f"뉴스 감성: 긍정 {candidate.sentiment_pct:.0f}%")
    if candidate.score_bonus > 0:
        lines.append(f"스코어 보너스: +{candidate.score_bonus}점")

    lines.append("")

    if candidate.conditions_met >= 5:
        lines.append("주호님, 이거 강하게 주목하세요!")
    else:
        lines.append("주호님, 관심 종목으로 추적하세요.")

    return "\n".join(lines)
