"""v12.4: for_regime_smoothed() 검증 테스트.

테스트 범위:
1. 기본 동작 — VIX 레벨별 제약 조건 방향성
2. 연속성 — VIX가 1씩 변할 때 출력이 급변하지 않음 (smooth)
3. 단조성 — VIX 증가 시 제약 강화 (max_single_weight ↓, min_cash_pct ↑)
4. 경계값 — VIX 극단값 (0, 100)에서 clamp 작동
5. prev_vix 댐핑 — 이전 VIX가 현재 출력에 영향
6. for_regime() (step) vs for_regime_smoothed() 비교
"""
from __future__ import annotations

import pytest

from kstock.core.risk_policy import RiskConstraintSet


@pytest.fixture
def base():
    return RiskConstraintSet()


# =====================================================================
# 1. 기본 동작 — VIX 레벨별 방향성
# =====================================================================

class TestBasicBehavior:

    def test_calm_relaxes(self, base):
        """VIX 10 → 기본보다 완화된 제약."""
        result = base.for_regime_smoothed(vix=10)
        assert result.max_single_weight >= base.max_single_weight
        assert result.min_cash_pct <= base.min_cash_pct + 0.01

    def test_normal_near_base(self, base):
        """VIX 20 → 기본값에 가까움."""
        result = base.for_regime_smoothed(vix=20)
        assert abs(result.max_single_weight - base.max_single_weight) < 0.05
        assert abs(result.min_cash_pct - base.min_cash_pct) < 0.10

    def test_fear_tightens(self, base):
        """VIX 28 → 기본보다 강화된 제약."""
        result = base.for_regime_smoothed(vix=28)
        assert result.max_single_weight < base.max_single_weight
        assert result.min_cash_pct > base.min_cash_pct

    def test_panic_much_tighter(self, base):
        """VIX 35 → fear보다 더 강화."""
        fear = base.for_regime_smoothed(vix=28)
        panic = base.for_regime_smoothed(vix=35)
        assert panic.max_single_weight < fear.max_single_weight
        assert panic.min_cash_pct > fear.min_cash_pct

    def test_crisis_extreme(self, base):
        """VIX 50 → 극단 제약."""
        result = base.for_regime_smoothed(vix=50)
        assert result.max_single_weight <= 0.15
        assert result.min_cash_pct >= 0.30
        # sigmoid 기반이므로 leverage는 clamp 하한(0.5)까지 천천히 내려감
        assert result.max_leverage <= 0.75


# =====================================================================
# 2. 연속성 (Smoothness)
# =====================================================================

class TestSmoothness:

    def test_no_jump_at_25(self, base):
        """VIX 24.9 → 25.1: 급격한 점프 없음."""
        r24 = base.for_regime_smoothed(vix=24.9)
        r25 = base.for_regime_smoothed(vix=25.1)
        diff = abs(r24.max_single_weight - r25.max_single_weight)
        assert diff < 0.02, f"Jump at 25: {diff:.4f}"

    def test_no_jump_at_30(self, base):
        """VIX 29.9 → 30.1: 급격한 점프 없음."""
        r29 = base.for_regime_smoothed(vix=29.9)
        r30 = base.for_regime_smoothed(vix=30.1)
        diff = abs(r29.max_single_weight - r30.max_single_weight)
        assert diff < 0.02, f"Jump at 30: {diff:.4f}"

    def test_max_step_across_range(self, base):
        """VIX 10→50 범위에서 1 단위 최대 변화량 제한."""
        prev = base.for_regime_smoothed(vix=10)
        max_step = 0.0
        for vix_int in range(11, 51):
            curr = base.for_regime_smoothed(vix=float(vix_int))
            step = abs(curr.max_single_weight - prev.max_single_weight)
            max_step = max(max_step, step)
            prev = curr
        assert max_step < 0.03, f"Max step: {max_step:.4f}"


# =====================================================================
# 3. 단조성 (Monotonicity)
# =====================================================================

