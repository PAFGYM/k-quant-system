"""v12.4: domain_types 통합 테스트.

테스트 범위:
1. TradeSignal — 생성, to_dict, from_dict, extra 키 처리
2. RiskDecision — 생성, to_tuple, __bool__, from_dict, to_dict
3. RiskDecision.from_market_state — VIX 레짐별, USDKRW 등급별, 만기일, 쇼크, 복합
4. HoldingItem — 생성, from_dict
5. PortfolioSnapshot — 생성, from_legacy_dict, holding_count
"""
from __future__ import annotations

import pytest

from kstock.core.domain_types import (
    HoldingItem,
    PortfolioSnapshot,
    RiskDecision,
    TradeSignal,
)


# =====================================================================
# TradeSignal
# =====================================================================

class TestTradeSignal:

    def test_defaults(self):
        s = TradeSignal()
        assert s.ticker == ""
        assert s.signal == "HOLD"
        assert s.score == 0.0
        assert s.reasons == []
        assert s.extra == {}

    def test_to_dict(self):
        s = TradeSignal(ticker="005930", signal="BUY", score=72)
        d = s.to_dict()
        assert d["ticker"] == "005930"
        assert d["signal"] == "BUY"
        assert d["score"] == 72

    def test_to_dict_timestamp_iso(self):
        from datetime import datetime
        ts = datetime(2026, 3, 12, 9, 0, 0)
        s = TradeSignal(ticker="005930", timestamp=ts)
        d = s.to_dict()
        assert d["timestamp"] == "2026-03-12T09:00:00"

    def test_from_dict_known_keys(self):
        d = {"ticker": "005930", "signal": "BUY", "score": 72}
        s = TradeSignal.from_dict(d)
        assert s.ticker == "005930"
        assert s.signal == "BUY"
        assert s.score == 72

    def test_from_dict_unknown_keys_go_to_extra(self):
        d = {"ticker": "005930", "custom_flag": True, "ai_note": "good"}
        s = TradeSignal.from_dict(d)
        assert s.ticker == "005930"
        assert s.extra["custom_flag"] is True
        assert s.extra["ai_note"] == "good"

    def test_roundtrip(self):
        original = TradeSignal(ticker="035720", signal="WATCH", score=55,
                               reasons=["매집 패턴"], tags=["swing"])
        d = original.to_dict()
        restored = TradeSignal.from_dict(d)
        assert restored.ticker == original.ticker
        assert restored.signal == original.signal
        assert restored.reasons == original.reasons


# =====================================================================
# RiskDecision — 기본 동작
# =====================================================================

class TestRiskDecisionBasic:

    def test_defaults(self):
        rd = RiskDecision()
        assert rd.allowed is True
        assert rd.reason == ""
        assert rd.risk_level == "normal"
        assert rd.block_new_buy is False
        assert rd.reduce_position is False
        assert rd.max_position_pct == 100.0
        assert rd.cash_floor_pct == 0.0
        assert rd.reasons == []
        assert rd.source_flags == []

    def test_to_tuple_compat(self):
        rd = RiskDecision(allowed=False, reason="VIX 급등")
        ok, msg = rd.to_tuple()
        assert ok is False
        assert msg == "VIX 급등"

    def test_bool_true(self):
        assert bool(RiskDecision(allowed=True)) is True

    def test_bool_false(self):
        assert bool(RiskDecision(allowed=False)) is False

    def test_to_dict(self):
        rd = RiskDecision(allowed=False, risk_level="blocked", vix=35.0)
        d = rd.to_dict()
        assert isinstance(d, dict)
        assert d["allowed"] is False
        assert d["risk_level"] == "blocked"
        assert d["vix"] == 35.0

    def test_from_dict(self):
        d = {"allowed": False, "reason": "test", "risk_level": "danger"}
        rd = RiskDecision.from_dict(d)
        assert rd.allowed is False
        assert rd.reason == "test"

    def test_from_dict_ignores_unknown(self):
        d = {"allowed": True, "unknown_field": 999}
        rd = RiskDecision.from_dict(d)
        assert rd.allowed is True

    def test_mutable_reasons(self):
        """RiskDecision은 frozen이 아니므로 reasons 추가 가능."""
        rd = RiskDecision()
        rd.reasons.append("추가 사유")
        assert "추가 사유" in rd.reasons

    def test_independent_list_fields(self):
        """각 인스턴스의 list 필드가 독립적."""
        rd1 = RiskDecision()
        rd2 = RiskDecision()
        rd1.reasons.append("rd1 only")
        assert rd2.reasons == []


# =====================================================================
# RiskDecision.from_market_state — VIX 레짐별
# =====================================================================

