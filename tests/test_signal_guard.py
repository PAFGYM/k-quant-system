"""Tests for signal_guard — 장기보유 보호 + 신뢰도 등급 엔진."""
from __future__ import annotations

import pytest

from kstock.signal.signal_guard import (
    HoldingGuardResult,
    SignalReliability,
    apply_holding_guard,
    compute_signal_reliability,
    format_guard_result,
    format_reliability,
    format_reliability_detail,
)


# ---------------------------------------------------------------------------
# 1. apply_holding_guard 테스트
# ---------------------------------------------------------------------------


class TestApplyHoldingGuard:
    """장기보유 보호 로직 테스트."""

    def test_buy_signal_not_suppressed(self):
        """매수 신호는 억제 대상이 아님."""
        result = apply_holding_guard(
            consensus="BUY", holding_type="long_term",
            hold_days=300, pnl_pct=-5.0,
        )
        assert not result.suppressed
        assert result.adjusted_consensus == "BUY"
        assert "매도 신호가 아님" in result.reason

    def test_hold_signal_not_suppressed(self):
        """HOLD 신호는 억제 대상이 아님."""
        result = apply_holding_guard(
            consensus="HOLD", holding_type="long_term",
        )
        assert not result.suppressed

    def test_scalp_sell_not_suppressed(self):
        """단타 종목은 매도 보호 없음."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="scalp",
            hold_days=1, pnl_pct=-5.0,
        )
        assert not result.suppressed
        assert "단타 종목" in result.reason

    def test_long_term_sell_suppressed(self):
        """장기 보유 종목의 일반적 매도는 억제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=120, pnl_pct=-5.0,
            confidence=0.6, agreement=0.5,
        )
        assert result.suppressed
        assert result.adjusted_consensus == "HOLD"
        assert "장기 (버핏)" in result.reason

    def test_position_sell_suppressed(self):
        """포지션 보유 종목 매도 억제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="position",
            hold_days=30, pnl_pct=-3.0,
        )
        assert result.suppressed
        assert result.adjusted_consensus == "HOLD"

    def test_swing_sell_suppressed(self):
        """스윙 종목도 기본적으로 억제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="swing",
            hold_days=3, pnl_pct=-3.0,
            confidence=0.5, agreement=0.4,
        )
        assert result.suppressed

    def test_override_severe_loss(self):
        """심각한 손실 시 보호 해제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=200, pnl_pct=-20.0,
        )
        assert not result.suppressed
        assert "손실" in result.reason
        assert len(result.override_conditions) > 0

    def test_override_panic_market(self):
        """시장 패닉 시 보호 해제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=200, pnl_pct=-5.0,
            market_regime="panic",
        )
        assert not result.suppressed
        assert any("panic" in c for c in result.override_conditions)

    def test_override_crisis_market(self):
        """시장 위기 시 보호 해제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="position",
            hold_days=30, pnl_pct=-5.0,
            market_regime="crisis",
        )
        assert not result.suppressed

    def test_override_unanimous_sell(self):
        """만장일치 매도 시 보호 해제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=200, pnl_pct=-5.0,
            confidence=0.85, agreement=0.95,
        )
        assert not result.suppressed
        assert any("만장일치" in c for c in result.override_conditions)

    def test_override_strong_sell_high_confidence(self):
        """STRONG_SELL + 높은 신뢰도 시 보호 해제."""
        result = apply_holding_guard(
            consensus="STRONG_SELL", holding_type="long_term",
            hold_days=200, pnl_pct=-5.0,
            confidence=0.9, agreement=0.5,
        )
        assert not result.suppressed
        assert any("STRONG_SELL" in c for c in result.override_conditions)

    def test_unknown_holding_type(self):
        """미설정 보유 유형은 보호 없음."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="auto",
        )
        assert not result.suppressed
        assert "미설정" in result.reason

    def test_profitable_long_term_suppressed(self):
        """수익 중인 장기 보유는 억제 + 이유에 수익 언급."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=180, pnl_pct=10.0,
        )
        assert result.suppressed
        assert "수익" in result.reason

    def test_loss_but_above_threshold_suppressed(self):
        """손실이지만 절대 손절 라인 전이면 억제."""
        result = apply_holding_guard(
            consensus="SELL", holding_type="long_term",
            hold_days=100, pnl_pct=-10.0,
            confidence=0.5, agreement=0.5,
        )
        assert result.suppressed
        assert "아직" in result.reason or "손절 라인" in result.reason


