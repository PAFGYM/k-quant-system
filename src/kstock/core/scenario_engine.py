"""Scenario analysis engine with cascade effects, probability weighting,
custom scenario builder, and recovery path prediction.

Extends the base scenario_analyzer with:
- Cascade effect propagation (up to 3 stages with damping)
- Probability-weighted stress testing
- Custom scenario builder
- V/U/L/W recovery path prediction
- Telegram-formatted output

Rules:
- Korean messages, personalized for the user
- No Markdown parse_mode, plain text + emoji
- try-except wrappers, dataclasses, logging
- numpy/scipy for numerical computation
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CascadeEffect:
    """A single cascade propagation rule."""

    trigger: str = ""
    target: str = ""
    lag_days: int = 0
    transmission_pct: float = 0.0
    description: str = ""


@dataclass
class ScenarioDef:
    """A complete scenario definition."""

    name: str = ""
    description: str = ""
    probability: float = 0.0
    shocks: dict[str, float] = field(default_factory=dict)
    duration_days: int = 30
    cascade_effects: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Result of running one scenario against a portfolio."""

    scenario: ScenarioDef = field(default_factory=ScenarioDef)
    portfolio_impact_pct: float = 0.0
    sector_impacts: dict[str, float] = field(default_factory=dict)
    worst_case_pct: float = 0.0
    best_case_pct: float = 0.0
    var_under_scenario: float = 0.0
    recovery_days: int = 0
    mitigation_actions: list[str] = field(default_factory=list)


@dataclass
class RecoveryPath:
    """Predicted recovery trajectory after a scenario shock."""

    scenario_name: str = ""
    recovery_type: str = "U"  # "V" / "U" / "L" / "W"
    expected_days: int = 60
    confidence_interval: tuple[int, int] = (30, 90)
    cumulative_path: list[float] = field(default_factory=list)
    key_indicators: list[str] = field(default_factory=list)


@dataclass
class StressTestSuite:
    """Aggregated result of running all scenarios."""

    scenarios: list[ScenarioResult] = field(default_factory=list)
    worst_scenario: str = ""
    expected_loss_weighted: float = 0.0
    portfolio_resilience_score: float = 0.0
    diversification_benefit_pct: float = 0.0


