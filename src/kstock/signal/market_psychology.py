"""Market psychology indicators (Section 33 - 시장 심리 지표).

Computes Fear & Greed index for the Korean market using VIX,
KOSPI 20-day return, volume ratio, and foreign net buy streaks.
Also detects retail contrarian signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FearGreedIndex:
    """Composite Fear & Greed index for the Korean market."""

    vix_score: float  # 0 ~ 25
    kospi_20d_score: float  # 0 ~ 25
    volume_score: float  # 0 ~ 25
    foreign_score: float  # 0 ~ 25
    total: float  # 0 ~ 100
    label: str  # "극단공포" / "공포" / "중립" / "탐욕" / "극단탐욕"


def compute_fear_greed(
    vix: float,
    kospi_20d_return_pct: float,
    volume_ratio: float,
    foreign_net_days: int,
) -> FearGreedIndex:
    """Compute Fear & Greed index from market data.

    Args:
        vix: Current VIX level.
        kospi_20d_return_pct: KOSPI 20-day return in percent.
        volume_ratio: Current volume / 20-day average volume.
        foreign_net_days: Consecutive foreign net buy days (negative = sell).

    Returns:
        FearGreedIndex with component scores and composite label.
    """
    # VIX score (low VIX = greed)
    if vix < 15:
        vix_score = 25.0
    elif vix < 20:
        vix_score = 18.0
    elif vix < 25:
        vix_score = 12.0
    elif vix < 30:
        vix_score = 6.0
    else:
        vix_score = 0.0

    # KOSPI 20-day return score (positive return = greed)
    if kospi_20d_return_pct > 5:
        kospi_score = 25.0
    elif kospi_20d_return_pct > 2:
        kospi_score = 18.0
    elif kospi_20d_return_pct > 0:
        kospi_score = 12.0
    elif kospi_20d_return_pct > -2:
        kospi_score = 6.0
    else:
        kospi_score = 0.0

    # Volume ratio score (high volume = greed)
    if volume_ratio > 1.5:
        vol_score = 25.0
    elif volume_ratio > 1.2:
        vol_score = 18.0
    elif volume_ratio > 0.8:
        vol_score = 12.0
    elif volume_ratio > 0.5:
        vol_score = 6.0
    else:
        vol_score = 0.0

    # Foreign net buy streak score (sustained buying = greed)
    if foreign_net_days >= 5:
        foreign_score = 25.0
    elif foreign_net_days >= 3:
        foreign_score = 18.0
    elif foreign_net_days >= 1:
        foreign_score = 12.0
    elif foreign_net_days >= -3:
        foreign_score = 6.0
    else:
        foreign_score = 0.0

    total = vix_score + kospi_score + vol_score + foreign_score

    # Label assignment
    if total < 20:
        label = "극단공포"
    elif total < 40:
        label = "공포"
    elif total < 60:
        label = "중립"
    elif total < 80:
        label = "탐욕"
    else:
        label = "극단탐욕"

    logger.debug(
        "Fear/Greed: VIX=%.1f(%.0f) KOSPI20d=%.1f%%(%.0f) "
        "Vol=%.2f(%.0f) Foreign=%dd(%.0f) => %.0f %s",
        vix, vix_score, kospi_20d_return_pct, kospi_score,
        volume_ratio, vol_score, foreign_net_days, foreign_score,
        total, label,
    )

    return FearGreedIndex(
        vix_score=vix_score,
        kospi_20d_score=kospi_score,
        volume_score=vol_score,
        foreign_score=foreign_score,
        total=total,
        label=label,
    )


def detect_retail_contrarian(
    retail_net_buy_krw: float,
    foreign_net_buy_krw: float,
) -> dict:
    """Detect retail contrarian signal.

    When retail investors pile in while foreigners sell (or vice versa),
    this often marks a turning point.

    Args:
        retail_net_buy_krw: Retail (individual) net buy amount in KRW.
        foreign_net_buy_krw: Foreign net buy amount in KRW.

    Returns:
        dict with "signal" (str) and "score_adj" (int), or empty dict
        if no contrarian signal detected.
    """
    # 개인 순매수 급증 + 외인 순매도 -> 고점 경고
    if retail_net_buy_krw > 0 and foreign_net_buy_krw < 0:
        logger.info(
            "Retail contrarian: 개인 순매수 %.0f억 + 외인 순매도 %.0f억 -> 고점 경고",
            retail_net_buy_krw / 1e8,
            foreign_net_buy_krw / 1e8,
        )
        return {"signal": "고점 경고", "score_adj": -5}

    # 개인 순매도 급증 + 외인 순매수 -> 저점 시그널
    if retail_net_buy_krw < 0 and foreign_net_buy_krw > 0:
        logger.info(
            "Retail contrarian: 개인 순매도 %.0f억 + 외인 순매수 %.0f억 -> 저점 시그널",
            retail_net_buy_krw / 1e8,
            foreign_net_buy_krw / 1e8,
        )
        return {"signal": "저점 시그널", "score_adj": 5}

    return {}


def get_psychology_score_adj(fear_greed: FearGreedIndex) -> int:
    """Get score adjustment based on market psychology (contrarian).

    Extreme fear is a buying opportunity; extreme greed is a warning.

    Args:
        fear_greed: Computed Fear & Greed index.

    Returns:
        Score adjustment: +10/+5/0/-5/-10.
    """
    if fear_greed.label == "극단공포":
        return 10
    if fear_greed.label == "공포":
        return 5
    if fear_greed.label == "탐욕":
        return -5
    if fear_greed.label == "극단탐욕":
        return -10
    return 0


def format_psychology_summary(
    fear_greed: FearGreedIndex,
    retail_signal: dict | None = None,
) -> str:
    """Format market psychology summary for Telegram.

    Args:
        fear_greed: Computed Fear & Greed index.
        retail_signal: Optional retail contrarian signal dict.

    Returns:
        Formatted multi-line string for Telegram message.
    """
    score_adj = get_psychology_score_adj(fear_greed)

    lines = [
        "\U0001f9e0 시장 심리 지표 (Fear & Greed)",
        f"  VIX 점수: {fear_greed.vix_score:.0f}/25",
        f"  KOSPI 20일 점수: {fear_greed.kospi_20d_score:.0f}/25",
        f"  거래량 점수: {fear_greed.volume_score:.0f}/25",
        f"  외인 수급 점수: {fear_greed.foreign_score:.0f}/25",
        f"  종합: {fear_greed.total:.0f}/100 ({fear_greed.label})",
        f"  점수 조정: {score_adj:+d}점 (역발상)",
    ]

    if retail_signal:
        lines.append(
            f"\n\u26a0\ufe0f 개인/외인 역행 시그널: {retail_signal['signal']}"
            f" ({retail_signal['score_adj']:+d}점)"
        )

    return "\n".join(lines)
