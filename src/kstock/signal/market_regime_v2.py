"""v12.2: Market Regime Detection Module (5-regime, cross-market aware).

oil_analysis, cross_market_impact, korea_risk 등 업스트림 시그널을
통합하여 시장을 5단계 레짐으로 분류하고 섹터 로테이션/포트폴리오 가이드를 제공.

데이터 소스: DB에 저장된 cross_market_impact, oil_analysis, MacroSnapshot.
직접 API 호출 없음 — 모든 입력은 업스트림 모듈에서 온다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGIME_NAMES = ["strong_bull", "bull", "neutral", "bear", "crash"]

REGIME_ENCODE_MAP: Dict[str, int] = {
    "strong_bull": 4,
    "bull": 3,
    "neutral": 2,
    "bear": 1,
    "crash": 0,
}

REGIME_KR: Dict[str, str] = {
    "strong_bull": "강세장",
    "bull": "상승장",
    "neutral": "횡보장",
    "bear": "하락장",
    "crash": "위기",
}

REGIME_EMOJI: Dict[str, str] = {
    "strong_bull": "\U0001f7e2\U0001f7e2",
    "bull": "\U0001f7e2",
    "neutral": "\u26aa",
    "bear": "\U0001f534",
    "crash": "\U0001f534\U0001f534",
}

# 레짐별 섹터 로테이션
REGIME_SECTOR_ROTATION: Dict[str, Dict[str, str]] = {
    "strong_bull": {
        "반도체": "최대 비중 (모멘텀 극대화)",
        "2차전지": "적극 매수 (성장주 랠리)",
        "바이오": "선별 매수 (테마주 강세)",
        "금융": "중립 (금리 안정)",
        "방산": "비중 축소 (위험선호 환경)",
    },
    "bull": {
        "반도체": "비중 확대",
        "2차전지": "매수 유지",
        "자동차": "수출 수혜 관심",
        "IT": "비중 확대",
        "금융": "중립",
    },
    "neutral": {
        "반도체": "선별적 접근",
        "금융": "배당주 관심",
        "철강": "밸류에이션 매력 체크",
        "조선": "수주 모멘텀 확인",
    },
    "bear": {
        "방산": "방어주 비중 확대",
        "금융": "고배당주 집중",
        "에너지": "유가 연동 확인 후 선별",
        "반도체": "비중 축소, 급락 시 분할 매수",
    },
    "crash": {
        "방산": "최대 방어",
        "금융": "대형 고배당만 유지",
        "현금": "현금 비중 50% 이상",
        "반도체": "투매 후 바닥 확인 시 분할 매수",
    },
}

# 레짐별 포트폴리오 가이드
REGIME_PORTFOLIO_GUIDE: Dict[str, Dict[str, str]] = {
    "strong_bull": {
        "position_size": "최대 (100%)",
        "hedging": "없음",
        "new_buy": "적극 매수",
        "stop_loss": "-7% (여유롭게)",
        "take_profit": "추세 추종 (트레일링 스탑)",
    },
    "bull": {
        "position_size": "확대 (80-100%)",
        "hedging": "최소",
        "new_buy": "분할 매수",
        "stop_loss": "-5%",
        "take_profit": "목표가 분할 매도",
    },
    "neutral": {
        "position_size": "보통 (60-80%)",
        "hedging": "인버스 ETF 10%",
        "new_buy": "선별적, 눌림목 매수",
        "stop_loss": "-5%",
        "take_profit": "+8~10% 분할",
    },
    "bear": {
        "position_size": "축소 (40-60%)",
        "hedging": "인버스 ETF 20%",
        "new_buy": "신규 매수 보류",
        "stop_loss": "-3% (엄격)",
        "take_profit": "+5% 즉시",
    },
    "crash": {
        "position_size": "최소 (20-40%)",
        "hedging": "인버스 ETF 30%+",
        "new_buy": "매수 중단 (바닥 확인 후)",
        "stop_loss": "-3% 즉시",
        "take_profit": "모든 수익 즉시 실현",
    },
}

# 레짐 전환 추천
_TRANSITION_RECS: Dict[Tuple[str, str], str] = {
    ("strong_bull", "bull"): "모멘텀 둔화, 차익실현 시작 검토",
    ("bull", "neutral"): "상승 모멘텀 소진, 포지션 축소 시작",
    ("neutral", "bear"): "하락 전환, 신규 매수 중단, 방어 태세",
    ("bear", "crash"): "위기 전환! 포지션 즉시 축소, 인버스 헤지",
    ("crash", "bear"): "최악 탈출, 아직 방어 유지",
    ("bear", "neutral"): "바닥 탈출 시도, 선별적 매수 재개",
    ("neutral", "bull"): "상승 전환 확인, 비중 확대 시작",
    ("bull", "strong_bull"): "강세 확인! 공격적 포지션 전환",
}

# 팩터 가중치
_FACTOR_WEIGHTS: Dict[str, float] = {
    "cross_market": 0.25,
    "vix": 0.20,
    "us_equity": 0.15,
    "kospi_momentum": 0.10,
    "fx": 0.10,
    "korea_risk": 0.10,
    "oil": 0.05,
    "bond": 0.05,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RegimeInput:
    """업스트림 모듈 데이터 집약."""
    # cross_market_impact
    cross_market_composite: float = 0.0
    cross_market_direction: str = "neutral"
    vix: float = 20.0
    vix_change_pct: float = 0.0
    vix_regime: str = "normal"
    sp500_change_pct: float = 0.0
    nasdaq_change_pct: float = 0.0
    usdkrw: float = 1300.0
    usdkrw_change_pct: float = 0.0
    us10y_yield: float = 4.0
    us10y_change_bp: float = 0.0
    # oil_analysis
    oil_regime: str = "neutral"
    oil_regime_strength: float = 0.0
    # korea_risk
    korea_risk_score: float = 0.0
    korea_risk_level: str = "안전"
    # KOSPI/KOSDAQ
    kospi_change_pct: float = 0.0
    kosdaq_change_pct: float = 0.0
    # 이전 레짐 (지속일수 추적)
    prev_regime: str = "neutral"
    prev_regime_duration: int = 0


@dataclass
class MarketRegime:
    """시장 레짐 분류 결과."""
    regime: str              # strong_bull / bull / neutral / bear / crash
    confidence: float        # 0.0 ~ 1.0
    duration_days: int       # 현재 레짐 지속일수
    transition_prob: float   # 전환 확률 (0~1)
    raw_score: float         # 종합 점수 (-100 ~ +100)
    description: str         # 한국어 설명


@dataclass
class RegimeSignal:
    """레짐 기반 액셔너블 시그널."""
    signal_type: str         # regime_change, volatility_expansion, etc.
    description: str
    strength: float          # 0~1
    recommendation: str


@dataclass
class MarketRegimeReport:
    """레짐 분석 통합 리포트."""
    regime: MarketRegime
    signals: List[RegimeSignal]
    sector_rotation: Dict[str, str]
    portfolio_guide: Dict[str, str]
    input_summary: Dict[str, float]


# ---------------------------------------------------------------------------
# Factor scoring functions
# ---------------------------------------------------------------------------

def _score_cross_market(composite: float, direction: str) -> float:
    """cross_market composite (-10~+10) → sub-score (-100~+100)."""
    return max(-100, min(100, composite * 10))


def _score_vix(vix: float, vix_change_pct: float, vix_regime: str) -> float:
    """VIX 레벨 + velocity → sub-score (-100~+100)."""
    # 레벨 점수
    if vix < 12:
        level = 60
    elif vix < 15:
        level = 40
    elif vix < 20:
        level = 20
    elif vix < 25:
        level = -20
    elif vix < 30:
        level = -50
    elif vix < 40:
        level = -80
    else:
        level = -100

    # velocity 보정
    velocity = 0
    if vix_change_pct > 15:
        velocity = -30
    elif vix_change_pct > 8:
        velocity = -15
    elif vix_change_pct < -15:
        velocity = 25
    elif vix_change_pct < -8:
        velocity = 15

    return max(-100, min(100, level + velocity))


def _score_us_equity(sp500_chg: float, nasdaq_chg: float) -> float:
    """미국 주식 야간 변동 → sub-score. SPX 40% + NASDAQ 60%, 하락 1.4x."""
    us_change = sp500_chg * 0.4 + nasdaq_chg * 0.6
    if us_change < 0:
        return max(-100, us_change * 20 * 1.4)
    return min(100, us_change * 20)


def _score_fx(usdkrw: float, usdkrw_change_pct: float) -> float:
    """USD/KRW 레벨 + velocity → sub-score (-100~+100)."""
    if usdkrw < 1200:
        level = 40
    elif usdkrw < 1250:
        level = 20
    elif usdkrw < 1300:
        level = 0
    elif usdkrw < 1350:
        level = -20
    elif usdkrw < 1400:
        level = -50
    else:
        level = -80

    velocity = 0
    if usdkrw_change_pct > 0.5:
        velocity = max(usdkrw_change_pct * -30, -40)
    elif usdkrw_change_pct < -0.3:
        velocity = min(abs(usdkrw_change_pct) * 20, 30)

    return max(-100, min(100, level + velocity))


def _score_korea_risk(risk_score: float) -> float:
    """한국 리스크 (0-100, 반전) → sub-score (-100~+100)."""
    return max(-100, min(100, 40 - risk_score * 1.4))


def _score_oil(oil_regime: str, oil_strength: float) -> float:
    """유가 레짐 → sub-score. 한국은 원유 순수입국."""
    _scores = {
        "bull": -20,
        "bear": 15,
        "neutral": 0,
        "spike": -40,
        "crash": 20,
    }
    base = _scores.get(oil_regime, 0)
    return base * max(oil_strength, 0.3)


def _score_bond(us10y_yield: float, us10y_change_bp: float) -> float:
    """미 국채 10Y → sub-score (-60~+30)."""
    score = 0
    if us10y_change_bp > 15:
        score = -40
    elif us10y_change_bp > 10:
        score = -20
    elif us10y_change_bp > 5:
        score = -10
    elif us10y_change_bp < -10:
        score = 20
    elif us10y_change_bp < -5:
        score = 10

    if us10y_yield > 5.0:
        score -= 20
    elif us10y_yield > 4.5:
        score -= 10

    return max(-60, min(30, score))


def _score_kospi_momentum(kospi_chg: float, kosdaq_chg: float) -> float:
    """KOSPI/KOSDAQ 모멘텀 → sub-score. KOSPI 60% + KOSDAQ 40%."""
    combined = kospi_chg * 0.6 + kosdaq_chg * 0.4
    return max(-100, min(100, combined * 20))


# ---------------------------------------------------------------------------
# Core: regime classification
# ---------------------------------------------------------------------------

def _raw_to_regime(raw_score: float) -> str:
    """raw_score(-100~+100) → 레짐 이름."""
    if raw_score > 40:
        return "strong_bull"
    elif raw_score > 15:
        return "bull"
    elif raw_score >= -15:
        return "neutral"
    elif raw_score >= -40:
        return "bear"
    else:
        return "crash"


def _compute_confidence(sub_scores: Dict[str, float], raw_score: float) -> float:
    """팩터 방향 일치도 + 점수 크기 → confidence (0~1)."""
    positive = sum(1 for v in sub_scores.values() if v > 10)
    negative = sum(1 for v in sub_scores.values() if v < -10)
    total = positive + negative
    alignment = max(positive, negative) / total if total > 0 else 0.3
    magnitude = min(abs(raw_score) / 60, 1.0)
    return round(min(alignment * 0.6 + magnitude * 0.4, 1.0), 2)


def _compute_transition_prob(
    current: str, prev: str, duration: int, confidence: float,
) -> float:
    """레짐 전환 확률 추정."""
    duration_factor = max(0, 1.0 - duration / 30)
    conf_factor = 1.0 - confidence
    recent_change = 0.3 if current != prev else 0.0
    return round(min(duration_factor * 0.4 + conf_factor * 0.3 + recent_change, 1.0), 2)


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def _transition_severity(prev: str, current: str) -> float:
    _order = {"strong_bull": 4, "bull": 3, "neutral": 2, "bear": 1, "crash": 0}
    diff = abs(_order.get(prev, 2) - _order.get(current, 2))
    return min(diff / 3, 1.0)


def _detect_signals(
    inp: RegimeInput,
    regime: MarketRegime,
    sub_scores: Dict[str, float],
) -> List[RegimeSignal]:
    """레짐 기반 시그널 감지 (7종)."""
    signals: List[RegimeSignal] = []

    # 1. 레짐 전환
    if regime.regime != inp.prev_regime and inp.prev_regime:
        rec = _TRANSITION_RECS.get(
            (inp.prev_regime, regime.regime),
            f"레짐 전환({inp.prev_regime}->{regime.regime}), 포트폴리오 재점검",
        )
        signals.append(RegimeSignal(
            signal_type="regime_change",
            description=f"시장 레짐 전환: {REGIME_KR.get(inp.prev_regime, inp.prev_regime)} -> {REGIME_KR.get(regime.regime, regime.regime)}",
            strength=_transition_severity(inp.prev_regime, regime.regime),
            recommendation=rec,
        ))

    # 2. 변동성 급팽창
    if inp.vix_change_pct > 15:
        signals.append(RegimeSignal(
            signal_type="volatility_expansion",
            description=f"변동성 급팽창 (VIX {inp.vix_change_pct:+.1f}%)",
            strength=min(inp.vix_change_pct / 25, 1.0),
            recommendation="포지션 축소, 손절 강화, 신규 매수 보류",
        ))

    # 3. 리스크 동시 악화
    neg_count = sum(1 for v in sub_scores.values() if v < -20)
    if neg_count >= 4:
        signals.append(RegimeSignal(
            signal_type="risk_escalation",
            description=f"리스크 동시 악화 ({neg_count}개 요인 부정적)",
            strength=min(neg_count / 6, 1.0),
            recommendation="전면 방어, 현금 비중 50% 이상",
        ))

    # 4. 하락장 회복 초기 신호
    if inp.prev_regime in ("bear", "crash") and regime.raw_score > -15:
        signals.append(RegimeSignal(
            signal_type="recovery_early",
            description=f"하락장 탈출 초기 (점수 {regime.raw_score:+.0f})",
            strength=0.6,
            recommendation="방어 포지션 축소 검토, 선별적 분할 매수",
        ))

    # 5. 폭락 경고
    if regime.regime == "bear" and regime.raw_score < -30:
        signals.append(RegimeSignal(
            signal_type="crash_warning",
            description=f"폭락 경고 (점수 {regime.raw_score:+.0f}, crash 임계 -40)",
            strength=0.8,
            recommendation="즉시 현금 확대, 인버스 헤지 강화",
        ))

    # 6. 강세 모멘텀 확인
    if regime.regime == "strong_bull" and regime.confidence > 0.7:
        signals.append(RegimeSignal(
            signal_type="momentum_surge",
            description=f"강세 모멘텀 확인 (신뢰도 {regime.confidence:.0%})",
            strength=regime.confidence,
            recommendation="모멘텀 전략 강화, 추세 추종, 돌파 매수",
        ))

    # 7. 환율 + VIX 동시 스트레스
    if inp.usdkrw_change_pct > 0.8 and inp.vix > 25:
        signals.append(RegimeSignal(
            signal_type="fx_stress",
            description="원화 급락 + VIX 공포 동시 발생",
            strength=0.9,
            recommendation="외인 이탈 극대화, 수출주 외 전면 방어",
        ))

    return signals


# ---------------------------------------------------------------------------
# Main compute
# ---------------------------------------------------------------------------

def compute_market_regime(inp: RegimeInput) -> MarketRegimeReport:
    """시장 레짐 통합 감지.

    RegimeInput → 8개 팩터 스코어링 → 가중합산 → 레짐 분류 → 시그널/가이드.
    """
    # 1. 팩터별 sub-score
    sub_scores = {
        "cross_market": _score_cross_market(inp.cross_market_composite, inp.cross_market_direction),
        "vix": _score_vix(inp.vix, inp.vix_change_pct, inp.vix_regime),
        "us_equity": _score_us_equity(inp.sp500_change_pct, inp.nasdaq_change_pct),
        "fx": _score_fx(inp.usdkrw, inp.usdkrw_change_pct),
        "korea_risk": _score_korea_risk(inp.korea_risk_score),
        "oil": _score_oil(inp.oil_regime, inp.oil_regime_strength),
        "bond": _score_bond(inp.us10y_yield, inp.us10y_change_bp),
        "kospi_momentum": _score_kospi_momentum(inp.kospi_change_pct, inp.kosdaq_change_pct),
    }

    # 2. 가중 합산
    raw_score = sum(sub_scores[k] * _FACTOR_WEIGHTS[k] for k in _FACTOR_WEIGHTS)

    # 3. 레짐 분류
    regime_name = _raw_to_regime(raw_score)
    confidence = _compute_confidence(sub_scores, raw_score)

    # 4. 지속일수
    duration = inp.prev_regime_duration + 1 if regime_name == inp.prev_regime else 1
    transition_prob = _compute_transition_prob(
        regime_name, inp.prev_regime, duration, confidence,
    )

    # 5. 설명
    desc = f"{REGIME_KR.get(regime_name, '?')} (종합 {raw_score:+.0f}, 신뢰도 {confidence:.0%})"

    regime = MarketRegime(
        regime=regime_name,
        confidence=confidence,
        duration_days=duration,
        transition_prob=transition_prob,
        raw_score=round(raw_score, 1),
        description=desc,
    )

    # 6. 시그널
    signals = _detect_signals(inp, regime, sub_scores)

    # 7. 섹터/가이드
    sector_rotation = REGIME_SECTOR_ROTATION.get(regime_name, {})
    portfolio_guide = REGIME_PORTFOLIO_GUIDE.get(regime_name, {})

    # 8. 입력 요약
    input_summary = {
        "cross_market": inp.cross_market_composite,
        "vix": inp.vix,
        "usdkrw": inp.usdkrw,
        "korea_risk": inp.korea_risk_score,
        "oil_regime": inp.oil_regime,
        "raw_score": raw_score,
    }

    return MarketRegimeReport(
        regime=regime,
        signals=signals,
        sector_rotation=sector_rotation,
        portfolio_guide=portfolio_guide,
        input_summary=input_summary,
    )


# ---------------------------------------------------------------------------
# ML feature builder
# ---------------------------------------------------------------------------

def build_regime_ml_features(report: MarketRegimeReport) -> Dict[str, float]:
    """ML 피처 4개 생성."""
    return {
        "market_regime_encoded": float(REGIME_ENCODE_MAP.get(report.regime.regime, 2)),
        "regime_confidence": report.regime.confidence,
        "regime_duration": min(report.regime.duration_days / 60.0, 1.0),
        "regime_transition_prob": report.regime.transition_prob,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_regime_report(report: MarketRegimeReport) -> str:
    """텔레그램 발송용 레짐 리포트."""
    r = report.regime
    emoji = REGIME_EMOJI.get(r.regime, "?")
    lines = [
        f"\U0001f4ca 시장 레짐 리포트 {emoji}",
        f"{'━' * 22}",
        f"[레짐] {r.description}",
        f"신뢰도: {r.confidence:.0%} | 지속: {r.duration_days}일 | 전환확률: {r.transition_prob:.0%}",
        "",
    ]
    if report.signals:
        lines.append("[시그널]")
        for s in sorted(report.signals, key=lambda x: -x.strength):
            lines.append(f"  [{s.strength:.0%}] {s.description}")
            lines.append(f"  -> {s.recommendation}")
        lines.append("")

    if report.sector_rotation:
        lines.append("[섹터 로테이션]")
        for sec, rec in list(report.sector_rotation.items())[:5]:
            lines.append(f"  {sec}: {rec}")
        lines.append("")

    if report.portfolio_guide:
        _guide_kr = {
            "position_size": "포지션",
            "hedging": "헤지",
            "new_buy": "매수",
            "stop_loss": "손절",
            "take_profit": "익절",
        }
        lines.append("[포트폴리오 가이드]")
        for key, label in _guide_kr.items():
            val = report.portfolio_guide.get(key, "")
            if val:
                lines.append(f"  {label}: {val}")

    return "\n".join(lines)


def format_regime_context_for_ai(report: MarketRegimeReport) -> str:
    """Claude AI 시스템 프롬프트 주입용 레짐 컨텍스트."""
    r = report.regime
    lines = [
        f"시장 레짐: {REGIME_KR.get(r.regime, '?')} (종합 {r.raw_score:+.0f}, 신뢰도 {r.confidence:.0%})",
        f"레짐 지속: {r.duration_days}일, 전환확률: {r.transition_prob:.0%}",
    ]
    for s in report.signals[:2]:
        lines.append(f"레짐 시그널: {s.description} -> {s.recommendation}")
    guide = report.portfolio_guide
    if guide.get("new_buy"):
        lines.append(f"매수 가이드: {guide['new_buy']}")
    if guide.get("position_size"):
        lines.append(f"포지션: {guide['position_size']}")
    return "\n".join(lines)


def to_db_dict(report: MarketRegimeReport, date_str: str) -> dict:
    """DB 저장용 dict."""
    signals_data = [
        {"signal_type": s.signal_type, "description": s.description,
         "strength": s.strength, "recommendation": s.recommendation}
        for s in report.signals
    ]
    return {
        "date": date_str,
        "regime": report.regime.regime,
        "confidence": report.regime.confidence,
        "duration_days": report.regime.duration_days,
        "transition_prob": report.regime.transition_prob,
        "raw_score": report.regime.raw_score,
        "description": report.regime.description,
        "signals_json": json.dumps(signals_data, ensure_ascii=False),
        "sector_rotation_json": json.dumps(report.sector_rotation, ensure_ascii=False),
        "portfolio_guide_json": json.dumps(report.portfolio_guide, ensure_ascii=False),
        "input_summary_json": json.dumps(report.input_summary, ensure_ascii=False),
    }
