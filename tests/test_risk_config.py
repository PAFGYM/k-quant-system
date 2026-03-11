"""v12.3: risk_config 경계값 통합 테스트.

테스트 범위:
1. VixThresholds — regime_for(), status_label() 경계값
2. UsdkrwThresholds — 6단계 경계
3. ShockThresholds — oil/us_futures/vix_change 등급
4. PositionLimits — 기본값 검증
5. YAML 로드 — 정상/커스텀/파일없음/파손
6. 소비자 함수 통합 — risk_policy, korea_risk, scoring, ensemble, volatility_regime
7. 싱글턴 — get_risk_thresholds() 캐시 동작
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# ── risk_config 직접 임포트 ───────────────────────────────────────
from kstock.core.risk_config import (
    VixThresholds,
    UsdkrwThresholds,
    ShockThresholds,
    PositionLimits,
    RiskThresholds,
    load_risk_thresholds,
)


# =====================================================================
# 1. VixThresholds 경계값 테스트
# =====================================================================


class TestVixThresholds:
    """VIX 임계값 경계 정확도."""

    vt = VixThresholds()  # 기본값: calm=15, normal_low=18, normal_high=25, fear=30, panic=35, crisis=40

    # ── regime_for() ──────────────────────────────────────────

    @pytest.mark.parametrize("vix, expected", [
        # calm 영역 (< 15)
        (0.0, "calm"),
        (14.9, "calm"),
        # 경계: 정확히 15는 여전히 calm (< normal_low=18)
        (15.0, "calm"),
        # normal 영역 (18 <= vix < 25)
        (18.0, "normal"),
        (18.1, "normal"),
        (24.9, "normal"),
        # fear 영역 (25 <= vix < 30)
        (25.0, "fear"),
        (25.1, "fear"),
        (29.9, "fear"),
        # panic 영역 (30 <= vix < 35)
        (30.0, "panic"),
        (34.9, "panic"),
        # crisis 영역 (>= 40)
        (35.0, "panic"),  # panic=35, crisis=40이므로 35는 panic
        (39.9, "panic"),
        (40.0, "crisis"),
        (80.0, "crisis"),
    ])
    def test_regime_for(self, vix: float, expected: str):
        assert self.vt.regime_for(vix) == expected

    # ── status_label() ────────────────────────────────────────

    @pytest.mark.parametrize("vix, expected", [
        (0.0, "안정"),
        (14.9, "안정"),
        (17.9, "안정"),
        (18.0, "주의"),
        (24.9, "주의"),
        (25.0, "경계"),
        (29.9, "경계"),
        (30.0, "공포"),
        (50.0, "공포"),
    ])
    def test_status_label(self, vix: float, expected: str):
        assert self.vt.status_label(vix) == expected

    # ── regime_for / status_label 경계 정합성 ────────────────
    def test_regime_and_label_consistency(self):
        """regime=panic → label=공포, regime=fear → label=경계 등 일관성."""
        mapping = {
            "calm": "안정",
            "normal": "주의",
            "fear": "경계",
            "panic": "공포",
            "crisis": "공포",
        }
        test_points = [10, 18, 25, 30, 35, 40, 50]
        for vix in test_points:
            regime = self.vt.regime_for(vix)
            label = self.vt.status_label(vix)
            assert label == mapping[regime], (
                f"VIX={vix}: regime={regime} → expected label={mapping[regime]}, got {label}"
            )

    def test_frozen(self):
        """frozen=True 확인."""
        with pytest.raises(AttributeError):
            self.vt.calm = 20.0


# =====================================================================
# 2. UsdkrwThresholds 경계값 테스트
# =====================================================================


class TestUsdkrwThresholds:
    """USD/KRW 임계값 검증."""

    ut = UsdkrwThresholds()  # favorable=1200, normal_low=1250, ..., crisis=1450

    def test_defaults(self):
        assert self.ut.favorable == 1200.0
        assert self.ut.normal_low == 1250.0
        assert self.ut.normal_high == 1300.0
        assert self.ut.warning == 1350.0
        assert self.ut.danger == 1400.0
        assert self.ut.crisis == 1450.0

    def test_ascending_order(self):
        """임계값이 오름차순인지 확인."""
        vals = [self.ut.favorable, self.ut.normal_low, self.ut.normal_high,
                self.ut.warning, self.ut.danger, self.ut.crisis]
        assert vals == sorted(vals), f"Not ascending: {vals}"

    def test_frozen(self):
        with pytest.raises(AttributeError):
            self.ut.danger = 1500.0


# =====================================================================
# 3. ShockThresholds 경계값 테스트
# =====================================================================


class TestShockThresholds:
    """쇼크 카테고리 (oil, us_futures 등) 임계값."""

    def test_oil_defaults(self):
        rt = RiskThresholds()
        assert rt.oil.watch_pct == 2.0
        assert rt.oil.alert_pct == 3.0
        assert rt.oil.shock_pct == 5.0

    def test_us_futures_defaults(self):
        rt = RiskThresholds()
        assert rt.us_futures.watch_pct == 1.0
        assert rt.us_futures.alert_pct == 1.5
        assert rt.us_futures.shock_pct == 2.5

    def test_vix_change_defaults(self):
        rt = RiskThresholds()
        assert rt.vix_change.watch_pct == 15.0
        assert rt.vix_change.shock_pct == 40.0

    @pytest.mark.parametrize("category", [
        "oil", "us_futures", "vix_change", "dollar", "korea_etf", "usdkrw_change",
    ])
    def test_ascending_order(self, category: str):
        rt = RiskThresholds()
        st = getattr(rt, category)
        assert st.watch_pct <= st.alert_pct <= st.shock_pct, (
            f"{category}: watch={st.watch_pct} alert={st.alert_pct} shock={st.shock_pct}"
        )


# =====================================================================
# 4. PositionLimits 기본값 테스트
# =====================================================================


class TestPositionLimits:

    def test_defaults(self):
        pl = PositionLimits()
        assert pl.max_single_weight == 0.30
        assert pl.max_sector_weight == 0.50
        assert pl.max_kelly_fraction == 0.25
        assert pl.min_cash_pct == 0.05

    def test_sane_ranges(self):
        """비합리적 범위 아닌지 확인."""
        pl = PositionLimits()
        assert 0 < pl.max_single_weight <= 1.0
        assert 0 < pl.max_sector_weight <= 1.0
        assert pl.min_cash_pct >= 0


# =====================================================================
# 5. YAML 로드 테스트
# =====================================================================


class TestYamlLoader:

    def test_load_default_yaml(self):
        """실제 config/risk_thresholds.yaml 로드."""
        rt = load_risk_thresholds(Path("config/risk_thresholds.yaml"))
        assert rt.vix.fear == 30.0
        assert rt.usdkrw.danger == 1400.0
        assert rt.oil.shock_pct == 5.0

    def test_load_missing_yaml_returns_defaults(self):
        """YAML 파일 없으면 기본값 반환 (에러 아님)."""
        rt = load_risk_thresholds(Path("/nonexistent/path.yaml"))
        assert rt.vix.fear == 30.0  # 기본값
        assert rt.usdkrw.danger == 1400.0

    def test_load_custom_yaml(self):
        """커스텀 YAML 오버라이드 확인."""
        content = """
