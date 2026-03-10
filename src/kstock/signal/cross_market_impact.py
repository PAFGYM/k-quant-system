"""Cross-market impact analysis: US/global markets → Korean market prediction.

Fetches overnight US futures, VIX, USD/KRW, oil, treasury yields, and Asian
market data to produce a composite impact score and sector-level predictions
for the next Korean trading session.

Data source: yfinance

Usage:
    from kstock.signal.cross_market_impact import (
        fetch_cross_market_data,
        compute_cross_market_impact,
        format_impact_report,
        format_impact_context_for_ai,
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
# yfinance ticker mapping
# ---------------------------------------------------------------------------

CROSS_MARKET_TICKERS: Dict[str, str] = {
    # US equity futures
    "sp500": "^GSPC",
    "nasdaq": "^NDX",
    "sp500_fut": "ES=F",
    "nasdaq_fut": "NQ=F",
    # Volatility
    "vix": "^VIX",
    # FX
    "usdkrw": "USDKRW=X",
    "dxy": "DX-Y.NYB",
    "usdjpy": "USDJPY=X",
    # Commodities
    "wti": "CL=F",
    "gold": "GC=F",
    "copper": "HG=F",
    # Bonds
    "us10y": "^TNX",
    # Asian markets
    "nikkei": "^N225",
    "shanghai": "000001.SS",
    "hsi": "^HSI",
    "taiwan": "^TWII",
    # Korean indices
    "kospi": "^KS11",
    "kosdaq": "^KQ11",
    # v11.0: Korea-linked US ETF (overnight predictor)
    "koru": "KORU",  # Direxion 3x South Korea Bull — overnight return predicts next-day KOSPI
}

# ---------------------------------------------------------------------------
# Sector sensitivity mapping (US → Korea)
# ---------------------------------------------------------------------------

# Beta coefficients: how much a 1% US market move impacts each Korean sector
SECTOR_BETA: Dict[str, Dict[str, float]] = {
    "반도체": {
        "nasdaq": 0.85,  # NASDAQ와 높은 상관
        "usdjpy": -0.3,  # 엔화 약세 → 일본 경쟁사 유리 → 한국 불리
        "dxy": -0.2,
        "taiwan": 0.4,  # v10.5: 한국-대만 반도체 상관
    },
    "자동차": {
        "sp500": 0.5,
        "usdkrw": -0.4,  # 원화 약세 → 수출 수혜
        "wti": -0.2,
    },
    "2차전지": {
        "nasdaq": 0.7,
        "copper": 0.3,  # 구리 가격 = EV 수요 바로미터
        "wti": 0.2,  # 고유가 → EV 전환 촉진
    },
    "바이오": {
        "nasdaq": 0.6,
        "vix": -0.3,  # 공포 증가 → 바이오 약세
    },
    "금융": {
        "us10y": 0.4,  # 금리 상승 → 순이자마진 확대
        "vix": -0.2,
    },
    "철강": {
        "copper": 0.4,  # 원자재 동반
        "shanghai": 0.3,  # 중국 수요 대리
    },
    "조선": {
        "wti": 0.3,  # 고유가 → 해양 플랜트 수주
        "usdkrw": -0.3,
    },
    "IT": {
        "nasdaq": 0.7,
        "vix": -0.25,
    },
    "에너지": {
        "wti": 0.8,
    },
    "방산": {
        "vix": 0.2,  # 지정학 불안 → 방산 수혜
        "gold": 0.15,
    },
}

# Historical asymmetry: negative US moves transmit stronger
_NEG_AMPLIFIER = 1.4  # negative beta multiplier
_POS_AMPLIFIER = 0.8

# VIX regime thresholds
VIX_THRESHOLDS = {
    "low": 15,
    "normal": 20,
    "elevated": 25,
    "stress": 30,
    "crisis": 40,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CrossMarketSnapshot:
    """Latest cross-market data."""

    date: str
    # US markets
    sp500_close: float = 0.0
    sp500_change_pct: float = 0.0
    nasdaq_close: float = 0.0
    nasdaq_change_pct: float = 0.0
    # VIX
    vix: float = 20.0
    vix_change_pct: float = 0.0
    vix_ma20: float = 20.0
    vix_regime: str = "normal"  # low/normal/elevated/stress/crisis
    # FX
    usdkrw: float = 1300.0
    usdkrw_change_pct: float = 0.0
    dxy: float = 100.0
    dxy_change_pct: float = 0.0
    # Bonds
    us10y_yield: float = 4.0
    us10y_change_bp: float = 0.0  # basis points
    # Commodities
    wti: float = 70.0
    wti_change_pct: float = 0.0
    gold: float = 2000.0
    gold_change_pct: float = 0.0
    copper_change_pct: float = 0.0
    # FX extended
    usdjpy: float = 150.0
    usdjpy_change_pct: float = 0.0
    # Asian markets
    nikkei_change_pct: float = 0.0
    shanghai_change_pct: float = 0.0
    hsi_change_pct: float = 0.0
    taiwan_change_pct: float = 0.0  # v10.5: TWII
    # Options PCR (v10.5)
    pcr_volume: float = 0.0
    # v11.0: KORU (Direxion 3x Korea Bull) — overnight predictor
    koru_close: float = 0.0
    koru_change_pct: float = 0.0  # KORU daily return (3x leveraged)
    koru_implied_kospi_pct: float = 0.0  # KORU/3 ≈ implied KOSPI return
    # Korean (previous close)
    kospi_prev: float = 0.0
    kosdaq_prev: float = 0.0


@dataclass
class ImpactScore:
    """Composite impact prediction for Korean market."""

    # Overall
    composite_score: float = 0.0  # -10 ~ +10 (negative = bearish)
    confidence: float = 0.0  # 0 ~ 1
    direction: str = "neutral"  # strong_bearish/bearish/neutral/bullish/strong_bullish
    expected_kospi_gap_pct: float = 0.0  # 예상 KOSPI 시가 갭 (%)

    # Component scores
    us_equity_impact: float = 0.0
    vix_impact: float = 0.0
    fx_impact: float = 0.0
    bond_impact: float = 0.0
    commodity_impact: float = 0.0
    asia_spillover: float = 0.0

    # Sector predictions
    sector_impacts: Dict[str, float] = field(default_factory=dict)

    # Risk flags
    risk_flags: List[str] = field(default_factory=list)


@dataclass
class MarketOutlook:
    """Forward-looking market outlook."""

    date: str
    impact_score: ImpactScore
    snapshot: CrossMarketSnapshot

    # Outlook text
    headline: str = ""
    key_drivers: List[str] = field(default_factory=list)
    sector_rotation: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_cross_market_data(period: str = "5d") -> Dict[str, pd.Series]:
    """Fetch all cross-market price data via yfinance.

    Returns dict of {key: close_price_series}.
    """
    import yfinance as yf

    tickers = list(CROSS_MARKET_TICKERS.values())
    result: Dict[str, pd.Series] = {}

    try:
        data = yf.download(tickers, period=period, progress=False, threads=True)
    except Exception as e:
        logger.error("Cross-market yfinance download failed: %s", e)
        return result

    for key, symbol in CROSS_MARKET_TICKERS.items():
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.get_level_values(0):
                    series = data[symbol]["Close"].dropna()
                elif "Close" in data.columns.get_level_values(0):
                    if symbol in data["Close"].columns:
                        series = data["Close"][symbol].dropna()
                    else:
                        continue
                else:
                    continue
            else:
                if "Close" in data.columns:
                    series = data["Close"].dropna()
                else:
                    continue

            if not series.empty:
                result[key] = series
        except Exception:
            logger.debug("Failed to extract %s (%s)", key, symbol, exc_info=True)

    logger.info("Cross-market data: fetched %d/%d series", len(result), len(CROSS_MARKET_TICKERS))
    return result


def _safe_pct_change(series: pd.Series) -> float:
    """Last close vs previous close percentage change."""
    if series is None or len(series) < 2:
        return 0.0
    last = float(series.iloc[-1])
    prev = float(series.iloc[-2])
    if prev == 0:
        return 0.0
    return (last - prev) / prev * 100


def _safe_last(series: pd.Series, default: float = 0.0) -> float:
    if series is None or series.empty:
        return default
    return float(series.iloc[-1])


def build_snapshot(
    data: Dict[str, pd.Series],
    date_str: str = "",
) -> CrossMarketSnapshot:
    """Build CrossMarketSnapshot from fetched data."""
    from datetime import date as _date

    if not date_str:
        date_str = _date.today().isoformat()

    snap = CrossMarketSnapshot(date=date_str)

    # US equity
    snap.sp500_close = _safe_last(data.get("sp500"))
    snap.sp500_change_pct = _safe_pct_change(data.get("sp500"))
    snap.nasdaq_close = _safe_last(data.get("nasdaq"))
    snap.nasdaq_change_pct = _safe_pct_change(data.get("nasdaq"))

    # VIX
    vix_series = data.get("vix")
    snap.vix = _safe_last(vix_series, 20.0)
    snap.vix_change_pct = _safe_pct_change(vix_series)
    if vix_series is not None and len(vix_series) >= 5:
        snap.vix_ma20 = float(vix_series.iloc[-5:].mean())
    else:
        snap.vix_ma20 = snap.vix

    # VIX regime
    if snap.vix >= VIX_THRESHOLDS["crisis"]:
        snap.vix_regime = "crisis"
    elif snap.vix >= VIX_THRESHOLDS["stress"]:
        snap.vix_regime = "stress"
    elif snap.vix >= VIX_THRESHOLDS["elevated"]:
        snap.vix_regime = "elevated"
    elif snap.vix >= VIX_THRESHOLDS["normal"]:
        snap.vix_regime = "normal"
    else:
        snap.vix_regime = "low"

    # FX
    snap.usdkrw = _safe_last(data.get("usdkrw"), 1300.0)
    snap.usdkrw_change_pct = _safe_pct_change(data.get("usdkrw"))
    snap.dxy = _safe_last(data.get("dxy"), 100.0)
    snap.dxy_change_pct = _safe_pct_change(data.get("dxy"))

    # Bonds
    us10y = data.get("us10y")
    if us10y is not None and not us10y.empty:
        snap.us10y_yield = float(us10y.iloc[-1]) / 10  # TNX is yield * 10
        if len(us10y) >= 2:
            snap.us10y_change_bp = (float(us10y.iloc[-1]) - float(us10y.iloc[-2])) * 10  # bp

    # Commodities
    snap.wti = _safe_last(data.get("wti"), 70.0)
    snap.wti_change_pct = _safe_pct_change(data.get("wti"))
    snap.gold = _safe_last(data.get("gold"), 2000.0)
    snap.gold_change_pct = _safe_pct_change(data.get("gold"))
    snap.copper_change_pct = _safe_pct_change(data.get("copper"))

    # FX extended (v10.5)
    snap.usdjpy = _safe_last(data.get("usdjpy"), 150.0)
    snap.usdjpy_change_pct = _safe_pct_change(data.get("usdjpy"))

    # Asian markets
    snap.nikkei_change_pct = _safe_pct_change(data.get("nikkei"))
    snap.shanghai_change_pct = _safe_pct_change(data.get("shanghai"))
    snap.hsi_change_pct = _safe_pct_change(data.get("hsi"))
    snap.taiwan_change_pct = _safe_pct_change(data.get("taiwan"))  # v10.5

    # v11.0: KORU (overnight predictor)
    snap.koru_close = _safe_last(data.get("koru"))
    snap.koru_change_pct = _safe_pct_change(data.get("koru"))
    # KORU is 3x leveraged → implied KOSPI return = KORU_return / 3
    snap.koru_implied_kospi_pct = snap.koru_change_pct / 3.0 if snap.koru_change_pct else 0.0

    # Korean (previous close)
    snap.kospi_prev = _safe_last(data.get("kospi"))
    snap.kosdaq_prev = _safe_last(data.get("kosdaq"))

    return snap


# ---------------------------------------------------------------------------
# Impact computation
# ---------------------------------------------------------------------------

def _compute_us_equity_impact(snap: CrossMarketSnapshot) -> float:
    """US equity overnight → KOSPI impact score (-5 ~ +5)."""
    # Weighted average of S&P and NASDAQ (tech-heavy Korea = more NASDAQ weight)
    us_change = snap.sp500_change_pct * 0.4 + snap.nasdaq_change_pct * 0.6

    # Asymmetric transmission
    if us_change < 0:
        beta = 0.7 * _NEG_AMPLIFIER  # Stronger negative transmission
    else:
        beta = 0.5 * _POS_AMPLIFIER  # Weaker positive transmission

    # VIX regime amplifier
    if snap.vix_regime in ("stress", "crisis"):
        beta *= 1.3  # Higher correlation in stress

    return max(-5, min(5, us_change * beta))


def _compute_vix_impact(snap: CrossMarketSnapshot) -> float:
    """VIX level and change → KOSPI impact score (-3 ~ +1)."""
    score = 0.0

    # VIX spike velocity (change rate matters more than absolute level)
    if snap.vix_change_pct > 15:
        score -= 2.0
    elif snap.vix_change_pct > 8:
        score -= 1.0
    elif snap.vix_change_pct < -8:
        score += 0.5

    # VIX absolute level
    if snap.vix >= 40:
        score -= 1.5
    elif snap.vix >= 30:
        score -= 0.8
    elif snap.vix >= 25:
        score -= 0.3
    elif snap.vix < 15:
        score += 0.3

    return max(-3, min(1, score))


def _compute_fx_impact(snap: CrossMarketSnapshot) -> float:
    """USD/KRW and DXY → KOSPI impact score (-3 ~ +3)."""
    score = 0.0

    # USD/KRW: 원화 약세(상승) = 외인 매도 = KOSPI 하락
    krw_change = snap.usdkrw_change_pct
    if krw_change > 0.5:  # KRW weakening
        score -= min(krw_change * 1.2, 2.5)
    elif krw_change < -0.3:  # KRW strengthening
        score += min(abs(krw_change) * 0.8, 1.5)

    # DXY: 달러 강세 = EM 약세 = KOSPI 하락
    if snap.dxy_change_pct > 0.5:
        score -= 0.5

    return max(-3, min(3, score))


def _compute_bond_impact(snap: CrossMarketSnapshot) -> float:
    """US Treasury yield → KOSPI impact score (-2 ~ +1)."""
    bp_change = snap.us10y_change_bp

    # Sharp yield rise (>10bp) = risk-off for growth stocks
    if bp_change > 10:
        return -1.0
    elif bp_change > 5:
        return -0.3
    elif bp_change < -10:
        return 0.5  # Yield drop = risk-on
    return 0.0


def _compute_commodity_impact(snap: CrossMarketSnapshot) -> float:
    """Oil, gold, copper → KOSPI impact score (-2 ~ +2)."""
    score = 0.0

    # Oil: 급등 = 한국 수입 부담 (순수입국)
    if snap.wti_change_pct > 5:
        score -= 1.5
    elif snap.wti_change_pct > 3:
        score -= 0.5
    elif snap.wti_change_pct < -5:
        score += 0.5  # 유가 하락 = 수입 부담 감소

    # Copper: 경기 선행 지표
    if snap.copper_change_pct > 2:
        score += 0.3
    elif snap.copper_change_pct < -2:
        score -= 0.3

    # Gold: 안전자산 수요 proxy
    if snap.gold_change_pct > 2 and snap.vix > 25:
        score -= 0.3  # risk-off 확인

    return max(-2, min(2, score))


def _compute_asia_spillover(snap: CrossMarketSnapshot) -> float:
    """Asian market performance → KOSPI impact (-2 ~ +2)."""
    # Nikkei is strongest same-timezone predictor
    score = 0.0
    if abs(snap.nikkei_change_pct) > 0.5:
        score += snap.nikkei_change_pct * 0.3

    # Shanghai/HSI: China demand proxy
    china_avg = (snap.shanghai_change_pct + snap.hsi_change_pct) / 2
    if abs(china_avg) > 0.5:
        score += china_avg * 0.2

    # v10.5: Taiwan TWII — 반도체 동조
    if abs(snap.taiwan_change_pct) > 0.5:
        score += snap.taiwan_change_pct * 0.15

    return max(-2, min(2, score))


def _compute_sector_impacts(snap: CrossMarketSnapshot) -> Dict[str, float]:
    """Predict per-sector impact based on cross-market data."""
    changes = {
        "nasdaq": snap.nasdaq_change_pct,
        "sp500": snap.sp500_change_pct,
        "vix": snap.vix_change_pct,
        "usdkrw": snap.usdkrw_change_pct,
        "dxy": snap.dxy_change_pct,
        "us10y": snap.us10y_change_bp / 10,  # normalize to %
        "wti": snap.wti_change_pct,
        "gold": snap.gold_change_pct,
        "copper": snap.copper_change_pct,
        "usdjpy": snap.usdjpy_change_pct,  # v10.5: activated
        "shanghai": snap.shanghai_change_pct,
        "taiwan": snap.taiwan_change_pct,  # v10.5: TWII
    }

    sector_scores: Dict[str, float] = {}
    for sector, betas in SECTOR_BETA.items():
        score = 0.0
        for factor, beta in betas.items():
            change = changes.get(factor, 0.0)
            # Asymmetric amplification for negative moves
            if change < 0 and beta > 0:
                score += change * beta * _NEG_AMPLIFIER
            elif change > 0 and beta < 0:
                score += change * beta * _NEG_AMPLIFIER
            else:
                score += change * beta * _POS_AMPLIFIER
        sector_scores[sector] = round(score, 2)

    return sector_scores


def _detect_risk_flags(snap: CrossMarketSnapshot) -> List[str]:
    """Detect risk flags from cross-market data."""
    flags = []

    if snap.vix >= 30:
        flags.append(f"VIX 공포 수준 ({snap.vix:.1f})")
    if snap.vix_change_pct > 20:
        flags.append(f"VIX 급등 ({snap.vix_change_pct:+.1f}%)")
    if snap.usdkrw_change_pct > 1.0:
        flags.append(f"원화 급락 ({snap.usdkrw_change_pct:+.1f}%)")
    if abs(snap.sp500_change_pct) > 3:
        flags.append(f"S&P500 급변동 ({snap.sp500_change_pct:+.1f}%)")
    if abs(snap.wti_change_pct) > 5:
        flags.append(f"유가 급변동 ({snap.wti_change_pct:+.1f}%)")
    if snap.us10y_change_bp > 15:
        flags.append(f"미국채 10Y 급등 (+{snap.us10y_change_bp:.0f}bp)")
    # v11.0: KORU signal
    if abs(snap.koru_change_pct) > 5:
        direction = "급등" if snap.koru_change_pct > 0 else "급락"
        flags.append(f"KORU 3X {direction} ({snap.koru_change_pct:+.1f}%, 내재 KOSPI {snap.koru_implied_kospi_pct:+.1f}%)")

    return flags


def compute_cross_market_impact(
    snap: CrossMarketSnapshot,
) -> ImpactScore:
    """Compute composite cross-market impact score.

    Returns ImpactScore with direction, magnitude, and sector breakdown.
    """
    us_eq = _compute_us_equity_impact(snap)
    vix_imp = _compute_vix_impact(snap)
    fx_imp = _compute_fx_impact(snap)
    bond_imp = _compute_bond_impact(snap)
    commodity_imp = _compute_commodity_impact(snap)
    asia_imp = _compute_asia_spillover(snap)

    # v11.0: KORU overnight signal — direct Korea predictor
    koru_signal = 0.0
    if abs(snap.koru_implied_kospi_pct) > 0.1:
        # KORU implied return is a direct bet on Korean market
        koru_signal = max(-3, min(3, snap.koru_implied_kospi_pct * 0.8))

    # Weighted composite (v11.0: KORU added at 10%, others adjusted)
    if abs(koru_signal) > 0.1:
        composite = (
            us_eq * 0.30
            + vix_imp * 0.18
            + fx_imp * 0.17
            + bond_imp * 0.05
            + commodity_imp * 0.08
            + asia_imp * 0.08
            + koru_signal * 0.14  # KORU direct predictor
        )
    else:
        # KORU unavailable → original weights
        composite = (
            us_eq * 0.35
            + vix_imp * 0.20
            + fx_imp * 0.20
            + bond_imp * 0.05
            + commodity_imp * 0.10
            + asia_imp * 0.10
        )

    # Direction classification
    if composite <= -2.5:
        direction = "strong_bearish"
    elif composite <= -1.0:
        direction = "bearish"
    elif composite >= 2.5:
        direction = "strong_bullish"
    elif composite >= 1.0:
        direction = "bullish"
    else:
        direction = "neutral"

    # Confidence: higher when multiple factors align
    factors = [us_eq, vix_imp, fx_imp, bond_imp, commodity_imp, asia_imp]
    signs = [1 if f > 0.3 else (-1 if f < -0.3 else 0) for f in factors]
    alignment = abs(sum(signs)) / max(len([s for s in signs if s != 0]), 1)
    confidence = min(alignment * 0.7 + abs(composite) / 10 * 0.3, 1.0)

    # Expected KOSPI gap
    kospi_beta = 0.5 if composite > 0 else 0.7
    expected_gap = composite * kospi_beta * 0.2  # rough estimate

    return ImpactScore(
        composite_score=round(composite, 2),
        confidence=round(confidence, 2),
        direction=direction,
        expected_kospi_gap_pct=round(expected_gap, 2),
        us_equity_impact=round(us_eq, 2),
        vix_impact=round(vix_imp, 2),
        fx_impact=round(fx_imp, 2),
        bond_impact=round(bond_imp, 2),
        commodity_impact=round(commodity_imp, 2),
        asia_spillover=round(asia_imp, 2),
        sector_impacts=_compute_sector_impacts(snap),
        risk_flags=_detect_risk_flags(snap),
    )


def generate_outlook(
    snap: CrossMarketSnapshot,
    impact: ImpactScore,
) -> MarketOutlook:
    """Generate forward-looking market outlook."""
    from datetime import date as _date

    # Headline
    dir_kr = {
        "strong_bearish": "강한 하방 압력",
        "bearish": "하방 압력",
        "neutral": "중립적 흐름",
        "bullish": "상방 압력",
        "strong_bullish": "강한 상방 압력",
    }
    headline = (
        f"한국 증시 전망: {dir_kr.get(impact.direction, '?')} "
        f"(종합 {impact.composite_score:+.1f}, 신뢰도 {impact.confidence:.0%})"
    )

    # Key drivers
    drivers = []
    components = [
        ("미국 증시", impact.us_equity_impact),
        ("VIX/공포", impact.vix_impact),
        ("환율", impact.fx_impact),
        ("채권", impact.bond_impact),
        ("원자재", impact.commodity_impact),
        ("아시아", impact.asia_spillover),
    ]
    for name, val in sorted(components, key=lambda x: abs(x[1]), reverse=True):
        if abs(val) >= 0.3:
            arrow = "+" if val > 0 else ""
            drivers.append(f"{name} {arrow}{val:.1f}")

    # Sector rotation suggestions
    sector_sorted = sorted(impact.sector_impacts.items(), key=lambda x: x[1], reverse=True)
    rotation = []
    for sector, score in sector_sorted[:3]:
        if score > 0.5:
            rotation.append(f"수혜: {sector} ({score:+.1f})")
    for sector, score in sector_sorted[-3:]:
        if score < -0.5:
            rotation.append(f"주의: {sector} ({score:+.1f})")

    # Risk warnings
    warnings = list(impact.risk_flags)
    if snap.vix_regime in ("stress", "crisis"):
        warnings.append(f"VIX {snap.vix_regime} 모드 - 전면적 리스크 관리 필요")

    # Action items
    actions = []
    if impact.direction in ("strong_bearish", "bearish"):
        actions.append("신규 매수 보류, 기존 포지션 리스크 점검")
        if impact.composite_score < -3:
            actions.append("손절 기준 엄격 적용, 현금 비중 확대 검토")
    elif impact.direction in ("strong_bullish", "bullish"):
        actions.append("수혜 섹터 중심 매수 기회 탐색")
        if impact.composite_score > 3:
            actions.append("모멘텀 종목 적극 관심")
    else:
        actions.append("섹터별 선별 대응, 트레이딩 범위 축소")

    return MarketOutlook(
        date=snap.date,
        impact_score=impact,
        snapshot=snap,
        headline=headline,
        key_drivers=drivers,
        sector_rotation=rotation,
        risk_warnings=warnings,
        action_items=actions,
    )


# ---------------------------------------------------------------------------
# ML Feature helpers
# ---------------------------------------------------------------------------

def build_cross_market_features(snap: CrossMarketSnapshot) -> Dict[str, float]:
    """Extract ML features from cross-market snapshot.

    Returns 8 features for the ML predictor.
    """
    impact = compute_cross_market_impact(snap)

    return {
        "us_overnight_impact": impact.us_equity_impact,
        "vix_velocity": snap.vix_change_pct,
        "usdkrw_change": snap.usdkrw_change_pct,
        "us10y_change_bp": snap.us10y_change_bp,
        "oil_change_pct": snap.wti_change_pct,
        "gold_change_pct": snap.gold_change_pct,
        "asia_spillover": impact.asia_spillover,
        "cross_market_composite": impact.composite_score,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_impact_report(outlook: MarketOutlook) -> str:
    """Format MarketOutlook as plain text for Telegram."""
    lines = []
    snap = outlook.snapshot
    imp = outlook.impact_score

    # Header
    dir_emoji = {
        "strong_bearish": "🔴🔴", "bearish": "🔴",
        "neutral": "⚪", "bullish": "🟢", "strong_bullish": "🟢🟢",
    }
    lines.append(f"🌐 크로스마켓 전망 {dir_emoji.get(imp.direction, '⚪')}")
    lines.append(f"{outlook.headline}")
    lines.append("")

    # Global snapshot
    lines.append("[글로벌 시장]")
    lines.append(f"S&P500: {snap.sp500_change_pct:+.1f}% | NASDAQ: {snap.nasdaq_change_pct:+.1f}%")
    lines.append(f"VIX: {snap.vix:.1f} ({snap.vix_change_pct:+.1f}%) [{snap.vix_regime}]")
    lines.append(f"USD/KRW: {snap.usdkrw:.0f} ({snap.usdkrw_change_pct:+.2f}%) | USD/JPY: {snap.usdjpy:.1f} ({snap.usdjpy_change_pct:+.2f}%)")
    lines.append(f"WTI: ${snap.wti:.1f} ({snap.wti_change_pct:+.1f}%) | Gold: ${snap.gold:.0f} ({snap.gold_change_pct:+.1f}%)")
    lines.append(f"미국채 10Y: {snap.us10y_yield:.2f}% ({snap.us10y_change_bp:+.0f}bp)")
    lines.append("")

    # v11.0: KORU signal
    if snap.koru_close > 0:
        lines.append(f"KORU 3X: ${snap.koru_close:.2f} ({snap.koru_change_pct:+.1f}%) → 내재 KOSPI {snap.koru_implied_kospi_pct:+.2f}%")
    lines.append("")

    # Asian markets
    lines.append("[아시아]")
    lines.append(
        f"닛케이: {snap.nikkei_change_pct:+.1f}% | "
        f"상해: {snap.shanghai_change_pct:+.1f}% | "
        f"항셍: {snap.hsi_change_pct:+.1f}% | "
        f"대만: {snap.taiwan_change_pct:+.1f}%"
    )
    lines.append("")

    # Impact breakdown
    lines.append("[영향도 분석]")
    lines.append(f"종합 점수: {imp.composite_score:+.1f} / 10 (신뢰도 {imp.confidence:.0%})")
    lines.append(f"예상 KOSPI 갭: {imp.expected_kospi_gap_pct:+.2f}%")
    if outlook.key_drivers:
        lines.append(f"핵심 요인: {' | '.join(outlook.key_drivers[:4])}")
    lines.append("")

    # Sector rotation
    if outlook.sector_rotation:
        lines.append("[섹터 전망]")
        for sr in outlook.sector_rotation:
            lines.append(f"  {sr}")
        lines.append("")

    # Risk warnings
    if outlook.risk_warnings:
        lines.append("[리스크 경고]")
        for w in outlook.risk_warnings:
            lines.append(f"  ⚠️ {w}")
        lines.append("")

    # Action items
    if outlook.action_items:
        lines.append("[대응 전략]")
        for a in outlook.action_items:
            lines.append(f"  → {a}")

    return "\n".join(lines)


def format_impact_context_for_ai(outlook: MarketOutlook) -> str:
    """Format outlook for AI system prompt context."""
    snap = outlook.snapshot
    imp = outlook.impact_score

    lines = [
        f"크로스마켓 종합점수: {imp.composite_score:+.1f}/10 ({imp.direction}, 신뢰도 {imp.confidence:.0%})",
        f"예상 KOSPI 시가 갭: {imp.expected_kospi_gap_pct:+.2f}%",
        f"S&P500: {snap.sp500_change_pct:+.1f}%, NASDAQ: {snap.nasdaq_change_pct:+.1f}%",
        f"VIX: {snap.vix:.1f} ({snap.vix_regime}), USD/KRW: {snap.usdkrw:.0f} ({snap.usdkrw_change_pct:+.2f}%), USD/JPY: {snap.usdjpy:.1f}",
        f"대만TWII: {snap.taiwan_change_pct:+.1f}%, 닛케이: {snap.nikkei_change_pct:+.1f}%",
        f"KORU 3X: {snap.koru_change_pct:+.1f}% (내재 KOSPI {snap.koru_implied_kospi_pct:+.2f}%)" if snap.koru_close > 0 else "",
    ]

    if outlook.key_drivers:
        lines.append(f"핵심 요인: {', '.join(outlook.key_drivers[:3])}")

    if outlook.sector_rotation:
        lines.append(f"섹터: {', '.join(outlook.sector_rotation[:4])}")

    if outlook.risk_warnings:
        lines.append(f"리스크: {', '.join(outlook.risk_warnings[:3])}")

    if outlook.action_items:
        lines.append(f"전략: {outlook.action_items[0]}")

    return "\n".join(lines)


def to_db_dict(snap: CrossMarketSnapshot, impact: ImpactScore) -> dict:
    """Convert snapshot + impact to a dict suitable for DB storage."""
    import json

    return {
        "date": snap.date,
        "sp500_change_pct": snap.sp500_change_pct,
        "nasdaq_change_pct": snap.nasdaq_change_pct,
        "vix": snap.vix,
        "vix_change_pct": snap.vix_change_pct,
        "vix_regime": snap.vix_regime,
        "usdkrw": snap.usdkrw,
        "usdkrw_change_pct": snap.usdkrw_change_pct,
        "us10y_yield": snap.us10y_yield,
        "wti_change_pct": snap.wti_change_pct,
        "gold_change_pct": snap.gold_change_pct,
        "composite_score": impact.composite_score,
        "direction": impact.direction,
        "confidence": impact.confidence,
        "expected_gap_pct": impact.expected_kospi_gap_pct,
        "sector_impacts_json": json.dumps(impact.sector_impacts, ensure_ascii=False),
        "risk_flags_json": json.dumps(impact.risk_flags, ensure_ascii=False),
    }
