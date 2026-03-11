"""Tests for advanced risk engine."""
import numpy as np
from kstock.core.risk_engine import (
    VaRResult,
    MonteCarloResult,
    StressTestResult,
    AdvancedRiskReport,
    calculate_historical_var,
    calculate_parametric_var,
    run_monte_carlo,
    run_stress_test,
    calculate_real_correlation,
    _calculate_risk_grade,
    format_advanced_risk_report,
    HISTORICAL_STRESS_SCENARIOS,
)
import pandas as pd


def test_historical_var_basic():
    returns = np.random.normal(0, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.var_95 < 0
    assert result.var_99 <= result.var_95  # 99% is more extreme loss
    assert result.method == "historical"


def test_var_95_less_than_99():
    returns = np.random.normal(0, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.var_99_pct <= result.var_95_pct


def test_cvar_gte_var():
    returns = np.random.normal(-0.001, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.cvar_95_pct <= result.var_95_pct


def test_parametric_var():
    weights = np.array([0.5, 0.5])
    mean_returns = np.array([0.001, 0.0005])
    cov_matrix = np.array([[0.0004, 0.0001], [0.0001, 0.0003]])
    result = calculate_parametric_var(10_000_000, weights, mean_returns, cov_matrix)
    assert result.var_95_pct < 0
    assert result.method == "parametric"


def test_monte_carlo_distribution():
    weights = np.array([0.6, 0.4])
    mean_returns = np.array([0.0005, 0.0003])
    cov_matrix = np.array([[0.0004, 0.00005], [0.00005, 0.0002]])
    result = run_monte_carlo(10_000_000, weights, mean_returns, cov_matrix, simulations=1000)
    assert result.simulations == 1000
    assert len(result.distribution) == 100
    assert result.best_case_pct > result.worst_case_pct


def test_stress_test_all_scenarios():
    holdings = [
        {"ticker": "005930", "name": "삼성전자", "weight": 0.5, "sector": "반도체"},
        {"ticker": "005380", "name": "현대차", "weight": 0.5, "sector": "자동차"},
    ]
    results = run_stress_test(10_000_000, holdings)
    assert len(results) == len(HISTORICAL_STRESS_SCENARIOS)
    for r in results:
        assert r.portfolio_impact_pct < 0


def test_stress_test_single():
    holdings = [{"ticker": "005930", "name": "삼성전자", "weight": 1.0, "sector": "반도체"}]
    results = run_stress_test(10_000_000, holdings, "covid_crash")
    assert len(results) == 1
    assert "코로나" in results[0].scenario_name


def test_risk_grade_a():
    grade, score = _calculate_risk_grade(var_95_pct=-0.5, max_dd_pct=-2, concentration=0.2, max_corr=0.3, worst_stress_pct=-10)
    assert grade in ("A", "B")
    assert score <= 40


def test_risk_grade_f():
    grade, score = _calculate_risk_grade(var_95_pct=-5, max_dd_pct=-30, concentration=0.9, max_corr=0.95, worst_stress_pct=-50)
    assert grade in ("D", "F")
    assert score >= 60


def test_real_correlation():
    dates = pd.date_range("2024-01-01", periods=100)
    prices = {
        "A": pd.Series(np.cumsum(np.random.randn(100)) + 100, index=dates),
        "B": pd.Series(np.cumsum(np.random.randn(100)) + 100, index=dates),
    }
    corr = calculate_real_correlation(prices)
    assert not corr.empty
    assert corr.shape == (2, 2)
    assert corr.iloc[0, 0] == 1.0


def test_format_report():
    report = AdvancedRiskReport(
        date="2025-02-25",
        portfolio_value=10_000_000,
        risk_grade="B",
        risk_score=35,
    )
    text = format_advanced_risk_report(report)
    assert "리스크" in text
    assert "B" in text


def test_empty_holdings_var():
    result = calculate_historical_var(10_000_000, [])
    assert result.var_95 == 0
    assert "데이터 부족" in result.confidence_text


# =====================================================================
# v12.5: RiskEngine + ManagerRiskPolicy 통합 테스트
# =====================================================================

import pytest
from kstock.core.risk_engine import (
    ManagerAction,
    ManagerRiskPolicy,
    RiskContext,
    RiskEngine,
)


@pytest.fixture
def engine():
    return RiskEngine()


# ── RiskContext ──────────────────────────────────────────────────

class TestRiskContext:

    def test_defaults(self):
        ctx = RiskContext()
        assert ctx.vix == 0.0
        assert ctx.fear_greed == 50.0
        assert ctx.shock_grade == "NONE"
        assert ctx.alert_mode == "normal"

    def test_from_dict_basic(self):
        ctx = RiskContext.from_dict({"vix": 30, "usdkrw": 1400})
        assert ctx.vix == 30
        assert ctx.usdkrw == 1400

    def test_from_dict_fear_greed_alias(self):
        ctx = RiskContext.from_dict({"fear_greed_score": 22})
        assert ctx.fear_greed == 22

    def test_from_dict_none_values(self):
        ctx = RiskContext.from_dict({"vix": None, "usdkrw": None})
        assert ctx.vix == 0.0
        assert ctx.usdkrw == 0.0

    def test_from_macro_snapshot(self):
        class FakeSnap:
            vix = 28.5
            vix_change_pct = -2.1
            usdkrw = 1350
            usdkrw_change_pct = 0.5
            fear_greed_score = 30
        ctx = RiskContext.from_macro_snapshot(FakeSnap())
        assert ctx.vix == 28.5
        assert ctx.usdkrw == 1350
        assert ctx.fear_greed == 30

    def test_from_macro_snapshot_none_fields(self):
        class FakeSnap:
            vix = None
            vix_change_pct = None
            usdkrw = None
            usdkrw_change_pct = None
            fear_greed_score = None
        ctx = RiskContext.from_macro_snapshot(FakeSnap())
        assert ctx.vix == 0.0
        assert ctx.fear_greed == 50.0


# ── ManagerAction ───────────────────────────────────────────────

class TestManagerAction:

    def test_defaults(self):
        a = ManagerAction()
        assert a.can_enter is True
        assert a.block_reason == ""
        assert a.regime_weight == 1.0

    def test_bool_true(self):
        assert bool(ManagerAction(can_enter=True)) is True

    def test_bool_false(self):
        assert bool(ManagerAction(can_enter=False)) is False

    def test_independent_lists(self):
        a1 = ManagerAction()
        a2 = ManagerAction()
        a1.recommendations.append("test")
        assert a2.recommendations == []


# ── RiskEngine.evaluate — VIX 레짐별 ───────────────────────────

class TestEvaluateVix:

    def test_calm(self, engine):
        rd = engine.evaluate(RiskContext(vix=14))
        assert rd.regime == "calm"
        assert rd.allowed is True
        assert rd.risk_level == "normal"

    def test_normal(self, engine):
        rd = engine.evaluate(RiskContext(vix=20))
        assert rd.regime == "normal"
        assert rd.allowed is True

    def test_fear(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        assert rd.regime == "fear"
        assert rd.cash_floor_pct == 25.0
        assert rd.risk_level == "warning"

    def test_panic(self, engine):
        rd = engine.evaluate(RiskContext(vix=36))
        assert rd.regime == "panic"
        assert rd.allowed is False
        assert rd.block_new_buy is True

    def test_crisis(self, engine):
        rd = engine.evaluate(RiskContext(vix=45))
        assert rd.regime == "crisis"
        assert rd.allowed is False

    def test_source_is_risk_engine(self, engine):
        rd = engine.evaluate(RiskContext(vix=20))
        assert rd.source == "risk_engine"


# ── RiskEngine.evaluate — Fear & Greed ─────────────────────────

class TestEvaluateFearGreed:

    def test_extreme_fear(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, fear_greed=15))
        assert "extreme_fear" in rd.source_flags

    def test_fear_fg(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, fear_greed=30))
        assert any("공포" in r for r in rd.reasons)

    def test_neutral_no_fg_flag(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, fear_greed=50))
        assert "extreme_fear" not in rd.source_flags


# ── RiskEngine.evaluate — 쇼크/글로벌 ─────────────────────────

class TestEvaluateShock:

    def test_shock_grade(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, shock_grade="SHOCK"))
        assert rd.block_new_buy is True

    def test_alert_grade(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, shock_grade="ALERT"))
        assert rd.block_new_buy is False

    def test_global_shock_high(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, global_shock_score=75))
        assert "global_shock_high" in rd.source_flags
        assert rd.risk_score >= 75

    def test_korea_open_risk_high(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, korea_open_risk_score=80))
        assert "korea_open_risk_high" in rd.source_flags