vix:
  calm: 12
  normal_low: 16
  normal_high: 22
  fear: 28
  panic: 33
  crisis: 38
usdkrw:
  favorable: 1150
  normal_low: 1200
  normal_high: 1280
  warning: 1330
  danger: 1380
  crisis: 1430
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            rt = load_risk_thresholds(Path(f.name))

        assert rt.vix.fear == 28.0
        assert rt.vix.crisis == 38.0
        assert rt.usdkrw.danger == 1380.0
        # 오버라이드 안 한 필드는 기본값
        assert rt.oil.shock_pct == 5.0

    def test_load_partial_yaml(self):
        """일부 섹션만 있는 YAML."""
        content = """
vix:
  calm: 10
  normal_low: 15
  normal_high: 20
  fear: 25
  panic: 30
  crisis: 35
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            rt = load_risk_thresholds(Path(f.name))

        assert rt.vix.fear == 25.0
        # usdkrw는 기본값 유지
        assert rt.usdkrw.danger == 1400.0

    def test_load_empty_yaml(self):
        """빈 YAML → 모두 기본값."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            rt = load_risk_thresholds(Path(f.name))

        assert rt.vix.fear == 30.0
        assert rt.usdkrw.danger == 1400.0

    def test_adaptive_intervals_loaded(self):
        """adaptive_intervals 로드 확인."""
        rt = load_risk_thresholds(Path("config/risk_thresholds.yaml"))
        assert "calm" in rt.adaptive_intervals
        assert rt.adaptive_intervals["calm"]["intraday_monitor"] == 120
        assert rt.adaptive_intervals["panic"]["intraday_monitor"] == 15


# =====================================================================
# 6. 싱글턴 캐시 테스트
# =====================================================================


class TestSingleton:

    def test_get_risk_thresholds_returns_same_object(self):
        """싱글턴이 동일 객체 반환하는지."""
        import kstock.core.risk_config as mod
        # 싱글턴 초기화
        mod._thresholds = None
        a = mod.get_risk_thresholds()
        b = mod.get_risk_thresholds()
        assert a is b

    def test_singleton_reset(self):
        """_thresholds = None으로 리셋 가능."""
        import kstock.core.risk_config as mod
        mod._thresholds = None
        rt = mod.get_risk_thresholds()
        assert rt is not None
        mod._thresholds = None  # cleanup


