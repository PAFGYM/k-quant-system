"""Oil price analysis module for K-Quant system.

Tracks WTI/Brent crude oil prices, detects regime changes,
analyzes impact on Korean stock sectors (refining, chemicals,
airlines, shipping, utilities), and generates actionable signals.

Data source: yfinance (CL=F for WTI, BZ=F for Brent)

Usage:
    from kstock.signal.oil_analysis import (
        compute_oil_analysis,
        format_oil_report,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Korean sector impact mapping
# ---------------------------------------------------------------------------

# 유가 영향 받는 한국 종목/섹터 매핑
OIL_SENSITIVE_SECTORS: Dict[str, Dict] = {
    "정유": {
        "tickers": {
            "010950": "S-Oil",
            "096770": "SK이노베이션",
            "267250": "HD현대",
        },
        "direction": "positive",  # 유가 상승 = 정유 수혜 (마진 확대, 재고 평가이익)
        "sensitivity": 0.8,
        "description": "유가 상승 시 재고평가이익 + 정제마진 확대",
    },
    "화학": {
        "tickers": {
            "051910": "LG화학",
            "009830": "한화솔루션",
            "006120": "SK디스커버리",
        },
        "direction": "mixed",  # 나프타 원가 상승 vs 제품가 전가
        "sensitivity": 0.6,
        "description": "납사 원가 상승, 스프레드 축소 가능. 전가력이 관건",
    },
    "항공": {
        "tickers": {
            "003490": "대한항공",
            "020560": "아시아나항공",
            "272450": "진에어",
        },
        "direction": "negative",  # 유가 상승 = 연료비 부담
        "sensitivity": 0.9,
        "description": "항공유 원가 직접 영향, 유가 상승 시 실적 악화",
    },
    "해운": {
        "tickers": {
            "011200": "HMM",
            "028670": "팬오션",
        },
        "direction": "mixed",  # 연료비 부담 vs 운임 전가 가능
        "sensitivity": 0.5,
        "description": "벙커유 원가 영향, 운임 전가 가능하나 시차 존재",
    },
    "유틸리티": {
        "tickers": {
            "015760": "한국전력",
            "034020": "두산에너빌리티",
        },
        "direction": "negative",  # 발전 원가 상승
        "sensitivity": 0.7,
        "description": "LNG/유류 발전단가 상승, 전기요금 인상 압박",
    },
    "2차전지/EV": {
        "tickers": {
            "373220": "LG에너지솔루션",
            "006400": "삼성SDI",
        },
        "direction": "positive_indirect",  # 유가 상승 → EV 수요 촉진
        "sensitivity": 0.4,
        "description": "고유가 → 내연기관 운행비 상승 → EV 전환 가속",
    },
}

# OPEC+ 주요 이벤트 키워드
OPEC_KEYWORDS = [
    "opec", "감산", "증산", "석유수출국", "사우디", "원유",
    "oil cut", "oil output", "crude production",
    "호르무즈", "hormuz", "중동", "이란", "이라크",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OilPriceData:
    """Raw oil price data with technical indicators."""

    wti_price: float
    wti_prev: float
    wti_change_pct: float
    wti_ma5: float
    wti_ma20: float
    wti_ma60: float
    brent_price: float
    brent_prev: float
    brent_change_pct: float
    brent_wti_spread: float  # Brent - WTI
    wti_52w_high: float
    wti_52w_low: float
    wti_position_52w: float  # 0~1 (0=52주 저점, 1=52주 고점)
    wti_volatility_20d: float  # 20일 실현변동성 (연환산 %)


@dataclass
class OilRegime:
    """Oil market regime classification."""

    regime: str  # "bull", "bear", "neutral", "spike", "crash"
    description: str
    strength: float  # 0~1
    trend_duration_days: int  # 현 추세 지속일


@dataclass
class SectorImpact:
    """Impact on a Korean stock sector."""

    sector: str
    direction: str  # "수혜", "피해", "혼재"
    magnitude: str  # "강", "중", "약"
    description: str
    key_tickers: List[str]


@dataclass
class OilSignal:
    """Actionable oil-related signal."""

    signal_type: str  # regime_change, spike_alert, spread_divergence, sector_rotation
    description: str
    strength: float  # 0~1
    recommendation: str


@dataclass
class OilAnalysisReport:
    """Complete oil analysis report."""

    price_data: OilPriceData
    regime: OilRegime
    sector_impacts: List[SectorImpact]
    signals: List[OilSignal]
    geopolitical_risk: str  # "낮음", "보통", "높음"
    overall_assessment: str


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _compute_oil_price_data(
    wti_series: pd.Series,
    brent_series: pd.Series,
) -> OilPriceData:
    """Compute oil price data with technical indicators."""
    if wti_series.empty or len(wti_series) < 2:
        return OilPriceData(
            wti_price=0, wti_prev=0, wti_change_pct=0,
            wti_ma5=0, wti_ma20=0, wti_ma60=0,
            brent_price=0, brent_prev=0, brent_change_pct=0,
            brent_wti_spread=0,
            wti_52w_high=0, wti_52w_low=0, wti_position_52w=0.5,
            wti_volatility_20d=0,
        )

    wti = float(wti_series.iloc[-1])
    wti_prev = float(wti_series.iloc[-2])
    wti_chg = (wti - wti_prev) / wti_prev * 100 if wti_prev > 0 else 0

    # Moving averages
    ma5 = float(wti_series.iloc[-5:].mean()) if len(wti_series) >= 5 else wti
    ma20 = float(wti_series.iloc[-20:].mean()) if len(wti_series) >= 20 else wti
    ma60 = float(wti_series.iloc[-60:].mean()) if len(wti_series) >= 60 else wti

    # 52-week range
    lookback = min(len(wti_series), 252)
    recent = wti_series.iloc[-lookback:]
    high_52w = float(recent.max())
    low_52w = float(recent.min())
    pos_52w = (wti - low_52w) / (high_52w - low_52w) if high_52w > low_52w else 0.5

    # 20-day realized volatility (annualized)
    if len(wti_series) >= 21:
        log_returns = np.log(wti_series.iloc[-21:] / wti_series.iloc[-21:].shift(1)).dropna()
        vol_20d = float(log_returns.std() * np.sqrt(252) * 100)
    else:
        vol_20d = 0.0

    # Brent
    brent = float(brent_series.iloc[-1]) if not brent_series.empty else 0
    brent_prev = float(brent_series.iloc[-2]) if len(brent_series) >= 2 else brent
    brent_chg = (brent - brent_prev) / brent_prev * 100 if brent_prev > 0 else 0
    spread = brent - wti if brent > 0 else 0

    return OilPriceData(
        wti_price=round(wti, 2),
        wti_prev=round(wti_prev, 2),
        wti_change_pct=round(wti_chg, 2),
        wti_ma5=round(ma5, 2),
        wti_ma20=round(ma20, 2),
        wti_ma60=round(ma60, 2),
        brent_price=round(brent, 2),
        brent_prev=round(brent_prev, 2),
        brent_change_pct=round(brent_chg, 2),
        brent_wti_spread=round(spread, 2),
        wti_52w_high=round(high_52w, 2),
        wti_52w_low=round(low_52w, 2),
        wti_position_52w=round(pos_52w, 4),
        wti_volatility_20d=round(vol_20d, 2),
    )


def _classify_regime(price_data: OilPriceData, wti_series: pd.Series) -> OilRegime:
    """Classify oil market regime."""
    wti = price_data.wti_price
    ma5 = price_data.wti_ma5
    ma20 = price_data.wti_ma20
    ma60 = price_data.wti_ma60
    change = price_data.wti_change_pct
    vol = price_data.wti_volatility_20d

    # Spike/crash detection (일일 변동 +-5% 이상)
    if change >= 5.0:
        return OilRegime(
            regime="spike",
            description=f"유가 급등 ({change:+.1f}%) - 공급 차질 또는 지정학 리스크 가능성",
            strength=min(change / 10, 1.0),
            trend_duration_days=1,
        )
    if change <= -5.0:
        return OilRegime(
            regime="crash",
            description=f"유가 급락 ({change:+.1f}%) - 수요 우려 또는 증산 합의 가능성",
            strength=min(abs(change) / 10, 1.0),
            trend_duration_days=1,
        )

    # Trend duration (MA5 기준 연속 상승/하락일)
    trend_days = 0
    if len(wti_series) >= 5:
        for i in range(1, min(len(wti_series), 30)):
            if wti_series.iloc[-i] >= wti_series.iloc[-i-1] if wti >= ma5 else wti_series.iloc[-i] <= wti_series.iloc[-i-1]:
                trend_days += 1
            else:
                break

    # Bull: 가격 > MA20 > MA60 (정배열)
    if wti > ma20 > ma60 and wti > ma5:
        deviation = (wti - ma20) / ma20 * 100
        strength = min(deviation / 10, 1.0)
        return OilRegime(
            regime="bull",
            description=f"상승 추세 (MA20 대비 +{deviation:.1f}%, 정배열)",
            strength=strength,
            trend_duration_days=trend_days,
        )

    # Bear: 가격 < MA20 < MA60 (역배열)
    if wti < ma20 < ma60 and wti < ma5:
        deviation = (ma20 - wti) / ma20 * 100
        strength = min(deviation / 10, 1.0)
        return OilRegime(
            regime="bear",
            description=f"하락 추세 (MA20 대비 -{deviation:.1f}%, 역배열)",
            strength=strength,
            trend_duration_days=trend_days,
        )

    # Neutral
    deviation = (wti - ma20) / ma20 * 100 if ma20 > 0 else 0
    return OilRegime(
        regime="neutral",
        description=f"횡보 구간 (MA20 대비 {deviation:+.1f}%)",
        strength=0.3,
        trend_duration_days=trend_days,
    )


def _assess_sector_impacts(
    price_data: OilPriceData,
    regime: OilRegime,
) -> List[SectorImpact]:
    """Assess impact on Korean stock sectors based on oil regime."""
    impacts: List[SectorImpact] = []

    is_rising = regime.regime in ("bull", "spike")
    is_falling = regime.regime in ("bear", "crash")
    vol_high = price_data.wti_volatility_20d > 30

    for sector_name, info in OIL_SENSITIVE_SECTORS.items():
        direction_raw = info["direction"]
        sensitivity = info["sensitivity"]
        tickers = list(info["tickers"].values())

        # Direction assessment
        if direction_raw == "positive":
            if is_rising:
                direction = "수혜"
                mag = "강" if regime.strength > 0.6 else "중"
            elif is_falling:
                direction = "피해"
                mag = "강" if regime.strength > 0.6 else "중"
            else:
                direction = "중립"
                mag = "약"
        elif direction_raw == "negative":
            if is_rising:
                direction = "피해"
                mag = "강" if regime.strength > 0.6 else "중"
            elif is_falling:
                direction = "수혜"
                mag = "강" if regime.strength > 0.6 else "중"
            else:
                direction = "중립"
                mag = "약"
        elif direction_raw == "positive_indirect":
            if is_rising:
                direction = "간접 수혜"
                mag = "약"
            elif is_falling:
                direction = "간접 피해"
                mag = "약"
            else:
                direction = "중립"
                mag = "약"
        else:  # mixed
            direction = "혼재"
            mag = "중" if vol_high else "약"

        impacts.append(SectorImpact(
            sector=sector_name,
            direction=direction,
            magnitude=mag,
            description=info["description"],
            key_tickers=tickers,
        ))

    return impacts


def _detect_oil_signals(
    price_data: OilPriceData,
    regime: OilRegime,
    prev_regime: str = "neutral",
) -> List[OilSignal]:
    """Detect actionable oil-related signals."""
    signals: List[OilSignal] = []

    # 1. Regime change
    if regime.regime != prev_regime and prev_regime:
        signals.append(OilSignal(
            signal_type="regime_change",
            description=f"유가 레짐 전환: {prev_regime} -> {regime.regime}",
            strength=regime.strength,
            recommendation=_get_regime_change_recommendation(prev_regime, regime.regime),
        ))

    # 2. Spike/crash alert
    if regime.regime == "spike":
        signals.append(OilSignal(
            signal_type="spike_alert",
            description=f"유가 급등 경보 ({price_data.wti_change_pct:+.1f}%)",
            strength=min(abs(price_data.wti_change_pct) / 8, 1.0),
            recommendation="정유 수혜 검토, 항공/화학 리스크 점검. 지정학 이슈 확인 필요",
        ))
    elif regime.regime == "crash":
        signals.append(OilSignal(
            signal_type="crash_alert",
            description=f"유가 급락 경보 ({price_data.wti_change_pct:+.1f}%)",
            strength=min(abs(price_data.wti_change_pct) / 8, 1.0),
            recommendation="항공/소비재 수혜 검토, 정유/에너지 리스크 점검",
        ))

    # 3. Brent-WTI spread divergence
    spread = price_data.brent_wti_spread
    if abs(spread) > 8:
        signals.append(OilSignal(
            signal_type="spread_divergence",
            description=f"Brent-WTI 스프레드 확대 (${spread:.1f})",
            strength=min(abs(spread) / 15, 1.0),
            recommendation="지역별 공급 불균형 신호. 수출입 물류 비용 변동 주의",
        ))

    # 4. High volatility warning
    if price_data.wti_volatility_20d > 40:
        signals.append(OilSignal(
            signal_type="volatility_warning",
            description=f"유가 변동성 극단적 ({price_data.wti_volatility_20d:.0f}%)",
            strength=min(price_data.wti_volatility_20d / 60, 1.0),
            recommendation="에너지 관련 포지션 축소 검토. 급변동 리스크 관리 필수",
        ))

    # 5. MA crossover signals
    if (price_data.wti_price > price_data.wti_ma20 and
            price_data.wti_prev <= price_data.wti_ma20):
        signals.append(OilSignal(
            signal_type="ma_crossover",
            description="WTI가 20일 이평선 상향 돌파",
            strength=0.6,
            recommendation="단기 상승 전환 가능성. 정유주 관심, 항공주 모니터링",
        ))
    elif (price_data.wti_price < price_data.wti_ma20 and
          price_data.wti_prev >= price_data.wti_ma20):
        signals.append(OilSignal(
            signal_type="ma_crossover",
            description="WTI가 20일 이평선 하향 이탈",
            strength=0.6,
            recommendation="단기 하락 전환 가능성. 항공주 관심, 정유주 모니터링",
        ))

    # 6. 52-week extreme positions
    if price_data.wti_position_52w > 0.9:
        signals.append(OilSignal(
            signal_type="extreme_position",
            description=f"WTI 52주 고점 근접 ({price_data.wti_position_52w:.0%})",
            strength=0.7,
            recommendation="과열 구간. 에너지주 차익실현 검토. 소비재/항공 저점매수 기회 탐색",
        ))
    elif price_data.wti_position_52w < 0.1:
        signals.append(OilSignal(
            signal_type="extreme_position",
            description=f"WTI 52주 저점 근접 ({price_data.wti_position_52w:.0%})",
            strength=0.7,
            recommendation="바닥 구간. 정유주 저점매수 기회 탐색. 항공주 수혜 지속 확인",
        ))

    return signals


def _get_regime_change_recommendation(prev: str, current: str) -> str:
    """Get recommendation for regime change."""
    transitions = {
        ("neutral", "bull"): "유가 상승 추세 진입. 정유/에너지 비중 확대 검토. 항공/화학 리스크 점검",
        ("neutral", "bear"): "유가 하락 추세 진입. 항공/소비재 수혜 검토. 정유주 비중 축소 검토",
        ("bull", "neutral"): "유가 상승 모멘텀 둔화. 정유주 차익실현 검토",
        ("bear", "neutral"): "유가 하락 모멘텀 둔화. 정유주 바닥 탐색 기회",
        ("bull", "bear"): "유가 추세 반전(상승→하락). 포트폴리오 대전환 검토",
        ("bear", "bull"): "유가 추세 반전(하락→상승). 에너지 섹터 재진입 검토",
    }
    return transitions.get((prev, current), f"유가 레짐 전환({prev}->{current}). 섹터 영향 재점검 필요")


def _assess_geopolitical_risk(price_data: OilPriceData) -> str:
    """Assess geopolitical risk level from price behavior."""
    vol = price_data.wti_volatility_20d
    spread = abs(price_data.brent_wti_spread)
    daily_move = abs(price_data.wti_change_pct)

    risk_score = 0
    if vol > 40:
        risk_score += 2
    elif vol > 25:
        risk_score += 1
    if spread > 8:
        risk_score += 1
    if daily_move > 3:
        risk_score += 1

    if risk_score >= 3:
        return "높음"
    elif risk_score >= 1:
        return "보통"
    return "낮음"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_oil_analysis(
    wti_series: pd.Series,
    brent_series: pd.Series,
    prev_regime: str = "neutral",
) -> OilAnalysisReport:
    """Compute comprehensive oil analysis.

    Parameters
    ----------
    wti_series : pd.Series
        WTI crude close prices (at least 5 days, ideally 252+).
    brent_series : pd.Series
        Brent crude close prices.
    prev_regime : str
        Previous oil regime for change detection.

    Returns
    -------
    OilAnalysisReport
    """
    price_data = _compute_oil_price_data(wti_series, brent_series)
    regime = _classify_regime(price_data, wti_series)
    sector_impacts = _assess_sector_impacts(price_data, regime)
    signals = _detect_oil_signals(price_data, regime, prev_regime)
    geo_risk = _assess_geopolitical_risk(price_data)

    # Overall assessment
    if regime.regime in ("spike", "crash"):
        overall = f"유가 급변동 상태. 에너지 섹터 포지션 재점검 필수. 변동성: {price_data.wti_volatility_20d:.0f}%"
    elif regime.regime == "bull":
        overall = f"유가 상승 추세. 정유 수혜, 항공/화학 비용 부담 증가. WTI ${price_data.wti_price}"
    elif regime.regime == "bear":
        overall = f"유가 하락 추세. 항공/소비재 수혜, 정유 실적 악화 우려. WTI ${price_data.wti_price}"
    else:
        overall = f"유가 횡보 구간. 섹터별 차별화 제한적. WTI ${price_data.wti_price}"

    return OilAnalysisReport(
        price_data=price_data,
        regime=regime,
        sector_impacts=sector_impacts,
        signals=signals,
        geopolitical_risk=geo_risk,
        overall_assessment=overall,
    )


def fetch_oil_data() -> tuple[pd.Series, pd.Series]:
    """Fetch WTI and Brent oil price data via yfinance.

    Returns (wti_series, brent_series) — close prices.
    """
    import yfinance as yf

    data = yf.download(["CL=F", "BZ=F"], period="1y", progress=False)

    wti = pd.Series(dtype=float)
    brent = pd.Series(dtype=float)

    try:
        if "CL=F" in data.columns.get_level_values(0):
            wti = data["CL=F"]["Close"].dropna()
        elif "Close" in data.columns:
            wti = data["Close"].dropna()
    except Exception as e:
        logger.warning("WTI data extraction failed: %s", e)

    try:
        if "BZ=F" in data.columns.get_level_values(0):
            brent = data["BZ=F"]["Close"].dropna()
    except Exception as e:
        logger.warning("Brent data extraction failed: %s", e)

    return wti, brent


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_oil_report(report: OilAnalysisReport) -> str:
    """Format OilAnalysisReport as plain text for Telegram."""
    p = report.price_data
    r = report.regime
    lines: List[str] = []

    # Header
    regime_emoji = {
        "bull": "📈", "bear": "📉", "neutral": "➡️",
        "spike": "🔥", "crash": "💥",
    }
    lines.append(f"🛢 유가 분석 리포트 {regime_emoji.get(r.regime, '📊')}")
    lines.append("")

    # Price section
    lines.append("[가격 현황]")
    wti_arrow = "🔼" if p.wti_change_pct > 0 else "🔽" if p.wti_change_pct < 0 else "➡️"
    lines.append(f"WTI: ${p.wti_price:.2f} ({p.wti_change_pct:+.1f}%) {wti_arrow}")
    if p.brent_price > 0:
        brent_arrow = "🔼" if p.brent_change_pct > 0 else "🔽" if p.brent_change_pct < 0 else "➡️"
        lines.append(f"Brent: ${p.brent_price:.2f} ({p.brent_change_pct:+.1f}%) {brent_arrow}")
        lines.append(f"Brent-WTI 스프레드: ${p.brent_wti_spread:.2f}")
    lines.append("")

    # Technical
    lines.append("[기술적 지표]")
    # MA arrangement
    if p.wti_price > p.wti_ma20 > p.wti_ma60:
        arrangement = "정배열 (상승)"
    elif p.wti_price < p.wti_ma20 < p.wti_ma60:
        arrangement = "역배열 (하락)"
    else:
        arrangement = "혼조"
    lines.append(f"이평선: MA5 ${p.wti_ma5:.1f} / MA20 ${p.wti_ma20:.1f} / MA60 ${p.wti_ma60:.1f} ({arrangement})")
    lines.append(f"52주 범위: ${p.wti_52w_low:.1f}~${p.wti_52w_high:.1f} (현위치 {p.wti_position_52w:.0%})")
    vol_label = "안정" if p.wti_volatility_20d < 20 else "주의" if p.wti_volatility_20d < 35 else "위험"
    lines.append(f"변동성(20일): {p.wti_volatility_20d:.1f}% ({vol_label})")
    lines.append("")

    # Regime
    lines.append("[레짐 판단]")
    lines.append(f"{regime_emoji.get(r.regime, '📊')} {r.description}")
    if r.trend_duration_days > 1:
        lines.append(f"추세 지속: {r.trend_duration_days}일")
    lines.append("")

    # Sector impacts
    lines.append("[한국 섹터 영향]")
    dir_emoji = {"수혜": "🟢", "피해": "🔴", "혼재": "🟡", "중립": "⚪", "간접 수혜": "🔵", "간접 피해": "🟠"}
    for si in report.sector_impacts:
        emoji = dir_emoji.get(si.direction, "⚪")
        lines.append(f"{emoji} {si.sector} [{si.direction}/{si.magnitude}]")
        lines.append(f"   {si.description}")
    lines.append("")

    # Signals
    if report.signals:
        lines.append("[시그널]")
        sig_emoji = {
            "regime_change": "🔄", "spike_alert": "🔥", "crash_alert": "💥",
            "spread_divergence": "↔️", "volatility_warning": "⚠️",
            "ma_crossover": "✂️", "extreme_position": "📍",
        }
        for s in sorted(report.signals, key=lambda x: -x.strength):
            em = sig_emoji.get(s.signal_type, "📌")
            lines.append(f"{em} [{s.strength:.0%}] {s.description}")
            lines.append(f"   -> {s.recommendation}")
        lines.append("")

    # Geopolitical risk
    geo_emoji = {"낮음": "🟢", "보통": "🟡", "높음": "🔴"}
    lines.append(f"지정학 리스크: {geo_emoji.get(report.geopolitical_risk, '⚪')} {report.geopolitical_risk}")
    lines.append("")

    # Overall
    lines.append(f"[종합] {report.overall_assessment}")

    return "\n".join(lines)


def format_oil_summary_line(report: OilAnalysisReport) -> str:
    """One-line oil summary for compact display."""
    p = report.price_data
    r = report.regime
    regime_kr = {"bull": "상승", "bear": "하락", "neutral": "횡보", "spike": "급등", "crash": "급락"}
    return (
        f"WTI ${p.wti_price:.1f} ({p.wti_change_pct:+.1f}%) "
        f"[{regime_kr.get(r.regime, '?')}] "
        f"변동성 {p.wti_volatility_20d:.0f}%"
    )


def format_oil_context_for_ai(report: OilAnalysisReport) -> str:
    """Format oil analysis for AI system prompt context."""
    p = report.price_data
    r = report.regime

    lines = [
        f"유가(WTI): ${p.wti_price:.2f} ({p.wti_change_pct:+.1f}%)",
        f"유가(Brent): ${p.brent_price:.2f} ({p.brent_change_pct:+.1f}%)",
        f"유가 레짐: {r.regime} - {r.description}",
        f"유가 변동성(20일): {p.wti_volatility_20d:.1f}%",
        f"52주 위치: {p.wti_position_52w:.0%} (${p.wti_52w_low:.1f}~${p.wti_52w_high:.1f})",
    ]

    # Sector impacts for AI
    for si in report.sector_impacts:
        if si.direction not in ("중립",):
            lines.append(f"유가→{si.sector}: {si.direction}({si.magnitude}) - {si.description}")

    # Active signals
    for s in report.signals:
        lines.append(f"유가 시그널: {s.description} -> {s.recommendation}")

    if report.geopolitical_risk != "낮음":
        lines.append(f"지정학 리스크: {report.geopolitical_risk}")

    return "\n".join(lines)