class TestFromMarketStateVix:

    def test_calm(self):
        rd = RiskDecision.from_market_state(vix=14)
        assert rd.regime == "calm"
        assert rd.allowed is True
        assert rd.block_new_buy is False
        assert rd.risk_level == "normal"

    def test_normal(self):
        rd = RiskDecision.from_market_state(vix=20)
        assert rd.regime == "normal"
        assert rd.allowed is True
        assert rd.risk_level == "normal"

    def test_fear(self):
        rd = RiskDecision.from_market_state(vix=28)
        assert rd.regime == "fear"
        assert rd.allowed is True
        assert rd.cash_floor_pct == 25.0
        assert rd.risk_level == "warning"

    def test_panic(self):
        rd = RiskDecision.from_market_state(vix=36)
        assert rd.regime == "panic"
        assert rd.allowed is False
        assert rd.block_new_buy is True
        assert rd.reduce_position is True
        assert rd.cash_floor_pct == 40.0
        assert rd.risk_level == "blocked"

    def test_crisis(self):
        rd = RiskDecision.from_market_state(vix=45)
        assert rd.regime == "crisis"
        assert rd.allowed is False
        assert rd.block_new_buy is True
        assert rd.reduce_position is True

    def test_vix_zero_no_regime(self):
        rd = RiskDecision.from_market_state(vix=0)
        assert rd.regime == ""
        assert rd.vix_status == ""

    def test_vix_status_label_fear(self):
        rd = RiskDecision.from_market_state(vix=31)
        assert rd.vix_status == "공포"

    def test_vix_status_label_caution(self):
        rd = RiskDecision.from_market_state(vix=26)
        assert rd.vix_status == "경계"

    def test_vix_score_override_30(self):
        """VIX >= 30이면 risk_score 최소 60."""
        rd = RiskDecision.from_market_state(vix=31, korea_risk_score=10)
        assert rd.risk_score >= 60

    def test_vix_score_override_25(self):
        """VIX >= 25이면 risk_score 최소 40."""
        rd = RiskDecision.from_market_state(vix=26, korea_risk_score=10)
        assert rd.risk_score >= 40

    def test_max_position_pct_panic(self):
        rd = RiskDecision.from_market_state(vix=36)
        assert rd.max_position_pct == 60.0

    def test_max_position_pct_fear(self):
        rd = RiskDecision.from_market_state(vix=28)
        assert rd.max_position_pct == 80.0

    def test_max_position_pct_calm(self):
        rd = RiskDecision.from_market_state(vix=14)
        assert rd.max_position_pct == 100.0


# =====================================================================
# RiskDecision.from_market_state — USDKRW 등급별
# =====================================================================

class TestFromMarketStateUsdkrw:

    def test_favorable(self):
        rd = RiskDecision.from_market_state(usdkrw=1180)
        assert rd.usdkrw_status == "강세"

    def test_normal_low(self):
        rd = RiskDecision.from_market_state(usdkrw=1260)
        assert rd.usdkrw_status == "안정"

    def test_normal_high(self):
        rd = RiskDecision.from_market_state(usdkrw=1310)
        assert rd.usdkrw_status == "주의"

    def test_warning(self):
        rd = RiskDecision.from_market_state(usdkrw=1360)
        assert rd.usdkrw_status == "경고"

    def test_danger(self):
        rd = RiskDecision.from_market_state(usdkrw=1410)
        assert rd.usdkrw_status == "위험"
        assert rd.allowed is True  # danger는 아직 차단 아님

    def test_crisis_blocks_buy(self):
        rd = RiskDecision.from_market_state(usdkrw=1460)
        assert rd.usdkrw_status == "위기"
        assert rd.block_new_buy is True
        assert rd.allowed is False

    def test_usdkrw_zero_no_status(self):
        rd = RiskDecision.from_market_state(usdkrw=0)
        assert rd.usdkrw_status == ""

    def test_usdkrw_reason_included(self):
        rd = RiskDecision.from_market_state(usdkrw=1360)
        assert any("환율" in r for r in rd.reasons)


# =====================================================================
# v12.6: 환율 모멘텀 + 복합 심각도
# =====================================================================

