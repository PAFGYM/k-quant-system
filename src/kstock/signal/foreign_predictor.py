"""Foreign flow direction predictor (Section 35 - 외인 방향 예측).

Predicts foreign investor inflow/outflow direction using five
independent signals: FX rate, US market, MSCI EM flows, futures
positioning, and DXY trend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ForeignPrediction:
    """Predicted foreign investor flow direction."""

    fx_signal: str  # "inflow" / "outflow"
    us_market_signal: str  # "inflow" / "outflow"
    msci_signal: str  # "inflow" / "outflow"
    futures_signal: str  # "inflow" / "outflow"
    dxy_signal: str  # "inflow" / "outflow"
    inflow_count: int  # 0 ~ 5
    outflow_count: int  # 0 ~ 5
    prediction: str  # "강한 유입", "유입 우세", "중립", "유출 우세", "강한 유출"
    score_adj: int  # +10, +5, 0, -5, -10


def predict_foreign_flow(
    usdkrw: float,
    usdkrw_20d_ma: float,
    spx_change_pct: float,
    msci_em_flow: str,
    foreign_futures_net_krw: float,
    dxy: float,
    dxy_change_pct: float,
) -> ForeignPrediction:
    """Predict foreign investor flow direction for Korean market.

    Uses five independent signals to predict whether foreign investors
    will be net buyers or sellers.

    Args:
        usdkrw: Current USD/KRW exchange rate.
        usdkrw_20d_ma: 20-day moving average of USD/KRW.
        spx_change_pct: S&P 500 daily change in percent.
        msci_em_flow: MSCI EM fund flow direction ("inflow" or "outflow").
        foreign_futures_net_krw: Foreign futures net buy amount in KRW.
        dxy: Current DXY (Dollar Index) level.
        dxy_change_pct: DXY daily change in percent.

    Returns:
        ForeignPrediction with signal breakdown and score adjustment.
    """
    inflow_count = 0
    outflow_count = 0

    # Signal 1: FX rate vs 20-day MA
    # 환율 < 20일 이평 -> 원화 강세 -> 외인 유입 우호
    if usdkrw < usdkrw_20d_ma:
        fx_signal = "inflow"
        inflow_count += 1
    else:
        fx_signal = "outflow"
        outflow_count += 1

    # Signal 2: US market direction
    # SPX 상승 -> 위험자산 선호 -> 신흥국 유입 우호
    if spx_change_pct > 0:
        us_signal = "inflow"
        inflow_count += 1
    else:
        us_signal = "outflow"
        outflow_count += 1

    # Signal 3: MSCI EM fund flow
    if msci_em_flow == "inflow":
        msci_signal = "inflow"
        inflow_count += 1
    else:
        msci_signal = "outflow"
        outflow_count += 1

    # Signal 4: Foreign futures positioning
    # 외인 선물 순매수 > 0 -> 상승 베팅 -> 현물 유입 가능
    if foreign_futures_net_krw > 0:
        futures_signal = "inflow"
        inflow_count += 1
    else:
        futures_signal = "outflow"
        outflow_count += 1

    # Signal 5: DXY (Dollar Index) trend
    # DXY 하락 -> 달러 약세 -> 신흥국 자금 유입 우호
    if dxy_change_pct < 0:
        dxy_signal = "inflow"
        inflow_count += 1
    else:
        dxy_signal = "outflow"
        outflow_count += 1

    # Determine prediction and score adjustment
    if inflow_count >= 4:
        prediction = "강한 유입"
        score_adj = 10
    elif inflow_count >= 3:
        prediction = "유입 우세"
        score_adj = 5
    elif outflow_count >= 4:
        prediction = "강한 유출"
        score_adj = -10
    elif outflow_count >= 3:
        prediction = "유출 우세"
        score_adj = -5
    else:
        prediction = "중립"
        score_adj = 0

    logger.info(
        "Foreign flow prediction: %s (inflow=%d, outflow=%d, adj=%+d)",
        prediction, inflow_count, outflow_count, score_adj,
    )

    return ForeignPrediction(
        fx_signal=fx_signal,
        us_market_signal=us_signal,
        msci_signal=msci_signal,
        futures_signal=futures_signal,
        dxy_signal=dxy_signal,
        inflow_count=inflow_count,
        outflow_count=outflow_count,
        prediction=prediction,
        score_adj=score_adj,
    )


def format_foreign_prediction(pred: ForeignPrediction) -> str:
    """Format foreign flow prediction for Telegram.

    Args:
        pred: Computed foreign flow prediction.

    Returns:
        Formatted multi-line string for Telegram message.
    """

    def _icon(signal: str) -> str:
        return "\u2705" if signal == "inflow" else "\u274c"

    lines = [
        "\U0001f30d 외인 방향 예측 (5-Signal Model)",
        f"  {_icon(pred.fx_signal)} 환율 신호: {pred.fx_signal}",
        f"  {_icon(pred.us_market_signal)} 미국 시장: {pred.us_market_signal}",
        f"  {_icon(pred.msci_signal)} MSCI EM 자금: {pred.msci_signal}",
        f"  {_icon(pred.futures_signal)} 외인 선물: {pred.futures_signal}",
        f"  {_icon(pred.dxy_signal)} DXY 방향: {pred.dxy_signal}",
        f"  \u2192 유입 {pred.inflow_count}개 / 유출 {pred.outflow_count}개",
        f"  \u2192 예측: {pred.prediction} ({pred.score_adj:+d}점)",
    ]

    return "\n".join(lines)