# ── RiskEngine.evaluate — alert_mode ──────────────────────────

class TestEvaluateAlertMode:

    def test_wartime(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="wartime"))
        assert rd.reduce_position is True
        assert rd.cash_floor_pct >= 30.0
        assert "wartime" in rd.source_flags

    def test_elevated(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="elevated"))
        assert rd.cash_floor_pct >= 15.0
        assert "elevated" in rd.source_flags

    def test_normal_no_flags(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="normal"))
        assert "wartime" not in rd.source_flags


# ── RiskEngine.evaluate — regime_mode ─────────────────────────

class TestEvaluateRegimeMode:

    def test_defense(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, regime_mode="defense"))
        assert "defense_regime" in rd.source_flags

    def test_attack(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, regime_mode="attack"))
        assert "attack_regime" in rd.source_flags

    def test_balanced_no_flag(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, regime_mode="balanced"))
        assert "defense_regime" not in rd.source_flags
        assert "attack_regime" not in rd.source_flags


# ── ManagerRiskPolicy.apply ───────────────────────────────────

class TestManagerPolicyApply:

    def test_calm_all_enter(self, engine):
        rd = engine.evaluate(RiskContext(vix=14))
        for mgr in ["scalp", "swing", "position", "long_term", "tenbagger"]:
            action = ManagerRiskPolicy.apply(mgr, rd)
            assert action.can_enter is True, f"{mgr} blocked at VIX 14"

    def test_fear_scalp_blocked(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.can_enter is False

    def test_fear_swing_ok(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        action = ManagerRiskPolicy.apply("swing", rd)
        assert action.can_enter is True

    def test_panic_blocks_scalp_swing(self, engine):
        rd = engine.evaluate(RiskContext(vix=36))
        for mgr in ["scalp", "swing"]:
            action = ManagerRiskPolicy.apply(mgr, rd)
            assert action.can_enter is False, f"{mgr} should be blocked"

    def test_panic_long_term_allowed(self, engine):
        rd = engine.evaluate(RiskContext(vix=36))
        action = ManagerRiskPolicy.apply("long_term", rd)
        assert action.can_enter is True

    def test_panic_tenbagger_allowed(self, engine):
        """v12.6: 텐배거 panic에서 VIX 한도(40) 이내면 매수 허용."""
        rd = engine.evaluate(RiskContext(vix=36))
        action = ManagerRiskPolicy.apply("tenbagger", rd)
        assert action.can_enter is True

    def test_regime_weight_calm_scalp(self, engine):
        rd = engine.evaluate(RiskContext(vix=14))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.regime_weight == 1.2

    def test_regime_weight_crisis_scalp_zero(self, engine):
        rd = engine.evaluate(RiskContext(vix=45))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.regime_weight == 0.0

    def test_stop_tighten_warning(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.stop_tighten_pct == 50

    def test_stop_tighten_zero_calm(self, engine):
        rd = engine.evaluate(RiskContext(vix=14))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.stop_tighten_pct == 0

    def test_recommendations_cash(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        action = ManagerRiskPolicy.apply("swing", rd)
        assert any("현금" in r for r in action.recommendations)


# ── ManagerRiskPolicy.apply — wartime ─────────────────────────

class TestManagerPolicyWartime:

    def test_wartime_disables_scalp(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="wartime"))
        action = ManagerRiskPolicy.apply("scalp", rd)
        assert action.can_enter is False

    def test_wartime_restricts_swing(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="wartime"))
        action = ManagerRiskPolicy.apply("swing", rd)
        assert action.can_enter is False

    def test_wartime_long_term_ok(self, engine):
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="wartime"))
        action = ManagerRiskPolicy.apply("long_term", rd)
        assert action.can_enter is True