# ---------------------------------------------------------------------------
# Built-in scenario definitions (existing 5 + 8 new)
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict[str, Any]] = {
    # --- existing ---
    "trump_tariff": {
        "name": "트럼프 관세 50% 인상",
        "description": "미국의 대중국/대한국 관세 대폭 인상 시나리오",
        "probability": 0.20,
        "shocks": {
            "2차전지": -0.15,
            "자동차": -0.20,
            "반도체": -0.08,
            "방산": 0.05,
        },
        "duration_days": 90,
        "cascade_effects": [
            {"trigger": "자동차", "target": "2차전지", "lag": 10, "transmission": 0.4,
             "description": "자동차 수출 감소 -> 배터리 수요 감소"},
        ],
        "tags": ["무역", "관세", "미국"],
    },
    "rate_cut": {
        "name": "기준금리 0.25%p 인하",
        "description": "한국은행 기준금리 인하 시나리오",
        "probability": 0.35,
        "shocks": {
            "2차전지": 0.08,
            "자동차": 0.05,
            "반도체": 0.10,
            "건설": 0.12,
            "금융": -0.05,
        },
        "duration_days": 60,
        "cascade_effects": [],
        "tags": ["금리", "한은", "통화정책"],
    },
    "msci_inclusion": {
        "name": "MSCI 코스닥 편입",
        "description": "MSCI 신흥시장 코스닥 편입으로 외국인 자금 유입",
        "probability": 0.15,
        "shocks": {
            "바이오": 0.15,
            "소프트웨어": 0.12,
            "2차전지": 0.20,
        },
        "duration_days": 30,
        "cascade_effects": [],
        "tags": ["MSCI", "외국인", "코스닥"],
    },
    "market_crash": {
        "name": "코로나급 폭락 재현",
        "description": "글로벌 팬데믹 수준의 시장 폭락",
        "probability": 0.05,
        "shocks": {
            "전체": -0.35,
        },
        "duration_days": 120,
        "cascade_effects": [],
        "tags": ["폭락", "글로벌"],
    },
    "won_crisis": {
        "name": "원화 급락 위기",
        "description": "원/달러 환율 급등 (원화 약세) 시나리오",
        "probability": 0.10,
        "shocks": {
            "수출": 0.08,
            "수입": -0.12,
            "내수": -0.10,
            "반도체": 0.05,
        },
        "duration_days": 60,
        "cascade_effects": [
            {"trigger": "수입", "target": "내수", "lag": 15, "transmission": 0.3,
             "description": "수입 원가 상승 -> 내수 소비 위축"},
        ],
        "tags": ["환율", "원화", "통화"],
    },
    # --- new 8 scenarios ---
    "china_slowdown": {
        "name": "중국 경기 둔화",
        "description": "중국 GDP 성장률 급락으로 한국 수출 타격",
        "probability": 0.15,
        "shocks": {
            "반도체": -0.20,
            "화학": -0.15,
            "조선": -0.10,
            "철강": -0.12,
        },
        "duration_days": 180,
        "cascade_effects": [
            {"trigger": "반도체", "target": "IT부품", "lag": 10, "transmission": 0.5,
             "description": "반도체 수출 감소 -> IT부품 수요 감소"},
            {"trigger": "화학", "target": "소재", "lag": 7, "transmission": 0.4,
             "description": "화학 수요 감소 -> 소재 가격 하락"},
        ],
        "tags": ["중국", "수출", "둔화"],
    },
    "fed_hawkish_surprise": {
        "name": "Fed 매파적 서프라이즈",
        "description": "미 연준 예상외 금리 인상 또는 인하 지연",
        "probability": 0.10,
        "shocks": {
            "성장주": -0.15,
            "금융": 0.05,
            "2차전지": -0.10,
            "바이오": -0.12,
        },
        "duration_days": 60,
        "cascade_effects": [
            {"trigger": "성장주", "target": "2차전지", "lag": 5, "transmission": 0.5,
             "description": "성장주 매도 -> 2차전지 연쇄 하락"},
        ],
        "tags": ["Fed", "금리", "미국"],
    },
    "oil_shock": {
        "name": "유가 급등 쇼크",
        "description": "중동 지정학 리스크로 유가 급등",
        "probability": 0.08,
        "shocks": {
            "운송": -0.12,
            "에너지": 0.20,
            "화학": -0.08,
            "항공": -0.15,
        },
        "duration_days": 90,
        "cascade_effects": [
            {"trigger": "운송", "target": "내수", "lag": 20, "transmission": 0.3,
             "description": "물류비 상승 -> 내수 소비 위축"},
        ],
        "tags": ["유가", "중동", "에너지"],
    },
    "tech_bubble_burst": {
        "name": "기술주 버블 붕괴",
        "description": "AI/기술주 과열 후 급락",
        "probability": 0.05,
        "shocks": {
            "IT": -0.30,
            "바이오": -0.20,
            "반도체": -0.25,
            "소프트웨어": -0.22,
        },
        "duration_days": 150,
        "cascade_effects": [
            {"trigger": "IT", "target": "반도체", "lag": 3, "transmission": 0.6,
             "description": "IT 매도세 -> 반도체 동반 하락"},
            {"trigger": "반도체", "target": "IT부품", "lag": 7, "transmission": 0.5,
             "description": "반도체 하락 -> IT부품 수요 급감"},
        ],
        "tags": ["기술주", "버블", "AI"],
    },
    "pandemic_v2": {
        "name": "신종 팬데믹 발생",
        "description": "신종 감염병 확산으로 글로벌 봉쇄 재현",
        "probability": 0.03,
        "shocks": {
            "항공": -0.40,
            "바이오": 0.30,
            "엔터": -0.25,
            "여행": -0.35,
            "내수": -0.15,
        },
        "duration_days": 180,
        "cascade_effects": [
            {"trigger": "항공", "target": "여행", "lag": 2, "transmission": 0.8,
             "description": "항공 중단 -> 여행 업종 동반 타격"},
            {"trigger": "내수", "target": "건설", "lag": 30, "transmission": 0.4,
             "description": "소비 위축 -> 건설/부동산 둔화"},
        ],
        "tags": ["팬데믹", "봉쇄", "글로벌"],
    },
    "japan_yen_crisis": {
        "name": "일본 엔화 위기",
        "description": "엔저 심화로 한국 수출 경쟁력 약화",
        "probability": 0.10,
        "shocks": {
            "수출": -0.10,
            "자동차": -0.08,
            "조선": -0.05,
            "USDKRW": 0.05,
        },
        "duration_days": 90,
        "cascade_effects": [
            {"trigger": "수출", "target": "자동차", "lag": 10, "transmission": 0.4,
             "description": "수출 경쟁 심화 -> 자동차 수익성 악화"},
        ],
        "tags": ["엔화", "일본", "환율"],
    },
    "ai_disruption": {
        "name": "AI 산업 혁신 가속",
        "description": "AI 기술 도입 가속으로 산업 구조 전환",
        "probability": 0.20,
        "shocks": {
            "IT": 0.15,
            "전통제조": -0.10,
            "반도체": 0.12,
            "소프트웨어": 0.18,
        },
        "duration_days": 120,
        "cascade_effects": [
            {"trigger": "IT", "target": "반도체", "lag": 5, "transmission": 0.5,
             "description": "AI 수요 증가 -> 반도체 수요 견인"},
        ],
        "tags": ["AI", "기술", "혁신"],
    },
    "geopolitical_escalation": {
        "name": "지정학적 긴장 고조",
        "description": "한반도/동아시아 지정학 리스크 급등",
        "probability": 0.08,
        "shocks": {
            "방산": 0.25,
            "반도체": -0.15,
            "전체": -0.10,
        },
        "duration_days": 60,
        "cascade_effects": [
            {"trigger": "전체", "target": "건설", "lag": 15, "transmission": 0.5,
             "description": "시장 불안 -> 건설/투자 위축"},
        ],
        "tags": ["지정학", "안보", "한반도"],
    },
}