# =====================================================================
# 7. 소비자 함수 통합 테스트 — risk_policy
# =====================================================================


class TestRiskPolicyConsumer:
    """risk_policy.py가 risk_config 임계값을 올바르게 사용하는지."""

    def test_vix_adjusted_policy_extreme(self):
        from kstock.core.risk_policy import vix_adjusted_policy
        p = vix_adjusted_policy(vix=31.0)
        assert p["new_buy_allowed"] is False
        assert p["leverage_allowed"] is False
        assert "극긴축" in p["regime_label"]

    def test_vix_adjusted_policy_fear(self):
        from kstock.core.risk_policy import vix_adjusted_policy
        p = vix_adjusted_policy(vix=26.0)
        assert p["new_buy_allowed"] is True
        assert p["leverage_allowed"] is False
        assert "긴축" in p["regime_label"]

    def test_vix_adjusted_policy_calm(self):
        from kstock.core.risk_policy import vix_adjusted_policy
        p = vix_adjusted_policy(vix=15.0)
        assert p["new_buy_allowed"] is True
        assert p["leverage_allowed"] is True
        assert "완화" in p["regime_label"]

    def test_vix_adjusted_policy_neutral(self):
        from kstock.core.risk_policy import vix_adjusted_policy
        p = vix_adjusted_policy(vix=22.0)
        assert p["new_buy_allowed"] is True
        assert "기본" in p["regime_label"]

    @pytest.mark.parametrize("vix, expect_buy", [
        (17.9, True),   # calm → 완화
        (18.0, True),   # neutral → 기본
        (25.0, True),   # fear boundary → 기본 (> needed)
        (25.1, True),   # fear → 긴축 (매수는 허용)
        (30.0, True),   # fear boundary → 긴축
        (30.1, False),  # panic → 극긴축 (매수 차단)
    ])
    def test_buy_allowed_boundary(self, vix: float, expect_buy: bool):
        from kstock.core.risk_policy import vix_adjusted_policy
        p = vix_adjusted_policy(vix=vix)
        assert p["new_buy_allowed"] == expect_buy, (
            f"VIX={vix}: expected buy={expect_buy}, got {p['new_buy_allowed']}"
        )

    def test_constraint_set_regime(self):
        from kstock.core.risk_policy import RiskConstraintSet
        cs = RiskConstraintSet()
        # VIX > fear(30) → min_cash=0.40
        tight = cs.for_regime(31.0)
        assert tight.min_cash_pct == 0.40
        assert tight.max_leverage == 0.0

        # VIX < normal_low(18) → min_cash=0.05
        relax = cs.for_regime(15.0)
        assert relax.min_cash_pct == 0.05


# =====================================================================
# 8. 소비자 함수 통합 — korea_risk
# =====================================================================


