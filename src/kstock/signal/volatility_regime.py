"""v9.0: 변동성 레짐 분류 모듈.

VIX(미국) + 한국 실현변동성을 결합하여 4단계 변동성 레짐을 판단.
VKOSPI 직접 수집이 어려우므로 KOSPI 실현변동성을 프록시로 사용.

레짐:
  저변동 → 공격적 포지션, 돌파 전략
  보통   → 기본 전략, 분할 매수
  고변동 → 포지션 축소, 넓은 손절
  극단   → 신규 매수 중단, 역발상 탐색
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

logger = logging.getLogger(__name__)

# 한국 실현변동성 캐시 (30분 TTL)
_kr_vol_cache: dict[str, tuple[float, datetime]] = {}
_KR_VOL_TTL = timedelta(minutes=30)


@dataclass
class VolatilityRegime:
    """변동성 레짐 판단 결과."""

    level: str          # "low", "normal", "high", "extreme"
    label: str          # "저변동", "보통", "고변동", "극단"
    emoji: str
    vix: float
    korean_vol: float   # 한국 실현변동성 (연환산 %)
    strategy: str       # 전략 가이드
    position_factor: float  # 포지션 사이징 계수 (1.0 = 기본)


def classify_volatility_regime(
    vix: float,
    korean_vol: float = 0.0,
) -> VolatilityRegime:
    """VIX + 한국 변동성으로 4단계 레짐 분류.

    Args:
        vix: 미국 VIX 지수.
        korean_vol: 한국 실현변동성 (연환산 %). 0이면 VIX만으로 판단.
    """
    # 한국 변동성이 없으면 VIX 기반으로 추정
    # VKOSPI는 보통 VIX × 1.2~1.5 수준
    if korean_vol <= 0:
        korean_vol = vix * 1.3

    # v12.3: risk_config VIX 임계값
    try:
        from kstock.core.risk_config import get_risk_thresholds
        _vt = get_risk_thresholds().vix
        _vix_panic, _vix_high, _vix_low = _vt.panic, _vt.normal_high, _vt.normal_low
    except Exception:
        _vix_panic, _vix_high, _vix_low = 35, 25, 18

    # 극단: VKOSPI > 35 + VIX > panic
    if korean_vol > 35 and vix > _vix_panic:
        return VolatilityRegime(
            level="extreme", label="극단", emoji="🔴",
            vix=vix, korean_vol=korean_vol,
            strategy="신규 매수 중단, 현금 70%+\n역발상 매수 기회 탐색 (극공포=매수)\n만기일 겹치면 완전 회피",
            position_factor=0.3,
        )

    # 고변동: VKOSPI 25-35 + VIX > normal_high
    if korean_vol > 25 or vix > _vix_high:
        return VolatilityRegime(
            level="high", label="고변동", emoji="🟠",
            vix=vix, korean_vol=korean_vol,
            strategy="포지션 50% 축소, 현금 비중 확대\n넓은 손절, 역추세 매매 주의\nETF 레버리지 금지",
            position_factor=0.5,
        )

    # 저변동: VKOSPI < 15 + VIX < normal_low
    if korean_vol < 15 and vix < _vix_low:
        return VolatilityRegime(
            level="low", label="저변동", emoji="🟢",
            vix=vix, korean_vol=korean_vol,
            strategy="공격적 포지션 가능\n돌파 전략 유효, 좁은 손절",
            position_factor=1.2,
        )

    # 보통
    return VolatilityRegime(
        level="normal", label="보통", emoji="🟡",
        vix=vix, korean_vol=korean_vol,
        strategy="기본 전략 운용, 분할 매수\n표준 손절/익절 기준",
        position_factor=1.0,
    )


def compute_korean_volatility(kospi_closes: list[float] | None = None) -> float:
    """KOSPI 종가 배열에서 한국 실현변동성(연환산 %) 계산.

    20일 EWMA 방식. 데이터가 없으면 yfinance에서 조회.
    """
    # 캐시 확인
    now = datetime.now()
    cached = _kr_vol_cache.get("korean_vol")
    if cached and (now - cached[1]) < _KR_VOL_TTL:
        return cached[0]

    if kospi_closes is None or len(kospi_closes) < 5:
        kospi_closes = _fetch_kospi_closes()

    if kospi_closes is None or len(kospi_closes) < 5:
        return 0.0

    try:
        arr = np.array(kospi_closes, dtype=float)
        returns = np.diff(arr) / arr[:-1]

        # EWMA 변동성 (span=20)
        if len(returns) >= 20:
            weights = np.exp(-np.arange(len(returns))[::-1] / 20)
            weights /= weights.sum()
            ewma_var = np.sum(weights * (returns - np.mean(returns)) ** 2)
            vol = float(np.sqrt(ewma_var) * np.sqrt(252) * 100)
        else:
            vol = float(np.std(returns) * np.sqrt(252) * 100)

        vol = round(vol, 2)
        _kr_vol_cache["korean_vol"] = (vol, now)
        return vol

    except Exception as e:
        logger.warning("Korean vol computation failed: %s", e)
        return 0.0


def _fetch_kospi_closes() -> list[float] | None:
    """yfinance에서 KOSPI 1개월 종가 조회."""
    try:
        import yfinance as yf
        data = yf.download("^KS11", period="1mo", progress=False)
        if data is not None and not data.empty:
            closes = data["Close"].dropna().tolist()
            # yfinance 반환값이 nested 구조일 수 있음
            if closes and hasattr(closes[0], '__iter__'):
                closes = [float(c) for c in data["Close"].values.flatten() if not np.isnan(c)]
            else:
                closes = [float(c) for c in closes]
            return closes
    except Exception as e:
        logger.warning("KOSPI fetch for vol failed: %s", e)
    return None


def format_volatility_regime(regime: VolatilityRegime) -> str:
    """변동성 레짐을 텔레그램 표시용 문자열로 포맷."""
    lines = [
        f"{regime.emoji} 변동성 레짐: {regime.label}",
        f"  VIX: {regime.vix:.1f} / 한국Vol: {regime.korean_vol:.1f}%",
        f"  포지션 계수: {regime.position_factor:.1f}x",
    ]
    for s in regime.strategy.split("\n"):
        lines.append(f"  → {s}")
    return "\n".join(lines)