# ---------------------------------------------------------------------------
# Mitigation templates per tag
# ---------------------------------------------------------------------------

_MITIGATION_MAP: dict[str, list[str]] = {
    "관세": [
        "관세 비영향 내수주 비중 확대",
        "미국 현지 생산 비중 높은 종목 선별",
    ],
    "무역": [
        "수출 의존도 낮은 종목으로 rotation",
        "환헤지 비중 점검",
    ],
    "금리": [
        "듀레이션 짧은 자산 비중 확대",
        "배당주/가치주 비중 점검",
    ],
    "폭락": [
        "현금 비중 30% 이상 확보",
        "인버스 ETF (KODEX 200선물인버스2X) 헤지 10~15%",
        "방어주 (통신/유틸리티) 비중 확대",
    ],
    "환율": [
        "수출주 비중 조정 (원화 약세 수혜)",
        "달러 자산 확보 검토",
    ],
    "중국": [
        "대중국 수출 비중 낮은 종목 선별",
        "내수 소비주 비중 확대",
    ],
    "Fed": [
        "성장주 비중 축소, 가치주 전환",
        "현금성 자산 비중 확대",
    ],
    "유가": [
        "에너지 수혜주 비중 확대",
        "운송비 민감 종목 축소",
    ],
    "버블": [
        "기술주 비중 30% 이하로 제한",
        "밸류에이션 과열 종목 이익실현",
    ],
    "기술주": [
        "기술주 비중 30% 이하로 제한",
        "밸류에이션 과열 종목 이익실현",
    ],
    "팬데믹": [
        "언택트/바이오 종목 비중 확대",
        "오프라인 의존 종목 비중 축소",
    ],
    "엔화": [
        "일본 경쟁 노출 종목 비중 축소",
        "환율 방어 포지션 검토",
    ],
    "AI": [
        "AI 수혜주 적극 편입",
        "전통 제조업 비중 점검",
    ],
    "지정학": [
        "방산주 비중 확대",
        "외국인 순매도 종목 주의",
        "안전자산 (금/달러) 비중 확대",
    ],
    "안보": [
        "방산주 비중 확대",
        "해외 분산 투자 검토",
    ],
}


