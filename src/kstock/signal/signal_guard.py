"""신호 가드 — 장기 보유 보호 + 신뢰도 등급 엔진.

장기 보유 종목에 대한 단기 매도 신호를 억제하고,
각 신호에 신뢰도 등급(A~D)을 부여한다.

사용:
    from kstock.signal.signal_guard import (
        apply_holding_guard,
        compute_signal_reliability,
        SignalReliability,
        HoldingGuardResult,
    )

    # 장기 보유 보호
    guard = apply_holding_guard(
        consensus="SELL", holding_type="long_term",
        hold_days=120, pnl_pct=-3.0,
    )
    if guard.suppressed:
        print(f"매도 억제: {guard.reason}")

    # 신뢰도 등급
    rel = compute_signal_reliability(
        consensus="BUY", confidence=0.8, agreement=0.9,
        contributing_count=5, total_votes=7,
        signal_source="consensus",
    )
    print(f"등급: {rel.grade} ({rel.label})")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HoldingGuardResult:
    """장기 보유 보호 판정 결과.

    Attributes:
        original_consensus: 원래 앙상블 합의.
        adjusted_consensus: 보호 적용 후 합의.
        suppressed: 매도 신호가 억제되었는지.
        reason: 억제/유지 이유.
        override_conditions: 보호 해제 조건 목록.
    """

    original_consensus: str = "HOLD"
    adjusted_consensus: str = "HOLD"
    suppressed: bool = False
    reason: str = ""
    override_conditions: List[str] = field(default_factory=list)


@dataclass
class SignalReliability:
    """신호 신뢰도 등급.

    Attributes:
        grade: A/B/C/D (A=높은 신뢰, D=낮은 신뢰).
        label: 한글 라벨.
        score: 0~100 신뢰도 점수.
        factors: 등급 산정에 사용된 요인별 점수.
        emoji: 등급별 이모지.
        warning: 신뢰도 낮을 때 경고 메시지.
    """

    grade: str = "C"
    label: str = "보통"
    score: float = 50.0
    factors: Dict[str, float] = field(default_factory=dict)
    emoji: str = "🟡"
    warning: str = ""


# ---------------------------------------------------------------------------
# 1. 장기 보유 보호 로직
# ---------------------------------------------------------------------------

# 보유 유형별 매도 억제 규칙
_HOLDING_PROTECTION = {
    "long_term": {
        "sell_suppress_threshold": -15.0,  # -15% 미만이면 억제 해제
        "min_hold_days_for_sell": 180,     # 180일 이상 보유해야 매도 허용
        "label": "장기 (버핏)",
        "description": "장기 투자는 펀더멘탈 기반. 단기 기술적 매도 신호 억제.",
    },
    "position": {
        "sell_suppress_threshold": -10.0,
        "min_hold_days_for_sell": 60,
        "label": "포지션 (린치)",
        "description": "포지션 투자는 중기 관점. 과도한 매도 억제.",
    },
    "swing": {
        "sell_suppress_threshold": -7.0,
        "min_hold_days_for_sell": 5,
        "label": "스윙 (오닐)",
        "description": "스윙은 계획된 손절만 허용.",
    },
    "scalp": {
        "sell_suppress_threshold": -3.0,
        "min_hold_days_for_sell": 0,
        "label": "단타 (리버모어)",
        "description": "단타는 모든 매도 신호 허용.",
    },
}


def apply_holding_guard(
    consensus: str,
    holding_type: str = "auto",
    hold_days: int = 0,
    pnl_pct: float = 0.0,
    confidence: float = 0.5,
    agreement: float = 0.5,
    market_regime: str = "normal",
) -> HoldingGuardResult:
    """보유 종목에 대한 매도 신호 억제 여부를 판정한다.

    장기/포지션 보유 종목은 단기 기술적 매도 신호를 억제하되,
    심각한 손실이나 위기 상황에서는 억제를 해제한다.

    Args:
        consensus: 앙상블 합의 (STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL).
        holding_type: 투자 유형 (scalp/swing/position/long_term).
        hold_days: 보유 일수.
        pnl_pct: 현재 수익률 (%).
        confidence: 매도 신호 신뢰도 (0~1).
        agreement: 전략 합의도 (0~1).
        market_regime: 시장 레짐 (normal/fear/panic/crisis).

    Returns:
        HoldingGuardResult.
    """
    result = HoldingGuardResult(
        original_consensus=consensus,
        adjusted_consensus=consensus,
    )

    # 매수/홀드 신호는 억제 대상 아님
    if consensus not in ("SELL", "STRONG_SELL"):
        result.reason = "매도 신호가 아님 — 보호 불필요"
        return result

    # 보유 유형 없거나 auto면 보호 없음
    protection = _HOLDING_PROTECTION.get(holding_type)
    if not protection:
        result.reason = "보유 유형 미설정 — 보호 없음"
        return result

    # 단타는 보호 없음
    if holding_type == "scalp":
        result.reason = "단타 종목 — 매도 허용"
        return result

    # === 보호 해제 조건 (예외) ===
    override_conditions: List[str] = []

    # 조건 1: 심각한 손실 — 절대 손절 라인
    if pnl_pct <= protection["sell_suppress_threshold"]:
        override_conditions.append(
            f"손실 {pnl_pct:.1f}% — {protection['label']} 절대 손절 라인"
            f" ({protection['sell_suppress_threshold']}%) 초과"
        )

    # 조건 2: 시장 위기 (panic/crisis)
    if market_regime in ("panic", "crisis"):
        override_conditions.append(
            f"시장 {market_regime} 상태 — 위기 탈출 매도 허용"
        )

    # 조건 3: 만장일치 매도 (agreement >= 0.9)
    if agreement >= 0.9 and confidence >= 0.8:
        override_conditions.append(
            f"전략 만장일치 매도 (합의 {agreement:.0%}, 신뢰 {confidence:.0%})"
        )

    # 조건 4: STRONG_SELL + 높은 신뢰도
    if consensus == "STRONG_SELL" and confidence >= 0.85:
        override_conditions.append(
            f"STRONG_SELL + 높은 신뢰도 ({confidence:.0%})"
        )

    result.override_conditions = override_conditions

    # 해제 조건이 있으면 매도 허용
    if override_conditions:
        result.reason = "보호 해제: " + " / ".join(override_conditions)
        return result

    # === 매도 억제 ===
    result.suppressed = True
    result.adjusted_consensus = "HOLD"

    # 억제 이유 생성
    reasons = []
    reasons.append(f"{protection['label']} 보유 {hold_days}일차")
    reasons.append(protection["description"])

    if pnl_pct > 0:
        reasons.append(f"현재 수익 {pnl_pct:+.1f}% — 급하게 팔 이유 없음")
    elif pnl_pct > protection["sell_suppress_threshold"]:
        reasons.append(
            f"손실 {pnl_pct:.1f}% — 아직 절대 손절 라인"
            f" ({protection['sell_suppress_threshold']}%) 전"
        )

    result.reason = " | ".join(reasons)

    logger.info(
        "매도 억제: holding_type=%s hold_days=%d pnl=%.1f%% "
        "original=%s → %s",
        holding_type, hold_days, pnl_pct,
        consensus, result.adjusted_consensus,
    )
    return result


# ---------------------------------------------------------------------------
# 2. 신호 신뢰도 등급 엔진
# ---------------------------------------------------------------------------

# 신호 소스별 기본 신뢰도 보너스
_SOURCE_RELIABILITY = {
    "consensus": 15.0,          # 증권사 컨센서스 — 펀더멘탈 기반
    "multi_agent": 10.0,        # 멀티 에이전트 — AI 합의
    "manager_long_term": 12.0,  # 버핏 매니저
    "manager_position": 10.0,   # 린치 매니저
    "manager_swing": 5.0,       # 오닐 매니저
    "manager_scalp": 0.0,       # 리버모어 매니저
    "scan_engine": 3.0,         # 스캔 엔진
    "ml_prediction": 8.0,       # ML 예측
    "surge_detect": -5.0,       # 급등 감지 — 노이즈 높음
    "stealth_accumulation": 7.0,# 세력 포착
    "sector_rotation": 5.0,     # 섹터 로테이션
    "contrarian": -3.0,         # 역발상 — 리스크 높음
    "manual": 0.0,              # 수동
}


def compute_signal_reliability(
    consensus: str,
    confidence: float = 0.5,
    agreement: float = 0.5,
    contributing_count: int = 0,
    total_votes: int = 0,
    signal_source: str = "",
    hit_rate_30d: float = 0.5,
    holding_type: str = "",
    market_regime: str = "normal",
) -> SignalReliability:
    """신호의 신뢰도 등급을 계산한다.

    5가지 팩터를 가중 합산하여 0~100 점수 → A/B/C/D 등급.

    팩터:
        1. confidence (30%): 앙상블 신뢰도
        2. agreement (25%): 전략 합의도
        3. breadth (15%): 참여 전략 수 (5개 이상이면 만점)
        4. track_record (20%): 과거 30일 적중률
        5. source_quality (10%): 신호 소스 품질

    Args:
        consensus: 합의 결과.
        confidence: 0~1 신뢰도.
        agreement: 0~1 합의도.
        contributing_count: 합의 지지 전략 수.
        total_votes: 총 투표 수.
        signal_source: 신호 소스명.
        hit_rate_30d: 과거 30일 적중률.
        holding_type: 보유 유형 (매도 신호 시 참고).
        market_regime: 시장 레짐.

    Returns:
        SignalReliability.
    """
    factors: Dict[str, float] = {}

    # Factor 1: Confidence (30%)
    conf_score = min(confidence, 1.0) * 100
    factors["confidence"] = round(conf_score, 1)

    # Factor 2: Agreement (25%)
    agree_score = min(agreement, 1.0) * 100
    factors["agreement"] = round(agree_score, 1)

    # Factor 3: Breadth — 참여 전략 수 (15%)
    if total_votes >= 5:
        breadth_score = min(contributing_count / max(total_votes, 1), 1.0) * 100
    elif total_votes >= 3:
        breadth_score = min(contributing_count / max(total_votes, 1), 1.0) * 80
    else:
        breadth_score = 30.0  # 투표 수 부족
    factors["breadth"] = round(breadth_score, 1)

    # Factor 4: Track record (20%)
    track_score = min(hit_rate_30d, 1.0) * 100
    factors["track_record"] = round(track_score, 1)

    # Factor 5: Source quality (10%)
    source_bonus = _SOURCE_RELIABILITY.get(signal_source, 0.0)
    source_score = 50.0 + source_bonus  # 기본 50점 + 보너스
    source_score = max(0.0, min(100.0, source_score))
    factors["source_quality"] = round(source_score, 1)

    # 가중 합산
    total_score = (
        factors["confidence"] * 0.30
        + factors["agreement"] * 0.25
        + factors["breadth"] * 0.15
        + factors["track_record"] * 0.20
        + factors["source_quality"] * 0.10
    )

    # 레짐 패널티: 위기 시 모든 신호 신뢰도 하락
    if market_regime in ("panic", "crisis"):
        total_score *= 0.85
        factors["regime_penalty"] = -15.0

    # STRONG_ 보너스
    if consensus in ("STRONG_BUY", "STRONG_SELL"):
        total_score = min(100.0, total_score + 5.0)
        factors["strong_signal_bonus"] = 5.0

    total_score = max(0.0, min(100.0, total_score))

    # 등급 판정
    if total_score >= 75:
        grade, label, emoji = "A", "높은 신뢰", "🟢"
        warning = ""
    elif total_score >= 55:
        grade, label, emoji = "B", "양호", "🔵"
        warning = ""
    elif total_score >= 35:
        grade, label, emoji = "C", "보통", "🟡"
        warning = "참고용으로만 활용하세요"
    else:
        grade, label, emoji = "D", "낮은 신뢰", "🔴"
        warning = "이 신호만으로 매매하지 마세요"

    return SignalReliability(
        grade=grade,
        label=label,
        score=round(total_score, 1),
        factors=factors,
        emoji=emoji,
        warning=warning,
    )


# ---------------------------------------------------------------------------
# 3. 포맷 함수
# ---------------------------------------------------------------------------


def format_guard_result(guard: HoldingGuardResult) -> str:
    """보호 판정 결과를 텔레그램 포맷으로 변환."""
    if not guard.suppressed:
        return ""

    lines = [
        "🛡 장기 보유 보호 적용",
        f"원래 신호: {guard.original_consensus} → {guard.adjusted_consensus}",
        f"이유: {guard.reason}",
    ]

    if guard.override_conditions:
        lines.append("")
        lines.append("⚠️ 보호 해제 조건:")
        for cond in guard.override_conditions:
            lines.append(f"  • {cond}")

    return "\n".join(lines)


def format_reliability(rel: SignalReliability) -> str:
    """신뢰도 등급을 텔레그램 포맷으로 변환.

    한 줄 요약 + (경고 시) 추가 라인.
    """
    line = f"{rel.emoji} 신뢰도 {rel.grade}등급 ({rel.label}, {rel.score:.0f}점)"
    if rel.warning:
        line += f"\n⚠️ {rel.warning}"
    return line


def format_reliability_detail(rel: SignalReliability) -> str:
    """신뢰도 상세 팩터를 포맷."""
    lines = [
        f"{rel.emoji} 신뢰도 {rel.grade}등급 ({rel.label})",
        f"종합: {rel.score:.0f}/100",
        "",
    ]

    factor_labels = {
        "confidence": "신뢰도",
        "agreement": "합의도",
        "breadth": "참여폭",
        "track_record": "적중률",
        "source_quality": "소스품질",
    }

    for key, label in factor_labels.items():
        score = rel.factors.get(key, 0.0)
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        lines.append(f"  {label}: {bar} {score:.0f}")

    if rel.warning:
        lines.append("")
        lines.append(f"⚠️ {rel.warning}")

    return "\n".join(lines)