class TestMonotonicity:

    def test_max_single_weight_decreases(self, base):
        """VIX 증가 → max_single_weight 감소 (전체 범위에서 대체로)."""
        weights = [base.for_regime_smoothed(vix=float(v)).max_single_weight
                   for v in range(15, 45)]
        # 약간의 비단조성은 relax→tighten 전환 구간에서 허용
        decreasing_count = sum(1 for i in range(len(weights) - 1)
                               if weights[i] >= weights[i + 1])
        assert decreasing_count >= len(weights) * 0.8

    def test_min_cash_increases(self, base):
        """VIX 증가 → min_cash_pct 증가 (대체로)."""
        cash = [base.for_regime_smoothed(vix=float(v)).min_cash_pct
                for v in range(15, 45)]
        increasing_count = sum(1 for i in range(len(cash) - 1)
                               if cash[i] <= cash[i + 1])
        assert increasing_count >= len(cash) * 0.8

    def test_max_kelly_decreases_in_panic(self, base):
        """VIX 30+ → kelly fraction 감소."""
        k25 = base.for_regime_smoothed(vix=25).max_kelly_fraction
        k35 = base.for_regime_smoothed(vix=35).max_kelly_fraction
        assert k35 < k25


# =====================================================================
# 4. 경계값 (Clamp)
# =====================================================================

class TestBoundary:

    def test_vix_zero(self, base):
        """VIX 0 → 유효한 출력, 완화 최대."""
        result = base.for_regime_smoothed(vix=0)
        assert result.max_single_weight >= 0.05
        assert result.max_single_weight <= 0.40
        assert result.min_cash_pct >= 0.0
        assert result.min_cash_pct <= 0.50

    def test_vix_100(self, base):
        """VIX 100 → 극단 제약, clamp 작동."""
        result = base.for_regime_smoothed(vix=100)
        assert result.max_single_weight >= 0.05  # min clamp
        assert result.max_single_weight <= 0.40  # max clamp
        assert result.min_cash_pct <= 0.50        # max clamp

    def test_vix_negative(self, base):
        """VIX 음수 → 에러 없이 처리."""
        result = base.for_regime_smoothed(vix=-5)
        assert result.max_single_weight >= 0.05
        assert result.min_cash_pct >= 0.0


# =====================================================================
# 5. prev_vix 댐핑
# =====================================================================

class TestPrevVixDamping:

    def test_with_prev_differs(self, base):
        """prev_vix 제공 시 순수 VIX만 쓸 때와 다름."""
        no_prev = base.for_regime_smoothed(vix=30)
        with_prev = base.for_regime_smoothed(vix=30, prev_vix=20)
        # prev_vix=20 (낮음)이므로 tightening이 약간 완화됨
        assert with_prev.max_single_weight >= no_prev.max_single_weight - 0.01

    def test_prev_high_tightens_more(self, base):
        """prev_vix가 높으면 현재 tightening 강화."""
        with_low_prev = base.for_regime_smoothed(vix=28, prev_vix=20)
        with_high_prev = base.for_regime_smoothed(vix=28, prev_vix=35)
        # 이전 VIX가 높으면 더 보수적
        assert with_high_prev.max_single_weight <= with_low_prev.max_single_weight

    def test_prev_none_same_as_default(self, base):
        """prev_vix=None → 댐핑 없음 (기본)."""
        r1 = base.for_regime_smoothed(vix=25, prev_vix=None)
        r2 = base.for_regime_smoothed(vix=25)
        assert r1.max_single_weight == r2.max_single_weight


# =====================================================================
# 6. for_regime() vs for_regime_smoothed() 비교
# =====================================================================

class TestStepVsSmooth:

    def test_same_direction_calm(self, base):
        """VIX 12: step과 smooth 모두 완화 방향."""
        step = base.for_regime(vix=12)
        smooth = base.for_regime_smoothed(vix=12)
        # 둘 다 base보다 완화
        assert step.max_single_weight >= base.max_single_weight
        assert smooth.max_single_weight >= base.max_single_weight

    def test_same_direction_panic(self, base):
        """VIX 35: step과 smooth 모두 긴축 방향."""
        step = base.for_regime(vix=35)
        smooth = base.for_regime_smoothed(vix=35)
        # 둘 다 base보다 긴축
        assert step.max_single_weight < base.max_single_weight
        assert smooth.max_single_weight < base.max_single_weight

    def test_smooth_closer_at_boundary(self, base):
        """VIX 25 경계: smooth는 25±1에서 연속, step은 급변."""
        step_24 = base.for_regime(vix=24)
        step_26 = base.for_regime(vix=26)
        smooth_24 = base.for_regime_smoothed(vix=24)
        smooth_26 = base.for_regime_smoothed(vix=26)

        step_jump = abs(step_24.max_single_weight - step_26.max_single_weight)
        smooth_jump = abs(smooth_24.max_single_weight - smooth_26.max_single_weight)
        assert smooth_jump < step_jump or smooth_jump < 0.03