# ---------------------------------------------------------------------------
# Helper: parse cascade rules into CascadeEffect objects
# ---------------------------------------------------------------------------

def _parse_cascade_effects(raw: list[dict]) -> list[CascadeEffect]:
    """Convert raw cascade dicts to CascadeEffect dataclasses."""
    effects: list[CascadeEffect] = []
    for r in raw:
        effects.append(CascadeEffect(
            trigger=r.get("trigger", ""),
            target=r.get("target", ""),
            lag_days=r.get("lag", r.get("lag_days", 0)),
            transmission_pct=r.get("transmission", r.get("transmission_pct", 0.0)),
            description=r.get("description", ""),
        ))
    return effects


def _scenario_from_dict(key: str, d: dict[str, Any]) -> ScenarioDef:
    """Build a ScenarioDef from the SCENARIOS dict entry."""
    return ScenarioDef(
        name=d.get("name", key),
        description=d.get("description", ""),
        probability=d.get("probability", 0.5),
        shocks=dict(d.get("shocks", {})),
        duration_days=d.get("duration_days", 30),
        cascade_effects=list(d.get("cascade_effects", [])),
        tags=list(d.get("tags", [])),
    )


# ---------------------------------------------------------------------------
# 1. build_custom_scenario
# ---------------------------------------------------------------------------

def build_custom_scenario(
    name: str,
    shocks: dict[str, float],
    probability: float = 0.5,
    cascade_rules: list[dict] | None = None,
    description: str = "",
    duration_days: int = 30,
    tags: list[str] | None = None,
) -> ScenarioDef:
    """Create a user-defined scenario.

    Args:
        name: Scenario name.
        shocks: {sector: impact_pct} e.g. {"반도체": -0.20}.
        probability: Occurrence probability [0, 1].
        cascade_rules: Optional list of cascade dicts, e.g.
            [{"trigger": "반도체", "target": "IT부품", "lag": 5, "transmission": 0.6}]
        description: Optional description text.
        duration_days: Expected duration.
        tags: Classification tags.

    Returns:
        ScenarioDef with validated fields.
    """
    try:
        prob = max(0.0, min(1.0, probability))
        if prob != probability:
            logger.warning("probability 범위 조정: %.4f -> %.4f", probability, prob)

        cascade_effects = cascade_rules or []

        scenario = ScenarioDef(
            name=name,
            description=description or f"사용자 정의 시나리오: {name}",
            probability=prob,
            shocks=dict(shocks),
            duration_days=max(1, duration_days),
            cascade_effects=cascade_effects,
            tags=tags or [],
        )

        logger.info("커스텀 시나리오 생성: %s (확률=%.1f%%, 충격=%d개)",
                     name, prob * 100, len(shocks))
        return scenario

    except Exception as e:
        logger.error("커스텀 시나리오 생성 실패: %s", e, exc_info=True)
        return ScenarioDef(name=name, shocks=dict(shocks))


# ---------------------------------------------------------------------------
# 2. compute_cascade_effects
# ---------------------------------------------------------------------------

def compute_cascade_effects(
    scenario: ScenarioDef,
    sector_map: dict[str, str] | None = None,
    max_depth: int = 3,
) -> dict[str, float]:
    """Propagate cascade effects up to *max_depth* stages.

    Propagation formula per stage:
        impact_next = impact_current * transmission_pct * exp(-lag / 30)

    Args:
        scenario: The scenario definition.
        sector_map: Optional ticker-to-sector mapping (unused for pure
            sector-level propagation, but kept for API symmetry).
        max_depth: Maximum cascade depth (default 3).

    Returns:
        {sector: total_impact} including both direct shocks and cascade.
    """
    try:
        # Start with direct shocks
        impacts: dict[str, float] = dict(scenario.shocks)

        if not scenario.cascade_effects:
            return impacts

        effects = _parse_cascade_effects(scenario.cascade_effects)

        # Iterative propagation up to max_depth
        for depth in range(max_depth):
            new_impacts: dict[str, float] = {}
            damping = 1.0 / (depth + 1)  # additional damping per stage

            for effect in effects:
                trigger_impact = impacts.get(effect.trigger, 0.0)
                if abs(trigger_impact) < 1e-8:
                    continue

                lag_factor = math.exp(-effect.lag_days / 30.0)
                propagated = (
                    trigger_impact
                    * effect.transmission_pct
                    * lag_factor
                    * damping
                )

                if abs(propagated) < 1e-6:
                    continue

                target = effect.target
                new_impacts[target] = new_impacts.get(target, 0.0) + propagated

            # Merge new impacts
            added_any = False
            for sector, delta in new_impacts.items():
                old = impacts.get(sector, 0.0)
                impacts[sector] = old + delta
                if abs(delta) > 1e-8:
                    added_any = True

            if not added_any:
                break  # No further propagation

        logger.info("연쇄효과 계산 완료: %d개 섹터 영향", len(impacts))
        return impacts

    except Exception as e:
        logger.error("연쇄효과 계산 실패: %s", e, exc_info=True)
        return dict(scenario.shocks)