class TestUsdkrwMomentum:

    def test_no_change_no_momentum(self):
        rd = RiskDecision.from_market_state(usdkrw=1350)
        assert rd.usdkrw_momentum == ""
        assert rd.usdkrw_change_pct == 0.0

    def test_surge(self):
        """usdkrw +1.2% → 급등."""
        rd = RiskDecision.from_market_state(usdkrw=1350, usdkrw_change_pct=1.2)
        assert rd.usdkrw_momentum == "급등"
        assert any("급등" in r for r in rd.reasons)

    def test_plunge(self):
        """usdkrw -1.2% → 급락."""
        rd = RiskDecision.from_market_state(usdkrw=1350, usdkrw_change_pct=-1.2)
        assert rd.usdkrw_momentum == "급락"

    def test_rise(self):
        rd = RiskDecision.from_market_state(usdkrw=1300, usdkrw_change_pct=0.6)
        assert rd.usdkrw_momentum == "상승"

    def test_fall(self):
        rd = RiskDecision.from_market_state(usdkrw=1300, usdkrw_change_pct=-0.7)
        assert rd.usdkrw_momentum == "하락"

    def test_extreme_both_directions(self):
        """±1.5% → 급변."""
        rd1 = RiskDecision.from_market_state(usdkrw=1300, usdkrw_change_pct=1.8)
        assert rd1.usdkrw_momentum == "급변"
        rd2 = RiskDecision.from_market_state(usdkrw=1300, usdkrw_change_pct=-1.6)
        assert rd2.usdkrw_momentum == "급변"

    def test_composite_escalation_danger_plus_surge(self):
        """위험(1400) + 2% → composite >= 0.8 → 매수 차단."""
        rd = RiskDecision.from_market_state(usdkrw=1400, usdkrw_change_pct=2.0)
        assert rd.usdkrw_composite >= 0.8
        assert rd.block_new_buy is True
        assert any("복합 위험" in r for r in rd.reasons)

    def test_composite_no_escalation_warning_moderate(self):
        """경고(1350) + 0.8% → composite < 0.8 → 차단 안 됨."""
        rd = RiskDecision.from_market_state(usdkrw=1350, usdkrw_change_pct=0.8)
        assert rd.usdkrw_composite < 0.8
        assert rd.block_new_buy is False

    def test_composite_zero_when_favorable(self):
        """강세(1180) + 0% → composite ~0."""
        rd = RiskDecision.from_market_state(usdkrw=1180)
        assert rd.usdkrw_composite < 0.05

    def test_backward_compat_no_change_pct(self):
        """usdkrw_change_pct 미제공 → 기존 동작 유지."""
        rd = RiskDecision.from_market_state(usdkrw=1400)
        assert rd.usdkrw_status == "위험"
        assert rd.usdkrw_momentum == ""
        assert rd.block_new_buy is False  # 절대값만으로는 danger, 차단 아님


class TestVixUsdkrwCross:

    def test_foreign_outflow_pattern(self):
        """VIX 28 + USDKRW +0.7% → 외인 이탈."""
        rd = RiskDecision.from_market_state(vix=28, usdkrw=1300, usdkrw_change_pct=0.7)
        assert "foreign_outflow_pattern" in rd.source_flags
        assert rd.cash_floor_pct >= 20.0
        assert any("외인 이탈" in r for r in rd.reasons)

    def test_no_cross_when_vix_low(self):
        """VIX 20 + USDKRW +0.7% → 교차 안 됨."""
        rd = RiskDecision.from_market_state(vix=20, usdkrw=1300, usdkrw_change_pct=0.7)
        assert "foreign_outflow_pattern" not in rd.source_flags

    def test_no_cross_when_krw_stable(self):
        """VIX 28 + USDKRW +0.3% → 교차 안 됨."""
        rd = RiskDecision.from_market_state(vix=28, usdkrw=1300, usdkrw_change_pct=0.3)
        assert "foreign_outflow_pattern" not in rd.source_flags

    def test_cross_stacks_with_fear_cash(self):
        """VIX fear(28) + 교차 → cash_floor = max(25, 20) = 25."""
        rd = RiskDecision.from_market_state(vix=28, usdkrw=1300, usdkrw_change_pct=0.6)
        assert rd.cash_floor_pct >= 25.0  # fear 25% > 교차 20%


# =====================================================================
# RiskDecision.from_market_state — 만기일 + 쇼크
# =====================================================================

class TestFromMarketStateExpiry:

    def test_expiry_day(self):
        rd = RiskDecision.from_market_state(days_to_expiry=1)
        assert "expiry_day" in rd.source_flags
        assert any("만기" in r for r in rd.reasons)

    def test_expiry_near(self):
        rd = RiskDecision.from_market_state(days_to_expiry=2)
        assert "expiry_near" in rd.source_flags

    def test_expiry_far(self):
        rd = RiskDecision.from_market_state(days_to_expiry=10)
        assert rd.source_flags == []

    def test_shock_blocks_buy(self):
        rd = RiskDecision.from_market_state(shock_grade="SHOCK")
        assert rd.block_new_buy is True
        assert "shock_SHOCK" in rd.source_flags
        assert any("쇼크" in r for r in rd.reasons)

    def test_alert_no_block(self):
        rd = RiskDecision.from_market_state(shock_grade="ALERT")
        assert rd.block_new_buy is False
        assert "shock_ALERT" in rd.source_flags

    def test_none_grade(self):
        rd = RiskDecision.from_market_state(shock_grade="NONE")
        assert rd.source_flags == []


