"""v12.4: 매니저별 리스크 정책 테스트.

테스트 범위:
1. REGIME_WEIGHTS — 5 레짐 × 5 매니저 가중치
2. MANAGER_RISK_POLICY — 5 매니저 정책 구성
3. get_regime_weight — risk_config 연동 + VIX 경계값
4. should_manager_enter — VIX 한도 + 쇼크 차단
"""
from __future__ import annotations

import pytest

from kstock.bot.investment_managers import (
    MANAGER_RISK_POLICY,
    MANAGER_THRESHOLDS,
    REGIME_WEIGHTS,
    get_manager_risk_policy,
    get_regime_weight,
    should_manager_enter,
)


# =====================================================================
# REGIME_WEIGHTS 구조
# =====================================================================

class TestRegimeWeights:

    def test_all_regimes_present(self):
        assert set(REGIME_WEIGHTS) == {"calm", "normal", "fear", "panic", "crisis"}

    def test_all_managers_in_each_regime(self):
        mgrs = {"scalp", "swing", "position", "long_term", "tenbagger"}
        for regime, weights in REGIME_WEIGHTS.items():
            assert set(weights.keys()) == mgrs, f"Missing managers in {regime}"

    def test_crisis_scalp_disabled(self):
        assert REGIME_WEIGHTS["crisis"]["scalp"] == 0.0

    def test_panic_long_term_amplified(self):
        assert REGIME_WEIGHTS["panic"]["long_term"] >= 1.3

    def test_calm_scalp_amplified(self):
        assert REGIME_WEIGHTS["calm"]["scalp"] > 1.0

    def test_normal_all_neutral(self):
        for v in REGIME_WEIGHTS["normal"].values():
            assert v == 1.0


# =====================================================================
# MANAGER_RISK_POLICY 구조
# =====================================================================

class TestManagerRiskPolicy:

    MANAGERS = ["scalp", "swing", "position", "long_term", "tenbagger"]
    REQUIRED_KEYS = ["label", "description", "max_vix_for_entry",
                     "wartime_action", "stop_tighten_pct"]

    def test_all_managers_present(self):
        assert set(MANAGER_RISK_POLICY.keys()) == set(self.MANAGERS)

    @pytest.mark.parametrize("mgr", MANAGERS)
    def test_required_keys(self, mgr):
        policy = MANAGER_RISK_POLICY[mgr]
        for key in self.REQUIRED_KEYS:
            assert key in policy, f"Missing {key} in {mgr}"

    def test_scalp_tightest_vix(self):
        """스캘퍼가 가장 낮은 VIX 한도."""
        vix_limits = {k: v["max_vix_for_entry"] for k, v in MANAGER_RISK_POLICY.items()}
        assert vix_limits["scalp"] <= min(vix_limits[k] for k in vix_limits if k != "scalp")

    def test_long_term_loosest_vix(self):
        """장기 투자가 가장 높은 VIX 한도."""
        vix_limits = {k: v["max_vix_for_entry"] for k, v in MANAGER_RISK_POLICY.items()}
        assert vix_limits["long_term"] >= max(vix_limits[k] for k in vix_limits if k != "long_term")

    def test_stop_tighten_decreasing_with_horizon(self):
        """보유기간 길수록 손절 강화 비율 낮음."""
        vals = [MANAGER_RISK_POLICY[m]["stop_tighten_pct"]
                for m in ["scalp", "swing", "position", "long_term"]]
        assert vals == sorted(vals, reverse=True)


# =====================================================================
# get_regime_weight — risk_config 연동
# =====================================================================

class TestGetRegimeWeight:

    def test_calm_scalp(self):
        assert get_regime_weight("scalp", vix=14) == 1.2

    def test_normal_all_one(self):
        assert get_regime_weight("swing", vix=20) == 1.0

    def test_fear_scalp_reduced(self):
        assert get_regime_weight("scalp", vix=28) == 0.6

    def test_panic_long_term_amplified(self):
        assert get_regime_weight("long_term", vix=36) == 1.5

    def test_crisis_scalp_zero(self):
        """VIX 45 → crisis 레짐 → scalp 0.0."""
        assert get_regime_weight("scalp", vix=45) == 0.0

    def test_crisis_tenbagger_reduced(self):
        """VIX 45 → crisis → tenbagger 0.5 (극단 위기 시 축소)."""
        assert get_regime_weight("tenbagger", vix=45) == 0.5

    def test_unknown_manager_default_one(self):
        assert get_regime_weight("unknown", vix=20) == 1.0

    def test_boundary_vix_18(self):
        """VIX 18 → normal (normal_low 경계)."""
        w = get_regime_weight("scalp", vix=18)
        assert w == REGIME_WEIGHTS["normal"]["scalp"]

    def test_boundary_vix_25(self):
        """VIX 25 → fear (normal_high 경계)."""
        w = get_regime_weight("scalp", vix=25)
        assert w == REGIME_WEIGHTS["fear"]["scalp"]

    def test_boundary_vix_30(self):
        """VIX 30 → panic (fear 경계)."""
        w = get_regime_weight("scalp", vix=30)
        assert w == REGIME_WEIGHTS["panic"]["scalp"]


# =====================================================================
# should_manager_enter
# =====================================================================

class TestShouldManagerEnter:

    def test_scalp_vix_ok(self):
        ok, msg = should_manager_enter("scalp", vix=20)
        assert ok is True

    def test_scalp_vix_blocked(self):
        ok, msg = should_manager_enter("scalp", vix=26)
        assert ok is False
        assert "VIX" in msg

    def test_swing_vix_25_ok(self):
        ok, _ = should_manager_enter("swing", vix=25)
        assert ok is True

    def test_swing_vix_31_blocked(self):
        ok, msg = should_manager_enter("swing", vix=31)
        assert ok is False

    def test_long_term_vix_45_ok(self):
        """버핏: VIX 45에서도 매수 가능 (max 50)."""
        ok, _ = should_manager_enter("long_term", vix=45)
        assert ok is True

    def test_long_term_vix_51_blocked(self):
        ok, msg = should_manager_enter("long_term", vix=51)
        assert ok is False

    def test_tenbagger_shock_blocked(self):
        ok, msg = should_manager_enter("tenbagger", vix=20, shock_grade="SHOCK")
        assert ok is False
        assert "쇼크" in msg

    def test_tenbagger_alert_ok(self):
        """텐배거: ALERT은 허용."""
        ok, _ = should_manager_enter("tenbagger", vix=20, shock_grade="ALERT")
        assert ok is True

    def test_scalp_alert_blocked(self):
        """스캘퍼: ALERT에서도 차단."""
        ok, msg = should_manager_enter("scalp", vix=20, shock_grade="ALERT")
        assert ok is False

    def test_position_shock_ok(self):
        """포지션: SHOCK에서도 VIX 한도 내면 허용."""
        ok, _ = should_manager_enter("position", vix=20, shock_grade="SHOCK")
        assert ok is True

    def test_all_clear(self):
        for mgr in ["scalp", "swing", "position", "long_term", "tenbagger"]:
            ok, _ = should_manager_enter(mgr, vix=15)
            assert ok is True, f"{mgr} should be ok at VIX 15"