# ---------------------------------------------------------------------------
# 3. run_scenario
# ---------------------------------------------------------------------------

def _generate_mitigations(scenario: ScenarioDef, impact_pct: float) -> list[str]:
    """Generate mitigation actions based on scenario tags and impact severity."""
    actions: list[str] = []

    # Severity-based generic actions
    if impact_pct < -0.20:
        actions.append("현금 비중 30% 이상 즉시 확보")
        actions.append("전체 포지션 50% 축소 검토")
    elif impact_pct < -0.10:
        actions.append("현금 비중 20% 확보")
        actions.append("취약 섹터 비중 점진적 축소")
    elif impact_pct < -0.05:
        actions.append("취약 섹터 비중 축소 검토")
    elif impact_pct > 0.05:
        actions.append("수혜 섹터 비중 확대 검토")

    # Tag-specific actions
    seen: set[str] = set()
    for tag in scenario.tags:
        for action in _MITIGATION_MAP.get(tag, []):
            if action not in seen:
                actions.append(action)
                seen.add(action)

    if not actions:
        actions.append("현재 포트폴리오 유지, 모니터링 강화")

    return actions


def run_scenario(
    scenario: ScenarioDef,
    portfolio: dict[str, dict],
    market_data: dict | None = None,
) -> ScenarioResult:
    """Run a single scenario against a portfolio.

    Args:
        scenario: The scenario definition.
        portfolio: {ticker: {"weight": float, "sector": str, "beta": float}}.
        market_data: Optional market context (unused in v1, reserved).

    Returns:
        ScenarioResult with impact analysis.
    """
    try:
        # Compute sector-level impacts including cascade
        sector_impacts = compute_cascade_effects(scenario)

        # Portfolio impact
        portfolio_impact = 0.0
        sector_vols: dict[str, list[float]] = {}

        for ticker, info in portfolio.items():
            weight = info.get("weight", 0.0)
            sector = info.get("sector", "")
            beta = info.get("beta", 1.0)

            # Look up sector shock: exact match, then "전체" fallback
            shock = sector_impacts.get(sector, sector_impacts.get("전체", 0.0))

            holding_impact = weight * shock * beta
            portfolio_impact += holding_impact

            # Collect for volatility estimation
            if sector not in sector_vols:
                sector_vols[sector] = []
            sector_vols[sector].append(abs(shock))

        # Estimate sector volatility (simple: use abs shock as proxy)
        avg_vol = 0.0
        if sector_vols:
            all_shocks = [v for vs in sector_vols.values() for v in vs]
            avg_vol = float(np.mean(all_shocks)) if all_shocks else 0.0

        # worst / best case
        worst_case = portfolio_impact * (1.0 + 2.0 * avg_vol) if portfolio_impact < 0 else portfolio_impact
        best_case = portfolio_impact * 0.5

        # Ensure worst <= best logically (worst is more negative)
        if worst_case > best_case:
            worst_case, best_case = best_case, worst_case

        # Simplified scenario VaR (parametric, 95%)
        var_under_scenario = abs(portfolio_impact) * 1.645

        # Estimate recovery days from duration
        recovery_days = max(
            int(scenario.duration_days * (0.5 + abs(portfolio_impact))),
            1,
        )

        # Generate mitigations
        mitigations = _generate_mitigations(scenario, portfolio_impact)

        result = ScenarioResult(
            scenario=scenario,
            portfolio_impact_pct=round(portfolio_impact, 6),
            sector_impacts=sector_impacts,
            worst_case_pct=round(worst_case, 6),
            best_case_pct=round(best_case, 6),
            var_under_scenario=round(var_under_scenario, 6),
            recovery_days=recovery_days,
            mitigation_actions=mitigations,
        )

        logger.info(
            "시나리오 [%s] 실행: impact=%.2f%%, worst=%.2f%%, best=%.2f%%",
            scenario.name,
            portfolio_impact * 100,
            worst_case * 100,
            best_case * 100,
        )
        return result

    except Exception as e:
        logger.error("시나리오 실행 실패 [%s]: %s", scenario.name, e, exc_info=True)
        return ScenarioResult(scenario=scenario)


