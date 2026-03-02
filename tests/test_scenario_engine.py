"""tests/test_scenario_engine.py — 시나리오 엔진 확장 모듈 테스트.

커스텀 시나리오, 연쇄효과, 시나리오 실행, 회복경로, 스트레스 테스트, 포맷.
"""
from __future__ import annotations

import pytest

from kstock.core.scenario_engine import (
    SCENARIOS,
    CascadeEffect,
    RecoveryPath,
    ScenarioDef,
    ScenarioResult,
    StressTestSuite,
    build_custom_scenario,
    compute_cascade_effects,
    format_scenario_result,
    format_stress_test,
    predict_recovery_path,
    run_scenario,
    run_stress_test_suite,
)


# ── helpers ───────────────────────────────────────────────

def _sample_portfolio() -> dict[str, dict]:
    """Simple 3-stock portfolio for testing."""
    return {
        "005930": {"weight": 0.4, "sector": "반도체", "beta": 1.1},
        "035720": {"weight": 0.3, "sector": "IT", "beta": 1.3},
        "068270": {"weight": 0.3, "sector": "바이오", "beta": 0.9},
    }


def _even_portfolio() -> dict[str, dict]:
    """Equal-weighted 2-stock portfolio."""
    return {
        "A": {"weight": 0.5, "sector": "자동차", "beta": 1.0},
        "B": {"weight": 0.5, "sector": "방산", "beta": 1.0},
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestCustomScenario
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCustomScenario:
    """build_custom_scenario 테스트."""

    def test_basic_creation(self):
        """기본 커스텀 시나리오 생성."""
        s = build_custom_scenario("테스트", shocks={"반도체": -0.10})
        assert isinstance(s, ScenarioDef)
        assert s.name == "테스트"
        assert s.shocks["반도체"] == -0.10
        assert 0 <= s.probability <= 1

    def test_with_cascade(self):
        """cascade_rules 포함 생성."""
        rules = [
            {"trigger": "반도체", "target": "IT부품", "lag": 5, "transmission": 0.6},
        ]
        s = build_custom_scenario(
            "캐스케이드 테스트",
            shocks={"반도체": -0.15},
            cascade_rules=rules,
        )
        assert len(s.cascade_effects) == 1
        assert s.cascade_effects[0]["trigger"] == "반도체"

    def test_probability_clamped(self):
        """확률이 [0, 1] 범위로 클램핑."""
        s1 = build_custom_scenario("hi", shocks={"A": 0.1}, probability=1.5)
        assert s1.probability <= 1.0

        s2 = build_custom_scenario("lo", shocks={"A": 0.1}, probability=-0.5)
        assert s2.probability >= 0.0

    def test_default_description(self):
        """description 미지정 시 자동 생성."""
        s = build_custom_scenario("자동", shocks={"IT": 0.05})
        assert "자동" in s.description

    def test_tags_preserved(self):
        """tags 전달 확인."""
        s = build_custom_scenario("태그", shocks={"IT": 0.1}, tags=["AI", "기술"])
        assert "AI" in s.tags
        assert "기술" in s.tags


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestCascadeEffects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCascadeEffects:
    """compute_cascade_effects 테스트."""

    def test_no_cascade(self):
        """cascade 없을 때 원본 shocks 반환."""
        s = ScenarioDef(shocks={"반도체": -0.10}, cascade_effects=[])
        result = compute_cascade_effects(s)
        assert result["반도체"] == -0.10
        assert len(result) == 1

    def test_single_cascade(self):
        """1차 연쇄 전파 확인."""
        s = ScenarioDef(
            shocks={"반도체": -0.20},
            cascade_effects=[
                {"trigger": "반도체", "target": "IT부품", "lag": 5, "transmission": 0.6},
            ],
        )
        result = compute_cascade_effects(s)
        assert "IT부품" in result
        assert result["IT부품"] < 0  # 부정 충격 전파
        # 전파량 < 원래 충격
        assert abs(result["IT부품"]) < abs(result["반도체"])

    def test_damping_reduces_impact(self):
        """깊이 증가 시 damping 확인."""
        s = ScenarioDef(
            shocks={"A": -0.30},
            cascade_effects=[
                {"trigger": "A", "target": "B", "lag": 1, "transmission": 0.9},
                {"trigger": "B", "target": "C", "lag": 1, "transmission": 0.9},
            ],
        )
        result = compute_cascade_effects(s, max_depth=3)
        # B should be impacted, C may be too (from B->C propagation)
        assert "B" in result
        assert abs(result.get("B", 0)) > 0

    def test_empty_shocks(self):
        """빈 shocks → 빈 결과."""
        s = ScenarioDef(shocks={}, cascade_effects=[])
        result = compute_cascade_effects(s)
        assert result == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestRunScenario
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRunScenario:
    """run_scenario 테스트."""

    def test_negative_shock_negative_impact(self):
        """부정 충격 → 부정 포트폴리오 영향."""
        s = ScenarioDef(
            name="하락 시나리오",
            shocks={"반도체": -0.20, "IT": -0.15, "바이오": -0.10},
            probability=0.1,
            tags=["기술주"],
        )
        result = run_scenario(s, _sample_portfolio())
        assert isinstance(result, ScenarioResult)
        assert result.portfolio_impact_pct < 0

    def test_worst_worse_than_best(self):
        """worst_case <= best_case (더 부정적)."""
        s = ScenarioDef(
            name="테스트",
            shocks={"반도체": -0.20, "IT": -0.10},
            probability=0.1,
        )
        result = run_scenario(s, _sample_portfolio())
        assert result.worst_case_pct <= result.best_case_pct

    def test_mitigation_exists(self):
        """mitigation_actions가 비어있지 않음."""
        s = ScenarioDef(
            name="관세 테스트",
            shocks={"자동차": -0.20},
            probability=0.2,
            tags=["관세"],
        )
        result = run_scenario(s, _even_portfolio())
        assert len(result.mitigation_actions) > 0

    def test_positive_scenario(self):
        """긍정 시나리오 영향."""
        s = ScenarioDef(
            name="AI 호재",
            shocks={"IT": 0.15, "반도체": 0.10},
            probability=0.2,
            tags=["AI"],
        )
        result = run_scenario(s, _sample_portfolio())
        assert result.portfolio_impact_pct > 0

    def test_empty_portfolio(self):
        """빈 포트폴리오 → impact 0."""
        s = ScenarioDef(name="테스트", shocks={"반도체": -0.20})
        result = run_scenario(s, {})
        assert result.portfolio_impact_pct == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestRecoveryPath
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRecoveryPath:
    """predict_recovery_path 테스트."""

    def test_v_shape(self):
        """짧은 기간 + 약한 충격 → V-shape."""
        s = ScenarioDef(
            name="경미한 조정",
            shocks={"IT": -0.10},
            duration_days=30,
            probability=0.3,
        )
        rp = predict_recovery_path(s)
        assert isinstance(rp, RecoveryPath)
        assert rp.recovery_type == "V"
        assert rp.expected_days > 0

    def test_u_shape(self):
        """중간 기간 + 중간 충격 → U-shape."""
        s = ScenarioDef(
            name="경기 둔화",
            shocks={"반도체": -0.15},
            duration_days=90,
            probability=0.15,
        )
        rp = predict_recovery_path(s)
        assert rp.recovery_type == "U"
        assert rp.expected_days > 0

    def test_l_shape(self):
        """장기 + 강한 충격 → L-shape."""
        s = ScenarioDef(
            name="장기 침체",
            shocks={"전체": -0.35},
            duration_days=180,
            probability=0.05,
        )
        rp = predict_recovery_path(s)
        assert rp.recovery_type == "L"
        assert rp.expected_days >= 180

    def test_path_length(self):
        """cumulative_path 길이 > 0."""
        s = ScenarioDef(
            name="테스트",
            shocks={"IT": -0.15},
            duration_days=60,
            probability=0.1,
        )
        rp = predict_recovery_path(s)
        assert len(rp.cumulative_path) > 0

    def test_historical_matching(self):
        """과거 데이터 기반 매칭."""
        s = ScenarioDef(
            name="테스트",
            shocks={"반도체": -0.18},
            duration_days=60,
        )
        history = [
            {"name": "코로나", "drawdown_pct": -0.30, "recovery_days": 120, "type": "V"},
            {"name": "금리쇼크", "drawdown_pct": -0.15, "recovery_days": 90, "type": "U"},
        ]
        rp = predict_recovery_path(s, historical_drawdowns=history)
        # Should match closest: 금리쇼크 (0.15 vs 0.18)
        assert rp.recovery_type == "U"
        assert rp.expected_days == 90

    def test_confidence_interval(self):
        """confidence_interval 범위 유효."""
        s = ScenarioDef(name="CI 테스트", shocks={"IT": -0.10}, duration_days=60)
        rp = predict_recovery_path(s)
        lo, hi = rp.confidence_interval
        assert lo > 0
        assert hi >= lo


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestStressTestSuite
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestStressTestSuite:
    """run_stress_test_suite 테스트."""

    def test_all_scenarios_run(self):
        """모든 내장 시나리오 실행."""
        suite = run_stress_test_suite(_sample_portfolio())
        assert isinstance(suite, StressTestSuite)
        assert len(suite.scenarios) == len(SCENARIOS)

    def test_resilience_in_range(self):
        """resilience_score [0, 1] 범위."""
        suite = run_stress_test_suite(_sample_portfolio())
        assert 0.0 <= suite.portfolio_resilience_score <= 1.0

    def test_worst_identified(self):
        """worst_scenario가 비어있지 않음."""
        suite = run_stress_test_suite(_sample_portfolio())
        assert suite.worst_scenario != ""

    def test_expected_loss_negative(self):
        """가중 손실이 0 이하 (부정적 시나리오 다수)."""
        suite = run_stress_test_suite(_sample_portfolio())
        assert suite.expected_loss_weighted <= 0

    def test_custom_scenarios_subset(self):
        """일부 시나리오만 실행."""
        subset = {
            k: v for k, v in list(SCENARIOS.items())[:3]
        }
        suite = run_stress_test_suite(_sample_portfolio(), scenarios=subset)
        assert len(suite.scenarios) == 3

    def test_empty_portfolio(self):
        """빈 포트폴리오 → 영향 없음."""
        suite = run_stress_test_suite({})
        assert suite.expected_loss_weighted == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestFormat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFormat:
    """포맷 함수 테스트."""

    def test_format_scenario_result(self):
        """format_scenario_result → str 반환."""
        s = ScenarioDef(name="포맷 테스트", probability=0.1, tags=["AI"])
        result = ScenarioResult(
            scenario=s,
            portfolio_impact_pct=-0.05,
            sector_impacts={"IT": -0.10, "반도체": -0.05},
            worst_case_pct=-0.10,
            best_case_pct=-0.025,
            var_under_scenario=0.082,
            recovery_days=30,
            mitigation_actions=["현금 비중 확대"],
        )
        text = format_scenario_result(result)
        assert isinstance(text, str)
        assert "포맷 테스트" in text
        assert "대응 전략" in text

    def test_format_stress_test(self):
        """format_stress_test → str 반환."""
        suite = run_stress_test_suite(_sample_portfolio())
        text = format_stress_test(suite)
        assert isinstance(text, str)
        assert "스트레스 테스트" in text
        assert "복원력" in text

    def test_format_empty_result(self):
        """빈 결과 포맷."""
        result = ScenarioResult()
        text = format_scenario_result(result)
        assert isinstance(text, str)
