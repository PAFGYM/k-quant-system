"""Evidence-based price target calculation engine.

v9.4: 기술적/기본적/AI합의 3중 근거 가격 목표 시스템.

1. 기술적 목표: 지지/저항선, 피보나치
2. 기본적 목표: PER/PBR 밴드 기반 적정가
3. 합의 목표: AI 토론 결과 통합
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PriceTarget:
    """3중 근거 가격 목표."""
    current_price: float = 0
    # 기술적
    support_1: float = 0       # 1차 지지선
    support_2: float = 0       # 2차 지지선
    resistance_1: float = 0   # 1차 저항선
    resistance_2: float = 0   # 2차 저항선
    fib_levels: dict = field(default_factory=dict)  # 피보나치 레벨
    sma_20: float = 0
    sma_60: float = 0
    sma_120: float = 0
    # 기본적
    fair_value_per: float = 0  # PER 기반 적정가
    fair_value_pbr: float = 0  # PBR 기반 적정가
    sector_avg_per: float = 0
    # 합의
    ai_target: float = 0      # AI 토론 합의 목표가
    ai_stop: float = 0        # AI 토론 합의 손절가
    # 종합
    upside_pct: float = 0     # 상승 여력 %
    downside_pct: float = 0   # 하락 위험 %
    risk_reward_ratio: float = 0
    confidence: str = "보통"   # 높음/보통/낮음


class PriceTargetEngine:
    """3중 근거 가격 목표 산출 엔진."""

    def calculate_targets(
        self,
        ohlcv: pd.DataFrame | None = None,
        current_price: float = 0,
        stock_info: dict | None = None,
        debate_target: float = 0,
        debate_stop: float = 0,
    ) -> PriceTarget:
        """가격 목표 종합 산출.

        Args:
            ohlcv: OHLCV DataFrame (close, high, low 필수)
            current_price: 현재가
            stock_info: 종목 기본 정보 (per, pbr, eps 등)
            debate_target: AI 토론 합의 목표가
            debate_stop: AI 토론 합의 손절가
        """
        pt = PriceTarget(current_price=current_price)

        # 1. 기술적 분석
        if ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
            self._calc_technical(pt, ohlcv)

        # 2. 기본적 분석
        if stock_info:
            self._calc_fundamental(pt, stock_info)

        # 3. AI 합의
        if debate_target > 0:
            pt.ai_target = debate_target
        if debate_stop > 0:
            pt.ai_stop = debate_stop

        # 4. 종합 계산
        self._calc_summary(pt)

        return pt

    def _calc_technical(self, pt: PriceTarget, ohlcv: pd.DataFrame) -> None:
        """기술적 지지/저항, 피보나치, 이동평균."""
        closes = ohlcv["close"].values.astype(float)
        n = len(closes)

        if n < 5:
            return

        # 이동평균
        if n >= 20:
            pt.sma_20 = round(float(np.mean(closes[-20:])), 0)
        if n >= 60:
            pt.sma_60 = round(float(np.mean(closes[-60:])), 0)
        if n >= 120:
            pt.sma_120 = round(float(np.mean(closes[-120:])), 0)

        # 지지/저항선 (피벗 포인트 기반)
        highs = ohlcv["high"].values.astype(float) if "high" in ohlcv.columns else closes
        lows = ohlcv["low"].values.astype(float) if "low" in ohlcv.columns else closes

        supports, resistances = self._find_support_resistance(closes, highs, lows)

        current = pt.current_price or closes[-1]

        # 지지선: 현재가 아래의 가장 가까운 레벨
        below = sorted([s for s in supports if s < current], reverse=True)
        if len(below) >= 1:
            pt.support_1 = round(below[0], 0)
        if len(below) >= 2:
            pt.support_2 = round(below[1], 0)

        # 저항선: 현재가 위의 가장 가까운 레벨
        above = sorted([r for r in resistances if r > current])
        if len(above) >= 1:
            pt.resistance_1 = round(above[0], 0)
        if len(above) >= 2:
            pt.resistance_2 = round(above[1], 0)

        # 피보나치 되돌림
        if n >= 20:
            recent_high = float(np.max(highs[-60:] if n >= 60 else highs))
            recent_low = float(np.min(lows[-60:] if n >= 60 else lows))
            pt.fib_levels = self._fibonacci_levels(recent_high, recent_low)

    def _find_support_resistance(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        window: int = 10,
    ) -> tuple[list[float], list[float]]:
        """지지/저항선 탐색.

        로컬 최저/최고점 기반 + 이동평균.
        """
        n = len(closes)
        supports = []
        resistances = []

        # 로컬 극값 탐색
        for i in range(window, n - window):
            # 로컬 최저 (지지)
            if lows[i] == np.min(lows[i - window : i + window + 1]):
                supports.append(float(lows[i]))
            # 로컬 최고 (저항)
            if highs[i] == np.max(highs[i - window : i + window + 1]):
                resistances.append(float(highs[i]))

        # 이동평균도 지지/저항으로 추가
        for period in (20, 60, 120):
            if n >= period:
                sma = float(np.mean(closes[-period:]))
                if sma < closes[-1]:
                    supports.append(sma)
                else:
                    resistances.append(sma)

        # 클러스터링: 가까운 레벨 병합 (2% 이내)
        supports = self._cluster_levels(supports)
        resistances = self._cluster_levels(resistances)

        return supports, resistances

    def _cluster_levels(self, levels: list[float], threshold: float = 0.02) -> list[float]:
        """가까운 가격 레벨 병합."""
        if not levels:
            return []

        levels = sorted(levels)
        clustered = [levels[0]]

        for level in levels[1:]:
            if abs(level - clustered[-1]) / clustered[-1] < threshold:
                # 평균으로 병합
                clustered[-1] = (clustered[-1] + level) / 2
            else:
                clustered.append(level)

        return clustered

    def _fibonacci_levels(self, high: float, low: float) -> dict:
        """피보나치 되돌림/확장 레벨."""
        diff = high - low
        if diff <= 0:
            return {}

        return {
            "0.0% (저점)": round(low, 0),
            "23.6%": round(low + diff * 0.236, 0),
            "38.2%": round(low + diff * 0.382, 0),
            "50.0%": round(low + diff * 0.500, 0),
            "61.8%": round(low + diff * 0.618, 0),
            "78.6%": round(low + diff * 0.786, 0),
            "100.0% (고점)": round(high, 0),
            "127.2% (확장)": round(low + diff * 1.272, 0),
            "161.8% (확장)": round(low + diff * 1.618, 0),
        }

    def _calc_fundamental(self, pt: PriceTarget, info: dict) -> None:
        """PER/PBR 밴드 기반 적정가."""
        per = info.get("per", 0) or info.get("PER", 0)
        pbr = info.get("pbr", 0) or info.get("PBR", 0)
        eps = info.get("eps", 0) or info.get("EPS", 0)
        bps = info.get("bps", 0) or info.get("BPS", 0)
        current = pt.current_price or info.get("current_price", 0)

        # EPS 기반 적정 PER 밴드 (한국시장 평균 PER ~10~14)
        if eps and eps > 0:
            # 업종 평균 PER이 있으면 사용, 없으면 KOSPI 평균 12배
            sector_per = info.get("sector_per", 0)
            if sector_per and sector_per > 0:
                pt.sector_avg_per = sector_per
                pt.fair_value_per = round(eps * sector_per, 0)
            else:
                pt.fair_value_per = round(eps * 12, 0)  # KOSPI 평균

        # BPS 기반 적정 PBR 밴드 (한국시장 평균 PBR ~0.9~1.2)
        if bps and bps > 0:
            pt.fair_value_pbr = round(bps * 1.0, 0)  # PBR 1.0 기준

    def _calc_summary(self, pt: PriceTarget) -> None:
        """종합 상승여력/위험보상비 계산."""
        current = pt.current_price
        if current <= 0:
            return

        # 목표가 결정 (우선순위: AI합의 > 저항선 > PER적정가)
        target = pt.ai_target or pt.resistance_1 or pt.fair_value_per or 0
        # 손절가 결정 (우선순위: AI합의 > 지지선)
        stop = pt.ai_stop or pt.support_1 or 0

        if target > 0:
            pt.upside_pct = round((target - current) / current * 100, 1)
        if stop > 0 and stop < current:
            pt.downside_pct = round((current - stop) / current * 100, 1)

        if pt.downside_pct > 0 and pt.upside_pct > 0:
            pt.risk_reward_ratio = round(pt.upside_pct / pt.downside_pct, 2)

        # 확신도
        if pt.risk_reward_ratio >= 3:
            pt.confidence = "높음"
        elif pt.risk_reward_ratio >= 1.5:
            pt.confidence = "보통"
        else:
            pt.confidence = "낮음"


# ── 포맷팅 ───────────────────────────────────────────────────

def format_price_target(pt: PriceTarget) -> str:
    """PriceTarget → 텍스트."""
    if pt.current_price <= 0:
        return ""

    lines = [
        f"📈 가격 목표 분석 (현재가: {pt.current_price:,.0f}원)",
        "",
    ]

    # 기술적
    tech_lines = []
    if pt.resistance_1:
        tech_lines.append(f"  1차 저항: {pt.resistance_1:,.0f}원")
    if pt.resistance_2:
        tech_lines.append(f"  2차 저항: {pt.resistance_2:,.0f}원")
    if pt.support_1:
        tech_lines.append(f"  1차 지지: {pt.support_1:,.0f}원")
    if pt.support_2:
        tech_lines.append(f"  2차 지지: {pt.support_2:,.0f}원")
    if tech_lines:
        lines.append("기술적 레벨:")
        lines.extend(tech_lines)
        lines.append("")

    # 이동평균
    sma_lines = []
    if pt.sma_20:
        pos = "위" if pt.current_price > pt.sma_20 else "아래"
        sma_lines.append(f"  20일선: {pt.sma_20:,.0f}원 ({pos})")
    if pt.sma_60:
        pos = "위" if pt.current_price > pt.sma_60 else "아래"
        sma_lines.append(f"  60일선: {pt.sma_60:,.0f}원 ({pos})")
    if sma_lines:
        lines.append("이동평균:")
        lines.extend(sma_lines)
        lines.append("")

    # 기본적
    fund_lines = []
    if pt.fair_value_per:
        diff_pct = (pt.fair_value_per - pt.current_price) / pt.current_price * 100
        label = "저평가" if diff_pct > 10 else ("고평가" if diff_pct < -10 else "적정")
        fund_lines.append(f"  PER 적정가: {pt.fair_value_per:,.0f}원 ({diff_pct:+.1f}%, {label})")
    if pt.fair_value_pbr:
        diff_pct = (pt.fair_value_pbr - pt.current_price) / pt.current_price * 100
        fund_lines.append(f"  PBR 적정가: {pt.fair_value_pbr:,.0f}원 ({diff_pct:+.1f}%)")
    if fund_lines:
        lines.append("밸류에이션:")
        lines.extend(fund_lines)
        lines.append("")

    # 종합
    if pt.upside_pct != 0 or pt.downside_pct != 0:
        lines.append("종합:")
        if pt.upside_pct:
            lines.append(f"  상승 여력: {pt.upside_pct:+.1f}%")
        if pt.downside_pct:
            lines.append(f"  하락 위험: -{pt.downside_pct:.1f}%")
        if pt.risk_reward_ratio > 0:
            lines.append(f"  위험보상비: {pt.risk_reward_ratio:.1f}배 ({pt.confidence})")

    return "\n".join(lines)


def format_price_target_for_debate(pt: PriceTarget) -> str:
    """AI 토론용 가격 목표 요약."""
    if pt.current_price <= 0:
        return ""

    parts = [f"현재가 {pt.current_price:,.0f}원"]
    if pt.support_1:
        parts.append(f"1차지지 {pt.support_1:,.0f}원")
    if pt.resistance_1:
        parts.append(f"1차저항 {pt.resistance_1:,.0f}원")
    if pt.fair_value_per:
        parts.append(f"PER적정가 {pt.fair_value_per:,.0f}원")
    if pt.sma_20:
        parts.append(f"20일선 {pt.sma_20:,.0f}원")

    return " / ".join(parts)