# ---------------------------------------------------------------------------
# 4. predict_recovery_path
# ---------------------------------------------------------------------------

_RECOVERY_INDICATORS: dict[str, list[str]] = {
    "V": ["VIX 급락", "외국인 순매수 전환", "주요국 통화정책 완화"],
    "U": ["기업 실적 바닥 확인", "재고 사이클 반전", "소비자 심리지수 반등"],
    "L": ["구조적 산업 변화", "신용 경색 지속", "정책 실효성 부재"],
    "W": ["2차 충격 우려", "정책 불확실성", "외부 변수 재발"],
}


def predict_recovery_path(
    scenario: ScenarioDef,
    historical_drawdowns: list[dict] | None = None,
) -> RecoveryPath:
    """Predict recovery type and trajectory after a scenario shock.

    Classification criteria (based on historical patterns):
    - V-shape: duration <= 45 days, moderate shock (< 20%)
    - U-shape: duration 45~120 days, or moderate-severe shock
    - W-shape: high uncertainty scenarios (probability < 0.08)
    - L-shape: duration > 120 days, severe shock (>= 30%)

    Args:
        scenario: The scenario that occurred.
        historical_drawdowns: Optional list of
            {"name": str, "drawdown_pct": float, "recovery_days": int, "type": str}.

    Returns:
        RecoveryPath with predicted trajectory.
    """
    try:
        # Determine maximum shock magnitude
        max_shock = max((abs(v) for v in scenario.shocks.values()), default=0.0)
        duration = scenario.duration_days

        # If historical data provided, use similarity matching
        if historical_drawdowns:
            return _predict_from_history(scenario, historical_drawdowns)

        # Rule-based classification
        if max_shock >= 0.30 and duration > 120:
            rtype = "L"
            expected_days = max(duration, 180)
            ci = (120, max(360, expected_days * 2))
        elif scenario.probability < 0.08 and max_shock >= 0.15:
            rtype = "W"
            expected_days = int(duration * 1.5)
            ci = (int(expected_days * 0.7), int(expected_days * 2.0))
        elif duration <= 45 and max_shock < 0.20:
            rtype = "V"
            expected_days = max(int(duration * 0.8), 10)
            ci = (max(expected_days - 10, 5), expected_days + 20)
        else:
            rtype = "U"
            expected_days = int(duration * 1.2)
            ci = (int(expected_days * 0.6), int(expected_days * 1.5))

        # Generate cumulative recovery path
        path = _generate_cumulative_path(rtype, max_shock, expected_days)

        recovery = RecoveryPath(
            scenario_name=scenario.name,
            recovery_type=rtype,
            expected_days=expected_days,
            confidence_interval=ci,
            cumulative_path=path,
            key_indicators=list(_RECOVERY_INDICATORS.get(rtype, [])),
        )

        logger.info(
            "회복 경로 예측 [%s]: %s-shape, %d일 예상",
            scenario.name, rtype, expected_days,
        )
        return recovery

    except Exception as e:
        logger.error("회복 경로 예측 실패: %s", e, exc_info=True)
        return RecoveryPath(
            scenario_name=scenario.name,
            recovery_type="U",
            expected_days=60,
            confidence_interval=(30, 90),
        )