# ── ManagerRiskPolicy.apply_all ───────────────────────────────

class TestApplyAll:

    def test_returns_five(self, engine):
        rd = engine.evaluate(RiskContext(vix=20))
        all_a = ManagerRiskPolicy.apply_all(rd)
        assert set(all_a.keys()) == {"scalp", "swing", "position", "long_term", "tenbagger"}

    def test_values_are_manager_actions(self, engine):
        rd = engine.evaluate(RiskContext(vix=20))
        for v in ManagerRiskPolicy.apply_all(rd).values():
            assert isinstance(v, ManagerAction)

    def test_calm_all_enter(self, engine):
        rd = engine.evaluate(RiskContext(vix=14))
        for a in ManagerRiskPolicy.apply_all(rd).values():
            assert a.can_enter is True

    def test_panic_mixed(self, engine):
        rd = engine.evaluate(RiskContext(vix=36))
        aa = ManagerRiskPolicy.apply_all(rd)
        assert aa["scalp"].can_enter is False
        assert aa["long_term"].can_enter is True


# ── 통합 시나리오 ────────────────────────────────────────────

class TestIntegration:

    def test_worst_case(self, engine):
        ctx = RiskContext(
            vix=45, usdkrw=1460, shock_grade="SHOCK",
            fear_greed=10, alert_mode="wartime",
        )
        rd = engine.evaluate(ctx)
        assert rd.allowed is False
        assert rd.block_new_buy is True
        assert "extreme_fear" in rd.source_flags
        assert "wartime" in rd.source_flags
        assert len(rd.reasons) >= 3
        for mgr in ["scalp", "swing", "tenbagger"]:
            assert not ManagerRiskPolicy.apply(mgr, rd).can_enter

    def test_all_clear(self, engine):
        ctx = RiskContext(vix=14, usdkrw=1200, fear_greed=60)
        rd = engine.evaluate(ctx)
        assert rd.allowed is True
        assert rd.risk_level == "normal"
        assert rd.cash_floor_pct == 0.0
        for a in ManagerRiskPolicy.apply_all(rd).values():
            assert a.can_enter is True

    def test_normal_vix_with_shock(self, engine):
        ctx = RiskContext(vix=18, shock_grade="SHOCK")
        rd = engine.evaluate(ctx)
        assert rd.allowed is False
        assert rd.block_new_buy is True

    def test_from_dict_end_to_end(self, engine):
        d = {"vix": 30, "usdkrw": 1380, "fear_greed": 40,
             "shock_grade": "ALERT", "alert_mode": "normal"}
        ctx = RiskContext.from_dict(d)
        rd = engine.evaluate(ctx)
        aa = ManagerRiskPolicy.apply_all(rd)
        assert isinstance(rd.regime, str)
        assert len(aa) == 5