# ---------------------------------------------------------------------------
# 2. compute_signal_reliability 테스트
# ---------------------------------------------------------------------------


class TestComputeSignalReliability:
    """신뢰도 등급 계산 테스트."""

    def test_high_reliability(self):
        """높은 신뢰도 → A등급."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=0.9,
            agreement=0.9,
            contributing_count=6,
            total_votes=7,
            signal_source="consensus",
            hit_rate_30d=0.8,
        )
        assert rel.grade == "A"
        assert rel.score >= 75
        assert rel.emoji == "🟢"
        assert rel.warning == ""

    def test_medium_reliability(self):
        """중간 신뢰도 → B등급."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=0.65,
            agreement=0.6,
            contributing_count=4,
            total_votes=7,
            signal_source="multi_agent",
            hit_rate_30d=0.6,
        )
        assert rel.grade in ("A", "B")
        assert rel.score >= 55

    def test_low_reliability(self):
        """낮은 신뢰도 → C 또는 D."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=0.3,
            agreement=0.3,
            contributing_count=1,
            total_votes=2,
            signal_source="surge_detect",
            hit_rate_30d=0.3,
        )
        assert rel.grade in ("C", "D")
        assert rel.score < 55

    def test_panic_regime_penalty(self):
        """패닉 시장 패널티 적용."""
        rel_normal = compute_signal_reliability(
            consensus="BUY",
            confidence=0.7,
            agreement=0.7,
            contributing_count=5,
            total_votes=7,
            market_regime="normal",
        )
        rel_panic = compute_signal_reliability(
            consensus="BUY",
            confidence=0.7,
            agreement=0.7,
            contributing_count=5,
            total_votes=7,
            market_regime="panic",
        )
        assert rel_panic.score < rel_normal.score
        assert "regime_penalty" in rel_panic.factors

    def test_strong_signal_bonus(self):
        """STRONG_BUY/SELL 보너스."""
        rel_buy = compute_signal_reliability(
            consensus="BUY", confidence=0.7,
        )
        rel_strong = compute_signal_reliability(
            consensus="STRONG_BUY", confidence=0.7,
        )
        assert rel_strong.score >= rel_buy.score
        assert "strong_signal_bonus" in rel_strong.factors

    def test_source_quality_consensus(self):
        """consensus 소스 보너스."""
        rel = compute_signal_reliability(
            consensus="BUY", signal_source="consensus",
        )
        assert rel.factors["source_quality"] == 65.0  # 50 + 15

    def test_source_quality_surge(self):
        """surge_detect 소스 패널티."""
        rel = compute_signal_reliability(
            consensus="BUY", signal_source="surge_detect",
        )
        assert rel.factors["source_quality"] == 45.0  # 50 - 5

    def test_source_quality_contrarian(self):
        """contrarian 소스 패널티."""
        rel = compute_signal_reliability(
            consensus="BUY", signal_source="contrarian",
        )
        assert rel.factors["source_quality"] == 47.0  # 50 - 3

    def test_breadth_few_votes(self):
        """투표 수 부족 시 breadth 점수 제한."""
        rel = compute_signal_reliability(
            consensus="BUY",
            contributing_count=1,
            total_votes=2,
        )
        assert rel.factors["breadth"] == 30.0  # 투표 수 부족

    def test_breadth_many_votes(self):
        """투표 수 충분 시 breadth 정상 계산."""
        rel = compute_signal_reliability(
            consensus="BUY",
            contributing_count=5,
            total_votes=7,
        )
        expected = min(5 / 7, 1.0) * 100
        assert rel.factors["breadth"] == round(expected, 1)

    def test_score_clamped_0_100(self):
        """점수는 0~100 범위."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=1.5,  # over 1.0
            agreement=1.5,
        )
        assert 0 <= rel.score <= 100

    def test_factors_populated(self):
        """모든 기본 팩터가 포함됨."""
        rel = compute_signal_reliability(consensus="BUY")
        assert "confidence" in rel.factors
        assert "agreement" in rel.factors
        assert "breadth" in rel.factors
        assert "track_record" in rel.factors
        assert "source_quality" in rel.factors

    def test_d_grade_warning(self):
        """D등급은 경고 메시지 포함."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=0.1,
            agreement=0.1,
            contributing_count=0,
            total_votes=1,
            hit_rate_30d=0.1,
            signal_source="contrarian",
        )
        assert rel.grade == "D"
        assert rel.warning != ""
        assert "매매하지 마세요" in rel.warning

    def test_c_grade_warning(self):
        """C등급은 참고용 경고."""
        rel = compute_signal_reliability(
            consensus="BUY",
            confidence=0.4,
            agreement=0.4,
            contributing_count=2,
            total_votes=5,
            hit_rate_30d=0.4,
        )
        if rel.grade == "C":
            assert "참고용" in rel.warning


# ---------------------------------------------------------------------------
# 3. 포맷 함수 테스트
# ---------------------------------------------------------------------------


class TestFormatFunctions:
    """포맷 함수 테스트."""

    def test_format_guard_result_suppressed(self):
        """억제된 결과 포맷."""
        guard = HoldingGuardResult(
            original_consensus="SELL",
            adjusted_consensus="HOLD",
            suppressed=True,
            reason="장기 (버핏) 보유 120일차",
        )
        text = format_guard_result(guard)
        assert "🛡" in text
        assert "SELL" in text
        assert "HOLD" in text
        assert "120일차" in text

    def test_format_guard_result_not_suppressed(self):
        """미억제 결과 → 빈 문자열."""
        guard = HoldingGuardResult(suppressed=False)
        text = format_guard_result(guard)
        assert text == ""

    def test_format_reliability(self):
        """신뢰도 포맷."""
        rel = SignalReliability(
            grade="A", label="높은 신뢰",
            score=82.0, emoji="🟢",
        )
        text = format_reliability(rel)
        assert "🟢" in text
        assert "A등급" in text
        assert "82" in text

    def test_format_reliability_with_warning(self):
        """경고 포함 신뢰도 포맷."""
        rel = SignalReliability(
            grade="D", label="낮은 신뢰",
            score=25.0, emoji="🔴",
            warning="이 신호만으로 매매하지 마세요",
        )
        text = format_reliability(rel)
        assert "⚠️" in text
        assert "매매하지 마세요" in text

    def test_format_reliability_detail(self):
        """상세 신뢰도 포맷."""
        rel = SignalReliability(
            grade="B", label="양호",
            score=65.0, emoji="🔵",
            factors={
                "confidence": 80.0,
                "agreement": 70.0,
                "breadth": 60.0,
                "track_record": 50.0,
                "source_quality": 65.0,
            },
        )
        text = format_reliability_detail(rel)
        assert "B등급" in text
        assert "신뢰도" in text
        assert "합의도" in text
        assert "█" in text


# ---------------------------------------------------------------------------
# 4. ensemble vote_with_guard 통합 테스트
# ---------------------------------------------------------------------------


class TestVoteWithGuard:
    """vote_with_guard 통합 테스트."""

    def test_basic_vote_with_reliability(self):
        """기본 투표 + 신뢰도 등급."""
        from kstock.signal.ensemble import SignalVote, vote_with_guard

        signals = [
            SignalVote(strategy="A", action="BUY", confidence=0.8, weight=1.0),
            SignalVote(strategy="B", action="BUY", confidence=0.7, weight=1.0),
            SignalVote(strategy="C", action="HOLD", confidence=0.5, weight=1.0),
        ]
        result = vote_with_guard(signals, signal_source="consensus")
        assert result.consensus in ("BUY", "STRONG_BUY")
        assert result.reliability_grade != ""
        assert result.reliability_score > 0

    def test_sell_suppressed_for_long_term(self):
        """장기 보유 종목 매도 억제."""
        from kstock.signal.ensemble import SignalVote, vote_with_guard

        signals = [
            SignalVote(strategy="A", action="SELL", confidence=0.6, weight=1.0),
            SignalVote(strategy="B", action="SELL", confidence=0.5, weight=1.0),
            SignalVote(strategy="C", action="HOLD", confidence=0.5, weight=1.0),
        ]
        result = vote_with_guard(
            signals,
            holding_type="long_term",
            hold_days=200,
            pnl_pct=-5.0,
        )
        assert result.holding_suppressed
        assert result.consensus == "HOLD"
        assert result.original_consensus in ("SELL", "STRONG_SELL")

    def test_sell_not_suppressed_for_scalp(self):
        """단타 종목은 매도 억제 없음."""
        from kstock.signal.ensemble import SignalVote, vote_with_guard

        signals = [
            SignalVote(strategy="A", action="SELL", confidence=0.8, weight=1.0),
            SignalVote(strategy="B", action="SELL", confidence=0.7, weight=1.0),
            SignalVote(strategy="C", action="SELL", confidence=0.6, weight=1.0),
        ]
        result = vote_with_guard(
            signals,
            holding_type="scalp",
            hold_days=2,
            pnl_pct=-3.0,
        )
        assert not result.holding_suppressed
        assert result.consensus in ("SELL", "STRONG_SELL")

    def test_buy_signal_no_suppression(self):
        """매수 신호는 억제 대상이 아님."""
        from kstock.signal.ensemble import SignalVote, vote_with_guard

        signals = [
            SignalVote(strategy="A", action="BUY", confidence=0.9, weight=1.0),
            SignalVote(strategy="B", action="BUY", confidence=0.8, weight=1.0),
        ]
        result = vote_with_guard(
            signals,
            holding_type="long_term",
            hold_days=200,
            pnl_pct=5.0,
        )
        assert not result.holding_suppressed
        assert result.consensus in ("BUY", "STRONG_BUY")


# ---------------------------------------------------------------------------
# 5. smart_alerts 통합 테스트
# ---------------------------------------------------------------------------


class TestSmartAlertsWithGuard:
    """smart_alerts의 signal_guard 통합 테스트."""

    def test_long_term_holding_suppressed_alert(self):
        """장기 보유 종목 매도 억제 시 보호 알림."""
        from kstock.bot.smart_alerts import build_holding_alert

        msg = build_holding_alert(
            name="현대차", ticker="005380",
            pnl_pct=-8.0,
            buy_price=200000,
            current_price=184000,
            holding_type="long_term",
            hold_days=200,
            market_regime="normal",
        )
        # 장기 보유 → 보호 알림 (매도가 아닌 보호 메시지)
        assert msg is not None
        assert "🛡" in msg
        assert "보호" in msg
        assert "보유 유지" in msg

    def test_position_holding_suppressed_alert(self):
        """포지션 보유 종목 매도 억제."""
        from kstock.bot.smart_alerts import build_holding_alert

        msg = build_holding_alert(
            name="에코프로", ticker="086520",
            pnl_pct=-8.0,
            buy_price=100000,
            current_price=92000,
            holding_type="position",
            hold_days=45,
            market_regime="normal",
        )
        assert msg is not None
        assert "🛡" in msg

    def test_swing_sell_not_suppressed(self):
        """스윙 종목은 기존 손절 알림."""
        from kstock.bot.smart_alerts import build_holding_alert

        msg = build_holding_alert(
            name="삼성전자", ticker="005930",
            pnl_pct=-8.0,
            buy_price=70000,
            current_price=64400,
            holding_type="swing",
            hold_days=10,
            market_regime="normal",
        )
        # 스윙은 기존 로직에 의해 -7% 이하이므로 손절 알림
        assert msg is not None
        assert "손절선 점검" in msg

    def test_opportunity_alert_with_reliability(self):
        """매수 기회 알림에 신뢰도 배지."""
        from kstock.bot.smart_alerts import build_opportunity_alert

        msg = build_opportunity_alert(
            name="삼성전자", ticker="005930",
            score=120, signal="BUY",
            reasons=["RSI 과매도", "기관 순매수"],
            signal_source="consensus",
            hit_rate=70, past_recommendations=10,
            reliability_grade="A", reliability_emoji="🟢",
        )
        assert "🟢 A등급" in msg