def _predict_from_history(
    scenario: ScenarioDef,
    historical: list[dict],
) -> RecoveryPath:
    """Match scenario to most similar historical event."""
    max_shock = max((abs(v) for v in scenario.shocks.values()), default=0.0)

    # Find most similar by drawdown magnitude
    best_match: dict | None = None
    best_dist = float("inf")

    for event in historical:
        dd_pct = abs(event.get("drawdown_pct", 0.0))
        dist = abs(dd_pct - max_shock)
        if dist < best_dist:
            best_dist = dist
            best_match = event

    if best_match is None:
        rtype = "U"
        expected_days = 60
    else:
        rtype = best_match.get("type", "U")
        expected_days = max(best_match.get("recovery_days", 60), 1)

    ci_low = max(int(expected_days * 0.6), 1)
    ci_high = int(expected_days * 1.5)
    path = _generate_cumulative_path(rtype, max_shock, expected_days)

    return RecoveryPath(
        scenario_name=scenario.name,
        recovery_type=rtype,
        expected_days=expected_days,
        confidence_interval=(ci_low, ci_high),
        cumulative_path=path,
        key_indicators=list(_RECOVERY_INDICATORS.get(rtype, [])),
    )


def _generate_cumulative_path(
    rtype: str,
    max_shock: float,
    expected_days: int,
) -> list[float]:
    """Generate a daily cumulative recovery path (percentage from bottom).

    Returns:
        List of floats representing cumulative return from trough, e.g.
        [-10.0, -8.0, -5.0, -2.0, 0.0, +2.0, ...] in percent.
    """
    n = max(expected_days, 5)
    t = np.linspace(0, 1, n)
    shock_pct = -max_shock * 100  # e.g. -20.0

    if rtype == "V":
        # Rapid symmetric recovery
        curve = shock_pct * (1.0 - 2.0 * t)
        curve = np.clip(curve, shock_pct, -shock_pct * 0.5)
    elif rtype == "U":
        # Flat bottom then gradual recovery
        flat_end = 0.3
        ratio = np.maximum((t - flat_end) / (1.0 - flat_end), 0.0)
        curve = np.where(
            t < flat_end,
            shock_pct,
            shock_pct * (1.0 - ratio ** 0.8),
        )
    elif rtype == "L":
        # Very slow partial recovery
        curve = shock_pct * (1.0 - 0.3 * t)
    elif rtype == "W":
        # Double dip
        curve = shock_pct * (
            1.0
            - 1.5 * np.sin(np.pi * t) * (1.0 - 0.5 * np.sin(2.0 * np.pi * t))
        )
        curve = np.clip(curve, shock_pct * 1.3, -shock_pct)
    else:
        curve = shock_pct * (1.0 - t)

    return [round(float(v), 2) for v in curve]


# ---------------------------------------------------------------------------
# 5. run_stress_test_suite
# ---------------------------------------------------------------------------

def run_stress_test_suite(
    portfolio: dict[str, dict],
    scenarios: dict[str, dict[str, Any]] | None = None,
    market_data: dict | None = None,
) -> StressTestSuite:
    """Run all scenarios and compute aggregate metrics.

    Args:
        portfolio: {ticker: {"weight": float, "sector": str, "beta": float}}.
        scenarios: Override built-in SCENARIOS dict. Defaults to SCENARIOS.
        market_data: Optional market context.

    Returns:
        StressTestSuite with all results and aggregate metrics.
    """
    try:
        scen_dict = scenarios if scenarios is not None else SCENARIOS
        results: list[ScenarioResult] = []

        for key, raw in scen_dict.items():
            sdef = _scenario_from_dict(key, raw)
            result = run_scenario(sdef, portfolio, market_data)
            results.append(result)

        if not results:
            return StressTestSuite()

        # Expected loss (probability-weighted)
        expected_loss = sum(
            r.scenario.probability * r.portfolio_impact_pct
            for r in results
            if r.portfolio_impact_pct < 0
        )

        # Worst scenario
        worst = min(results, key=lambda r: r.portfolio_impact_pct)
        worst_name = worst.scenario.name

        # Max possible loss (worst of all without probability weighting)
        max_loss = min(r.portfolio_impact_pct for r in results)

        # Resilience score: 1 - (expected_loss / max_loss), clamped [0, 1]
        if abs(max_loss) > 1e-8:
            resilience = 1.0 - (expected_loss / max_loss)
            resilience = max(0.0, min(1.0, resilience))
        else:
            resilience = 1.0

        # Diversification benefit: undiversified - diversified
        # undiversified = sum(abs(negative impacts)) probability weighted
        # diversified = abs(expected_loss)
        undiversified_loss = sum(
            r.scenario.probability * abs(r.portfolio_impact_pct)
            for r in results
            if r.portfolio_impact_pct < 0
        )
        diversified_loss = abs(expected_loss)
        div_benefit = (
            (undiversified_loss - diversified_loss) / undiversified_loss * 100
            if undiversified_loss > 1e-8
            else 0.0
        )

        suite = StressTestSuite(
            scenarios=results,
            worst_scenario=worst_name,
            expected_loss_weighted=round(expected_loss, 6),
            portfolio_resilience_score=round(resilience, 4),
            diversification_benefit_pct=round(div_benefit, 2),
        )

        logger.info(
            "스트레스 테스트 완료: %d개 시나리오, 가중손실=%.2f%%, 복원력=%.2f",
            len(results),
            expected_loss * 100,
            resilience,
        )
        return suite

    except Exception as e:
        logger.error("스트레스 테스트 실패: %s", e, exc_info=True)
        return StressTestSuite()


