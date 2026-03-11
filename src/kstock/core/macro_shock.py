"""v10.2 Global Macro Shock Engine.

글로벌 매크로 쇼크를 선제 감지하고, 위험 등급별 정책을 출력한다.
5등급: NONE → WATCH → ALERT → SHOCK → CRISIS

출력:
- ShockAssessment: 등급, 3대 리스크 스코어, 정책 지침
- Global Shock Score (0-100)
- Korea Open Risk Score (0-100)
- Foreign Outflow Risk Score (0-100)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


# ── 등급 정의 ──────────────────────────────────────────────

class ShockGrade(IntEnum):
    NONE = 0
    WATCH = 1
    ALERT = 2
    SHOCK = 3
    CRISIS = 4


GRADE_LABELS = {
    ShockGrade.NONE: "정상",
    ShockGrade.WATCH: "주의",
    ShockGrade.ALERT: "경계",
    ShockGrade.SHOCK: "위험",
    ShockGrade.CRISIS: "위기",
}

ALERT_COLORS = {
    ShockGrade.NONE: "GREEN",
    ShockGrade.WATCH: "YELLOW",
    ShockGrade.ALERT: "ORANGE",
    ShockGrade.SHOCK: "RED",
    ShockGrade.CRISIS: "RED",
}


# ── 임계값 ─────────────────────────────────────────────────
# v12.3: risk_thresholds.yaml에서 로드 (없으면 기본값 fallback)
# {category: [(grade, threshold)]}  — 절대값 기준 (양/음 모두 감지)

def _build_thresholds() -> dict:
    """risk_config에서 쇼크 임계값 로드, 실패 시 하드코딩 기본값."""
    try:
        from kstock.core.risk_config import get_risk_thresholds
        rt = get_risk_thresholds()
        return {
            "oil": [
                (ShockGrade.WATCH, rt.oil.watch_pct),
                (ShockGrade.ALERT, rt.oil.alert_pct),
                (ShockGrade.SHOCK, rt.oil.shock_pct),
            ],
            "us_futures": [
                (ShockGrade.WATCH, rt.us_futures.watch_pct),
                (ShockGrade.ALERT, rt.us_futures.alert_pct),
                (ShockGrade.SHOCK, rt.us_futures.shock_pct),
            ],
            "vix": [
                (ShockGrade.WATCH, rt.vix_change.watch_pct),
                (ShockGrade.ALERT, rt.vix_change.alert_pct),
                (ShockGrade.SHOCK, rt.vix_change.shock_pct),
            ],
            "dollar": [
                (ShockGrade.WATCH, rt.dollar.watch_pct),
                (ShockGrade.ALERT, rt.dollar.alert_pct),
                (ShockGrade.SHOCK, rt.dollar.shock_pct),
            ],
            "korea_etf": [
                (ShockGrade.WATCH, rt.korea_etf.watch_pct),
                (ShockGrade.ALERT, rt.korea_etf.alert_pct),
                (ShockGrade.SHOCK, rt.korea_etf.shock_pct),
            ],
            "usdkrw": [
                (ShockGrade.WATCH, rt.usdkrw_change.watch_pct),
                (ShockGrade.ALERT, rt.usdkrw_change.alert_pct),
                (ShockGrade.SHOCK, rt.usdkrw_change.shock_pct),
            ],
        }
    except Exception:
        return {
            "oil": [(ShockGrade.WATCH, 2.0), (ShockGrade.ALERT, 3.0), (ShockGrade.SHOCK, 5.0)],
            "us_futures": [(ShockGrade.WATCH, 1.0), (ShockGrade.ALERT, 1.5), (ShockGrade.SHOCK, 2.5)],
            "vix": [(ShockGrade.WATCH, 15.0), (ShockGrade.ALERT, 25.0), (ShockGrade.SHOCK, 40.0)],
            "dollar": [(ShockGrade.WATCH, 0.5), (ShockGrade.ALERT, 1.0), (ShockGrade.SHOCK, 1.5)],
            "korea_etf": [(ShockGrade.WATCH, 1.5), (ShockGrade.ALERT, 2.5), (ShockGrade.SHOCK, 4.0)],
            "usdkrw": [(ShockGrade.WATCH, 0.5), (ShockGrade.ALERT, 1.0), (ShockGrade.SHOCK, 1.5)],
        }

THRESHOLDS = _build_thresholds()

# 섹터 민감도 분류
SECTOR_SENSITIVITY = {
    "HIGH": ["반도체", "2차전지", "IT", "디스플레이", "인터넷", "플랫폼", "소프트웨어"],
    "MEDIUM": ["자동차", "화학", "철강", "금융", "조선", "정유", "건설"],
    "LOW": ["통신", "유틸리티", "음식료", "의료", "바이오", "내수", "방산"],
}


# ── 데이터 클래스 ──────────────────────────────────────────

@dataclass
class ShockSignal:
    """개별 카테고리 쇼크 신호."""
    category: str
    grade: ShockGrade
    value: float
    reason: str


@dataclass
class ShockPolicy:
    """쇼크 등급별 운영 정책."""
    new_buy_allowed: bool = True
    buy_restriction_sectors: list[str] = field(default_factory=list)
    position_action: dict[str, str] = field(default_factory=dict)
    atr_override_to_scalp: bool = False
    blocked_strategies: list[str] = field(default_factory=list)
    alert_level: str = "GREEN"
    recheck_time: str | None = None
    regime: str = "NEUTRAL"  # RISK_ON, RISK_OFF, PANIC, NEUTRAL
    ml_blend_override: dict | None = None


@dataclass
class ShockAssessment:
    """매크로 쇼크 종합 평가 결과."""
    signals: dict[str, ShockSignal] = field(default_factory=dict)
    overall_grade: ShockGrade = ShockGrade.NONE
    shock_count: int = 0
    dominant_shock: str = "none"
    global_shock_score: float = 0.0
    korea_open_risk_score: float = 0.0
    foreign_outflow_risk_score: float = 0.0
    policy: ShockPolicy = field(default_factory=ShockPolicy)
    timestamp: datetime | None = None


# ── 등급 판정 ──────────────────────────────────────────────

def _classify_signal(category: str, abs_value: float) -> ShockGrade:
    """절대값 기준 쇼크 등급 판정."""
    thresholds = THRESHOLDS.get(category, [])
    grade = ShockGrade.NONE
    for g, th in thresholds:
        if abs_value >= th:
            grade = g
    return grade


def _compute_global_shock_score(signals: dict[str, ShockSignal]) -> float:
    """Global Shock Score (0-100).

    가중치: oil 25%, us_futures 25%, vix 20%, dollar 15%, korea_etf 15%.
    """
    weights = {
        "oil": 0.25, "us_futures": 0.25, "vix": 0.20,
        "dollar": 0.15, "korea_etf": 0.15,
    }
    score = 0.0
    for cat, w in weights.items():
        sig = signals.get(cat)
        if sig:
            score += int(sig.grade) * 25.0 * w  # 0~100
    return round(min(100.0, score), 1)


def _compute_korea_open_risk(signals: dict[str, ShockSignal],
                             usdkrw_grade: ShockGrade) -> float:
    """Korea Open Risk Score (0-100).

    한국장 개장 리스크: KORU/EWY 30%, US선물 25%, 원달러 20%, 원유 15%, VIX 10%.
    """
    weights = {
        "korea_etf": 0.30, "us_futures": 0.25, "usdkrw": 0.20,
        "oil": 0.15, "vix": 0.10,
    }
    score = 0.0
    for cat, w in weights.items():
        if cat == "usdkrw":
            score += int(usdkrw_grade) * 25.0 * w
        else:
            sig = signals.get(cat)
            if sig:
                score += int(sig.grade) * 25.0 * w
    return round(min(100.0, score), 1)


def _compute_foreign_outflow_risk(signals: dict[str, ShockSignal],
                                  usdkrw_grade: ShockGrade) -> float:
    """Foreign Outflow Risk Score (0-100).

    외인 이탈 리스크: KORU/EWY 35%, 원달러 25%, VIX 20%, US선물 20%.
    """
    weights = {
        "korea_etf": 0.35, "usdkrw": 0.25, "vix": 0.20, "us_futures": 0.20,
    }
    score = 0.0
    for cat, w in weights.items():
        if cat == "usdkrw":
            score += int(usdkrw_grade) * 25.0 * w
        else:
            sig = signals.get(cat)
            if sig:
                score += int(sig.grade) * 25.0 * w
    return round(min(100.0, score), 1)


# ── 정책 엔진 ──────────────────────────────────────────────

def _build_policy(grade: ShockGrade, dominant: str) -> ShockPolicy:
    """등급별 강제 운영 정책 생성."""
    mgr_keys = ["livermore", "oneil", "lynch", "buffett"]

    if grade == ShockGrade.NONE:
        return ShockPolicy(
            alert_level="GREEN", regime="NEUTRAL",
            position_action={m: "HOLD_ALL" for m in mgr_keys},
        )

    if grade == ShockGrade.WATCH:
        return ShockPolicy(
            alert_level="YELLOW", regime="NEUTRAL",
            position_action={m: "HOLD_ALL" for m in mgr_keys},
            recheck_time="10:00",
        )

    if grade == ShockGrade.ALERT:
        return ShockPolicy(
            new_buy_allowed=True,
            buy_restriction_sectors=SECTOR_SENSITIVITY["HIGH"],
            alert_level="ORANGE", regime="RISK_OFF",
            position_action={
                "livermore": "TIGHTEN_STOP",
                "oneil": "TIGHTEN_STOP",
                "lynch": "HOLD_ALL",
                "buffett": "HOLD_ALL",
            },
            blocked_strategies=["A"],  # 단기반등 차단
            recheck_time="10:00",
            ml_blend_override={
                "active": True,
                "traditional_weight": 0.75,
                "ml_weight": 0.25,
                "reason": "ALERT 레짐: ML 비중 축소, 전통 가중",
            },
        )

    if grade == ShockGrade.SHOCK:
        return ShockPolicy(
            new_buy_allowed=False,
            buy_restriction_sectors=SECTOR_SENSITIVITY["HIGH"] + SECTOR_SENSITIVITY["MEDIUM"],
            alert_level="RED", regime="RISK_OFF",
            position_action={
                "livermore": "REDUCE_30",
                "oneil": "TIGHTEN_STOP",
                "lynch": "TIGHTEN_STOP",
                "buffett": "HOLD_ALL",
            },
            atr_override_to_scalp=True,
            blocked_strategies=["A", "G"],  # 단기반등 + 돌파매매 차단
            recheck_time="10:00",
            ml_blend_override={
                "active": True,
                "traditional_weight": 0.80,
                "ml_weight": 0.20,
                "reason": "SHOCK 레짐: ML 신뢰도 하락, 전통 우선",
            },
        )

    # CRISIS
    return ShockPolicy(
        new_buy_allowed=False,
        buy_restriction_sectors=(
            SECTOR_SENSITIVITY["HIGH"]
            + SECTOR_SENSITIVITY["MEDIUM"]
            + SECTOR_SENSITIVITY["LOW"]
        ),
        alert_level="RED", regime="PANIC",
        position_action={
            "livermore": "CLOSE_ALL",
            "oneil": "REDUCE_30",
            "lynch": "TIGHTEN_STOP",
            "buffett": "HOLD_ALL",
        },
        atr_override_to_scalp=True,
        blocked_strategies=["A", "F", "G", "J"],  # 공격 전략 전면 차단
        recheck_time="09:30",
        ml_blend_override={
            "active": True,
            "traditional_weight": 0.90,
            "ml_weight": 0.10,
            "reason": "CRISIS 레짐: ML 거의 무시, 룰 기반 방어",
        },
    )


# ── 스코어링 가중치 오버라이드 ──────────────────────────────

# 정상시 가중치: macro=10%, flow=15%, fundamental=30%, technical=30%, risk=15%
SHOCK_WEIGHT_OVERRIDES = {
    ShockGrade.NONE: None,  # 기존 레짐 가중치 사용
    ShockGrade.WATCH: None,
    ShockGrade.ALERT: {
        "macro": 0.20, "flow": 0.15, "fundamental": 0.20,
        "technical": 0.20, "risk": 0.25,
    },
    ShockGrade.SHOCK: {
        "macro": 0.25, "flow": 0.10, "fundamental": 0.05,
        "technical": 0.15, "risk": 0.45,
    },
    ShockGrade.CRISIS: {
        "macro": 0.30, "flow": 0.05, "fundamental": 0.05,
        "technical": 0.10, "risk": 0.50,
    },
}


# ── 메인 진입점 ────────────────────────────────────────────

def detect_shock(macro) -> ShockAssessment:
    """MacroSnapshot으로부터 글로벌 매크로 쇼크를 감지한다.

    Args:
        macro: MacroSnapshot 인스턴스 (ingest/macro_client.py)

    Returns:
        ShockAssessment: 등급, 리스크 스코어, 정책 지침
    """
    signals: dict[str, ShockSignal] = {}

    # 1) 원유 (WTI)
    wti_chg = abs(getattr(macro, "wti_change_pct", 0.0))
    oil_grade = _classify_signal("oil", wti_chg)
    signals["oil"] = ShockSignal(
        category="oil", grade=oil_grade,
        value=getattr(macro, "wti_change_pct", 0.0),
        reason=f"WTI {getattr(macro, 'wti_change_pct', 0.0):+.1f}%",
    )

    # 2) 미국 선물 (나스닥 E-mini)
    nq_chg = abs(getattr(macro, "nq_futures_change_pct", 0.0))
    nq_grade = _classify_signal("us_futures", nq_chg)
    signals["us_futures"] = ShockSignal(
        category="us_futures", grade=nq_grade,
        value=getattr(macro, "nq_futures_change_pct", 0.0),
        reason=f"NQ선물 {getattr(macro, 'nq_futures_change_pct', 0.0):+.1f}%",
    )

    # 3) VIX 변화율
    vix_chg = abs(getattr(macro, "vix_change_pct", 0.0))
    vix_grade = _classify_signal("vix", vix_chg)
    signals["vix"] = ShockSignal(
        category="vix", grade=vix_grade,
        value=getattr(macro, "vix_change_pct", 0.0),
        reason=f"VIX {getattr(macro, 'vix', 0.0):.1f} ({getattr(macro, 'vix_change_pct', 0.0):+.1f}%)",
    )

    # 4) DXY 달러인덱스
    dxy_chg = abs(getattr(macro, "dxy_change_pct", 0.0))
    dxy_grade = _classify_signal("dollar", dxy_chg)
    signals["dollar"] = ShockSignal(
        category="dollar", grade=dxy_grade,
        value=getattr(macro, "dxy_change_pct", 0.0),
        reason=f"DXY {getattr(macro, 'dxy_change_pct', 0.0):+.1f}%",
    )

    # 5) 한국 ETF (EWY 우선, 없으면 KORU/3으로 프록시)
    ewy_chg = abs(getattr(macro, "ewy_change_pct", 0.0))
    if ewy_chg > 0:
        etf_val = getattr(macro, "ewy_change_pct", 0.0)
        etf_grade = _classify_signal("korea_etf", ewy_chg)
        etf_reason = f"EWY {etf_val:+.1f}%"
    else:
        koru_chg = getattr(macro, "koru_change_pct", 0.0)
        ewy_proxy = abs(koru_chg) / 3.0  # 3배 레버리지 역산
        etf_grade = _classify_signal("korea_etf", ewy_proxy)
        etf_val = koru_chg
        etf_reason = f"KORU {koru_chg:+.1f}% (EWY 프록시 {koru_chg/3:+.1f}%)"
    signals["korea_etf"] = ShockSignal(
        category="korea_etf", grade=etf_grade,
        value=etf_val, reason=etf_reason,
    )

    # 6) 원달러 (별도 카테고리 — 스코어링에 사용)
    krw_chg = abs(getattr(macro, "usdkrw_change_pct", 0.0))
    krw_grade = _classify_signal("usdkrw", krw_chg)

    # ── 종합 등급 판정 ──
    all_grades = [s.grade for s in signals.values()]
    shock_count = sum(1 for g in all_grades if g >= ShockGrade.SHOCK)
    alert_plus_count = sum(1 for g in all_grades if g >= ShockGrade.ALERT)
    max_grade = max(all_grades) if all_grades else ShockGrade.NONE

    # CRISIS: SHOCK 2개 이상 동시
    if shock_count >= 2:
        overall = ShockGrade.CRISIS
    else:
        overall = max_grade

    # dominant shock 판정
    dominant = "none"
    if overall >= ShockGrade.WATCH:
        dominant = max(signals.values(), key=lambda s: (s.grade, abs(s.value))).category

    # ── 3대 리스크 스코어 ──
    global_score = _compute_global_shock_score(signals)
    korea_score = _compute_korea_open_risk(signals, krw_grade)
    foreign_score = _compute_foreign_outflow_risk(signals, krw_grade)

    # ── 정책 생성 ──
    policy = _build_policy(overall, dominant)

    return ShockAssessment(
        signals=signals,
        overall_grade=overall,
        shock_count=shock_count,
        dominant_shock=dominant,
        global_shock_score=global_score,
        korea_open_risk_score=korea_score,
        foreign_outflow_risk_score=foreign_score,
        policy=policy,
        timestamp=datetime.now(KST),
    )


# ── 텔레그램 포맷 ──────────────────────────────────────────

def format_shock_briefing(assessment: ShockAssessment) -> str:
    """쇼크 평가를 텔레그램 메시지로 포맷."""
    grade = assessment.overall_grade
    label = GRADE_LABELS[grade]
    color = ALERT_COLORS[grade]
    policy = assessment.policy

    lines = [
        f"🌍 글로벌 매크로 [{color}] {label}",
        "━" * 22,
    ]

    # 신호 요약
    for sig in assessment.signals.values():
        if sig.grade >= ShockGrade.WATCH:
            emoji = {ShockGrade.WATCH: "🟡", ShockGrade.ALERT: "🟠",
                     ShockGrade.SHOCK: "🔴", ShockGrade.CRISIS: "🔴"}.get(sig.grade, "⚪")
            lines.append(f"  {emoji} {sig.reason}")

    if grade == ShockGrade.NONE:
        lines.append("  🟢 정상 범위")

    # 3대 리스크 스코어
    lines.append("")
    lines.append(f"📊 Global Shock: {assessment.global_shock_score:.0f}/100")
    lines.append(f"📊 Korea Open Risk: {assessment.korea_open_risk_score:.0f}/100")
    lines.append(f"📊 외인 이탈 Risk: {assessment.foreign_outflow_risk_score:.0f}/100")

    # 운영 지침
    lines.append("")
    lines.append(f"⚡ 운영 레짐: {policy.regime}")

    if not policy.new_buy_allowed:
        lines.append("⛔ 오늘 신규 매수 금지")
    elif policy.buy_restriction_sectors:
        lines.append(f"⚠️ 매수 제한: {', '.join(policy.buy_restriction_sectors[:5])}...")

    if policy.atr_override_to_scalp:
        lines.append("🔧 전 매니저 손절 스캘프 수준 강제")

    if policy.blocked_strategies:
        names = {"A": "단기반등", "F": "모멘텀", "G": "돌파매매", "J": "역전돌파"}
        blocked = [f"{s}({names.get(s, s)})" for s in policy.blocked_strategies]
        lines.append(f"🚫 차단 전략: {', '.join(blocked)}")

    # 매니저별 지침
    action_labels = {
        "HOLD_ALL": "유지", "TIGHTEN_STOP": "손절강화",
        "REDUCE_30": "30%축소", "CLOSE_ALL": "전량청산",
    }
    if any(v != "HOLD_ALL" for v in policy.position_action.values()):
        lines.append("")
        mgr_names = {
            "livermore": "리버모어", "oneil": "오닐",
            "lynch": "린치", "buffett": "버핏",
        }
        for mgr, action in policy.position_action.items():
            if action != "HOLD_ALL":
                lines.append(f"  {mgr_names.get(mgr, mgr)}: {action_labels.get(action, action)}")

    if policy.recheck_time:
        lines.append(f"\n🔄 {policy.recheck_time} 장중 재평가 예정")

    return "\n".join(lines)
