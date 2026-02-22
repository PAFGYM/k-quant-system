"""FX (USD/KRW) strategy module.

Based on academic research showing ML-based KR-US portfolio
with FX hedging achieves Sharpe 3.48.

Core logic: compare current USD/KRW to 20-day moving average
to determine currency regime and adjust domestic/global weights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FXSignal:
    """FX regime signal."""

    usdkrw_current: float
    usdkrw_ma20: float
    deviation_pct: float  # % above/below MA20
    regime: str  # "krw_weak", "krw_strong", "neutral"
    domestic_weight_adj: float  # -0.1 ~ +0.1 multiplier
    global_weight_adj: float  # -0.1 ~ +0.1 multiplier
    message: str


def compute_fx_signal(
    usdkrw_history: pd.Series | None = None,
    usdkrw_current: float = 0.0,
) -> FXSignal:
    """Compute FX regime signal based on USD/KRW 20-day MA.

    Args:
        usdkrw_history: Recent USD/KRW price series (at least 20 values).
        usdkrw_current: Current USD/KRW rate (used if history unavailable).
    """
    if usdkrw_history is not None and len(usdkrw_history) >= 20:
        ma20 = float(usdkrw_history.iloc[-20:].mean())
        current = float(usdkrw_history.iloc[-1])
    elif usdkrw_current > 0:
        # Fallback: use current value with approximate MA
        ma20 = usdkrw_current * 0.995  # assume slight trend
        current = usdkrw_current
    else:
        return FXSignal(
            usdkrw_current=0, usdkrw_ma20=0, deviation_pct=0,
            regime="neutral", domestic_weight_adj=0, global_weight_adj=0,
            message="환율 데이터 없음",
        )

    deviation = (current - ma20) / ma20 * 100

    if deviation > 1.0:
        # KRW weakening -> favor global/USD assets
        regime = "krw_weak"
        dom_adj = -0.05
        glob_adj = 0.10
        msg = (
            f"\U0001f4b1 환율 {current:,.0f}원 "
            f"(20일 평균 {ma20:,.0f}원 대비 {deviation:+.1f}%)\n"
            f"\u2192 원화 약세 구간, 해외 ETF 비중 확대 권장"
        )
    elif deviation < -1.0:
        # KRW strengthening -> favor domestic assets
        regime = "krw_strong"
        dom_adj = 0.10
        glob_adj = -0.05
        msg = (
            f"\U0001f4b1 환율 {current:,.0f}원 "
            f"(20일 평균 {ma20:,.0f}원 대비 {deviation:+.1f}%)\n"
            f"\u2192 원화 강세 구간, 국내 종목 비중 확대 권장"
        )
    else:
        regime = "neutral"
        dom_adj = 0.0
        glob_adj = 0.0
        msg = (
            f"\U0001f4b1 환율 {current:,.0f}원 "
            f"(20일 평균 {ma20:,.0f}원 대비 {deviation:+.1f}%)\n"
            f"\u2192 환율 중립 구간"
        )

    return FXSignal(
        usdkrw_current=round(current, 0),
        usdkrw_ma20=round(ma20, 0),
        deviation_pct=round(deviation, 2),
        regime=regime,
        domestic_weight_adj=dom_adj,
        global_weight_adj=glob_adj,
        message=msg,
    )