# =====================================================================
# RiskDecision.from_market_state — 복합 시나리오
# =====================================================================

class TestFromMarketStateComposite:

    def test_panic_vix_plus_crisis_usdkrw(self):
        """패닉 VIX + 환율 위기 → 최악 시나리오."""
        rd = RiskDecision.from_market_state(vix=38, usdkrw=1460)
        assert rd.allowed is False
        assert rd.block_new_buy is True
        assert rd.reduce_position is True
        assert rd.risk_level == "blocked"
        assert len(rd.reasons) >= 2

    def test_fear_vix_plus_expiry(self):
        """공포 VIX + 만기일 → 차단 아님, warning + expiry flag."""
        rd = RiskDecision.from_market_state(vix=28, days_to_expiry=1)
        assert rd.allowed is True
        assert rd.cash_floor_pct == 25.0
        assert "expiry_day" in rd.source_flags

    def test_normal_with_high_korea_risk(self):
        """VIX 정상 + korea_risk_score 높음 → caution."""
        rd = RiskDecision.from_market_state(vix=20, korea_risk_score=55)
        assert rd.risk_level == "caution"
        assert rd.allowed is True

    def test_shock_overrides_normal_vix(self):
        """VIX 정상이어도 SHOCK이면 차단."""
        rd = RiskDecision.from_market_state(vix=18, shock_grade="SHOCK")
        assert rd.allowed is False
        assert rd.block_new_buy is True

    def test_source_field(self):
        rd = RiskDecision.from_market_state(source="test_module")
        assert rd.source == "test_module"

    def test_default_source(self):
        rd = RiskDecision.from_market_state()
        assert rd.source == "market_state"

    def test_all_clear(self):
        """모든 지표 정상 → 완전 클리어."""
        rd = RiskDecision.from_market_state(
            vix=14, usdkrw=1200, days_to_expiry=30,
            shock_grade="NONE", korea_risk_score=10)
        assert rd.allowed is True
        assert rd.risk_level == "normal"
        assert rd.block_new_buy is False
        assert rd.reduce_position is False
        assert rd.cash_floor_pct == 0.0
        assert rd.max_position_pct == 100.0


# =====================================================================
# HoldingItem
# =====================================================================

class TestHoldingItem:

    def test_defaults(self):
        h = HoldingItem()
        assert h.ticker == ""
        assert h.quantity == 0

    def test_from_dict(self):
        d = {"ticker": "005930", "name": "삼성전자", "quantity": 10}
        h = HoldingItem.from_dict(d)
        assert h.ticker == "005930"
        assert h.quantity == 10

    def test_to_dict(self):
        h = HoldingItem(ticker="005930", quantity=10)
        d = h.to_dict()
        assert d["ticker"] == "005930"


# =====================================================================
# PortfolioSnapshot
# =====================================================================

class TestPortfolioSnapshot:

    def test_defaults(self):
        ps = PortfolioSnapshot()
        assert ps.holdings == []
        assert ps.holding_count == 0

    def test_holding_count(self):
        ps = PortfolioSnapshot(holdings=[HoldingItem(), HoldingItem()])
        assert ps.holding_count == 2

    def test_from_legacy_dict(self):
        legacy = {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "quantity": 10,
                 "avg_price": 70000, "current_price": 75000,
                 "profit_pct": 7.14, "eval_amount": 750000},
            ],
            "total_eval": 750000,
            "total_profit": 50000,
            "cash": 250000,
            "_pit_source": "kis_realtime",
        }
        ps = PortfolioSnapshot.from_legacy_dict(legacy)
        assert ps.holding_count == 1
        assert ps.holdings[0].ticker == "005930"
        assert ps.total_eval == 750000
        assert ps.cash == 250000
        assert ps.is_realtime is True
        assert ps.source == "kis_realtime"

    def test_from_legacy_dict_empty(self):
        ps = PortfolioSnapshot.from_legacy_dict({})
        assert ps.holding_count == 0
        assert ps.total_eval == 0

    def test_to_dict(self):
        ps = PortfolioSnapshot(total_eval=1000000, cash=200000)
        d = ps.to_dict()
        assert d["total_eval"] == 1000000
        assert d["cash"] == 200000

    def test_to_dict_timestamp_iso(self):
        from datetime import datetime
        ps = PortfolioSnapshot(fetched_at=datetime(2026, 3, 12, 9, 0))
        d = ps.to_dict()
        assert d["fetched_at"] == "2026-03-12T09:00:00"