# ── v12.6: USDKRW 모멘텀 전달 ─────────────────────────────────

class TestUsdkrwMomentumEngine:

    def test_evaluate_passes_change_pct(self, engine):
        ctx = RiskContext(vix=20, usdkrw=1400, usdkrw_change_pct=1.2)
        rd = engine.evaluate(ctx)
        assert rd.usdkrw_momentum == "급등"
        assert rd.usdkrw_change_pct == 1.2

    def test_evaluate_cross_pattern(self, engine):
        ctx = RiskContext(vix=28, usdkrw=1300, usdkrw_change_pct=0.7)
        rd = engine.evaluate(ctx)
        assert "foreign_outflow_pattern" in rd.source_flags

    def test_evaluate_no_change_backward_compat(self, engine):
        ctx = RiskContext(vix=20, usdkrw=1350)
        rd = engine.evaluate(ctx)
        assert rd.usdkrw_momentum == ""


# ── v12.6: 텐배거 보유 관리 ────────────────────────────────────

class TestTenbaggerHolding:

    def test_panic_can_enter(self, engine):
        """텐배거: panic(VIX 36) + VIX < 40 → 매수 허용."""
        rd = engine.evaluate(RiskContext(vix=36))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.can_enter is True
        assert a.holding_override_stop is True
        assert a.stop_tighten_pct == 0.0

    def test_crisis_blocked_with_reduce(self, engine):
        """텐배거: crisis(VIX 45) → 매수 차단 + C등급 축소."""
        rd = engine.evaluate(RiskContext(vix=45))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.can_enter is False
        assert a.holding_action == "reduce"
        assert a.holding_reduce_pct == 50.0
        assert any("C등급" in r for r in a.recommendations)

    def test_fear_no_stop_tighten(self, engine):
        """텐배거: fear → 손절 강화 안 함."""
        rd = engine.evaluate(RiskContext(vix=28))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.stop_tighten_pct == 0.0
        assert a.holding_override_stop is True

    def test_calm_normal_hold(self, engine):
        """텐배거: calm → 정상 보유."""
        rd = engine.evaluate(RiskContext(vix=14))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.can_enter is True
        assert a.holding_action == "hold"
        assert a.holding_override_stop is True

    def test_panic_blocked_above_vix_40(self, engine):
        """텐배거: VIX 42 (crisis) → 매수 차단."""
        rd = engine.evaluate(RiskContext(vix=42))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.can_enter is False

    def test_krw_weakness_benefit(self, engine):
        """텐배거: USDKRW 1380 + 외인이탈 아님 → 수출 수혜."""
        rd = engine.evaluate(RiskContext(vix=20, usdkrw=1380))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert any("수출형" in r for r in a.recommendations)

    def test_krw_weakness_with_outflow_no_benefit(self, engine):
        """VIX 28 + USDKRW +0.7% → 외인이탈 패턴 → 수혜 안 뜸."""
        rd = engine.evaluate(RiskContext(vix=28, usdkrw=1380, usdkrw_change_pct=0.7))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert not any("수출형" in r for r in a.recommendations)

    def test_wartime_tenbagger_blocked(self, engine):
        """텐배거: wartime → event_hold → 매수 차단."""
        rd = engine.evaluate(RiskContext(vix=20, alert_mode="wartime"))
        a = ManagerRiskPolicy.apply("tenbagger", rd)
        assert a.can_enter is False


# ── v12.6: long_term 보유 관리 ──────────────────────────────────

class TestLongTermHolding:

    def test_panic_override_stop(self, engine):
        """long_term: panic → 손절 강화 안 함."""
        rd = engine.evaluate(RiskContext(vix=36))
        a = ManagerRiskPolicy.apply("long_term", rd)
        assert a.holding_override_stop is True

    def test_calm_no_override(self, engine):
        """long_term: calm → 기본 동작."""
        rd = engine.evaluate(RiskContext(vix=14))
        a = ManagerRiskPolicy.apply("long_term", rd)
        assert a.holding_override_stop is False


# ── v12.6: scalp/swing 기존 동작 유지 ──────────────────────────

class TestOtherManagersHolding:

    def test_scalp_default_holding(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        a = ManagerRiskPolicy.apply("scalp", rd)
        assert a.holding_action == "hold"
        assert a.holding_override_stop is False

    def test_swing_default_holding(self, engine):
        rd = engine.evaluate(RiskContext(vix=28))
        a = ManagerRiskPolicy.apply("swing", rd)
        assert a.holding_action == "hold"
        assert a.holding_override_stop is False