# ---------------------------------------------------------------------------
# 6 & 7. Formatting (Telegram plain text)
# ---------------------------------------------------------------------------

def format_scenario_result(result: ScenarioResult) -> str:
    """Format a single scenario result for Telegram (plain text)."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        s = result.scenario

        lines = [
            f"[시나리오 분석] {now}",
            f"{USER_NAME}, '{s.name}' 분석 결과입니다.",
            "",
            f"  확률: {s.probability * 100:.0f}%",
            f"  포트폴리오 영향: {result.portfolio_impact_pct * 100:+.2f}%",
            f"  최악: {result.worst_case_pct * 100:+.2f}%",
            f"  최선: {result.best_case_pct * 100:+.2f}%",
            f"  시나리오 VaR(95%): {result.var_under_scenario * 100:.2f}%",
            f"  예상 회복: {result.recovery_days}일",
            "",
        ]

        # Sector impacts
        if result.sector_impacts:
            lines.append("-- 섹터별 영향 --")
            sorted_sectors = sorted(
                result.sector_impacts.items(),
                key=lambda x: x[1],
            )
            for sector, impact in sorted_sectors:
                arrow = "+" if impact > 0 else ""
                lines.append(f"  {sector}: {arrow}{impact * 100:.1f}%")
            lines.append("")

        # Mitigations
        if result.mitigation_actions:
            lines.append("-- 대응 전략 --")
            for i, action in enumerate(result.mitigation_actions, 1):
                lines.append(f"  {i}. {action}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("시나리오 결과 포맷 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 시나리오 결과 생성 중 오류가 발생했습니다."


def format_stress_test(suite: StressTestSuite) -> str:
    """Format stress test suite results for Telegram (plain text)."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")

        lines = [
            f"[스트레스 테스트] {now}",
            f"{USER_NAME}, {len(suite.scenarios)}개 시나리오 분석 결과입니다.",
            "",
            f"  가중 예상 손실: {suite.expected_loss_weighted * 100:+.2f}%",
            f"  포트폴리오 복원력: {suite.portfolio_resilience_score:.2f} / 1.00",
            f"  분산투자 효과: {suite.diversification_benefit_pct:.1f}%",
            f"  최악 시나리오: {suite.worst_scenario}",
            "",
            "-- 시나리오별 요약 --",
        ]

        # Sort by impact (worst first)
        sorted_results = sorted(
            suite.scenarios,
            key=lambda r: r.portfolio_impact_pct,
        )

        for r in sorted_results:
            impact = r.portfolio_impact_pct * 100
            prob = r.scenario.probability * 100
            name = r.scenario.name
            lines.append(f"  {name} (확률 {prob:.0f}%): {impact:+.2f}%")

        return "\n".join(lines)

    except Exception as e:
        logger.error("스트레스 테스트 포맷 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 스트레스 테스트 결과 생성 중 오류가 발생했습니다."