class TestKoreaRiskConsumer:
    """korea_risk.py VIX/USDKRW 경계값 검증."""

    def test_vix_30_adds_panic_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(vix=30.0)
        names = [f["name"] for f in r.factors]
        assert "VIX 패닉" in names

    def test_vix_25_adds_fear_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(vix=25.0)
        names = [f["name"] for f in r.factors]
        assert "VIX 공포" in names

    def test_vix_24_no_vix_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(vix=24.0)
        names = [f["name"] for f in r.factors]
        assert "VIX 패닉" not in names
        assert "VIX 공포" not in names

    def test_usdkrw_1400_adds_danger_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(usdkrw=1400.0)
        names = [f["name"] for f in r.factors]
        assert "환율 고위험" in names

    def test_usdkrw_1350_adds_warning_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(usdkrw=1350.0)
        names = [f["name"] for f in r.factors]
        assert "환율 주의" in names

    def test_usdkrw_1349_no_factor(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(usdkrw=1349.0)
        krw_factors = [f for f in r.factors if "환율" in f["name"]]
        assert len(krw_factors) == 0

    # ── 만기일 플래그 ──────────────────────────────────────

    def test_expiry_day(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(days_to_expiry=1)
        names = [f["name"] for f in r.factors]
        assert "만기일 당일" in names

    def test_expiry_near(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(days_to_expiry=3)
        names = [f["name"] for f in r.factors]
        assert "만기일 접근" in names

    def test_expiry_far(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(days_to_expiry=10)
        names = [f["name"] for f in r.factors]
        assert "만기일 당일" not in names
        assert "만기일 접근" not in names

    # ── 계절 리스크 (양도세 시즌) ──────────────────────────

    def test_year_end_tax_selling(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(month=12, day=20)
        names = [f["name"] for f in r.factors]
        assert "대주주 양도세 매도" in names

    def test_early_december(self):
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(month=12, day=5)
        names = [f["name"] for f in r.factors]
        assert "연말 양도세 시즌" in names

    # ── 복합 시나리오 ─────────────────────────────────────

    def test_full_stress(self):
        """VIX 패닉 + USDKRW 위기 + 만기일 → 리스크 합산."""
        from kstock.signal.korea_risk import assess_korea_risk
        r = assess_korea_risk(vix=35, usdkrw=1420, days_to_expiry=1)
        assert r.total_risk >= 35  # VIX(15) + KRW(10) + 만기(10) = 35+


# =====================================================================
# 9. 소비자 함수 통합 — volatility_regime
# =====================================================================


class TestVolatilityRegimeConsumer:
    """volatility_regime.py VIX 경계값 검증."""

    def test_extreme_regime(self):
        from kstock.signal.volatility_regime import classify_volatility_regime
        r = classify_volatility_regime(vix=36, korean_vol=40)
        assert r.level == "extreme"
        assert r.position_factor < 0.5

    def test_high_regime(self):
        from kstock.signal.volatility_regime import classify_volatility_regime
        r = classify_volatility_regime(vix=26, korean_vol=0)
        assert r.level == "high"

    def test_low_regime(self):
        from kstock.signal.volatility_regime import classify_volatility_regime
        r = classify_volatility_regime(vix=12, korean_vol=10)
        assert r.level == "low"
        assert r.position_factor >= 1.0


# =====================================================================
# 10. 소비자 함수 통합 — contrarian_signal
# =====================================================================


class TestContrarianConsumer:
    """contrarian_signal.py VIX 상수가 risk_config에서 로드되는지."""

    def test_constants_match_config(self):
        from kstock.signal.contrarian_signal import VIX_EXTREME_FEAR, VIX_FEAR, VIX_GREED
        from kstock.core.risk_config import get_risk_thresholds
        vt = get_risk_thresholds().vix
        assert VIX_EXTREME_FEAR == vt.fear   # 30
        assert VIX_FEAR == vt.normal_high    # 25
        assert VIX_GREED == vt.calm          # 15


# =====================================================================
# 11. macro_shock THRESHOLDS 연동 테스트
# =====================================================================


class TestMacroShockConsumer:
    """macro_shock.py THRESHOLDS가 risk_config에서 로드되는지."""

    def test_thresholds_loaded(self):
        from kstock.core.macro_shock import THRESHOLDS
        assert "oil" in THRESHOLDS
        assert "us_futures" in THRESHOLDS
        assert "vix" in THRESHOLDS  # macro_shock uses "vix" not "vix_change"

    def test_oil_thresholds_match_config(self):
        from kstock.core.macro_shock import THRESHOLDS, ShockGrade
        from kstock.core.risk_config import get_risk_thresholds
        rt = get_risk_thresholds()
        oil = THRESHOLDS["oil"]
        # THRESHOLDS는 [(grade, threshold), ...] 형태
        values = {g: th for g, th in oil}
        assert values[ShockGrade.WATCH] == rt.oil.watch_pct
        assert values[ShockGrade.SHOCK] == rt.oil.shock_pct


# =====================================================================
# 12. 경계값 표 (문서화 겸 회귀 테스트)
# =====================================================================


class TestBoundaryTable:
    """핵심 경계값이 변경되지 않았는지 스냅샷 테스트."""

    def test_vix_boundary_snapshot(self):
        vt = VixThresholds()
        assert (vt.calm, vt.normal_low, vt.normal_high, vt.fear, vt.panic, vt.crisis) == (
            15.0, 18.0, 25.0, 30.0, 35.0, 40.0
        )

    def test_usdkrw_boundary_snapshot(self):
        ut = UsdkrwThresholds()
        assert (ut.favorable, ut.normal_low, ut.normal_high, ut.warning, ut.danger, ut.crisis) == (
            1200.0, 1250.0, 1300.0, 1350.0, 1400.0, 1450.0
        )

    def test_shock_defaults_snapshot(self):
        rt = RiskThresholds()
        assert (rt.oil.watch_pct, rt.oil.alert_pct, rt.oil.shock_pct) == (2.0, 3.0, 5.0)
        assert (rt.us_futures.watch_pct, rt.us_futures.alert_pct, rt.us_futures.shock_pct) == (1.0, 1.5, 2.5)
        assert (rt.vix_change.watch_pct, rt.vix_change.alert_pct, rt.vix_change.shock_pct) == (15.0, 25.0, 40.0)
