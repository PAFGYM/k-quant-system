"""Tests for kstock.signal.swing_trader module."""

from __future__ import annotations

import pytest

from kstock.signal.swing_trader import (
    SWING_RULES,
    SwingSignal,
    compute_swing_size,
    evaluate_swing,
    format_swing_alert,
)


# ---------------------------------------------------------------------------
# evaluate_swing
# ---------------------------------------------------------------------------
class TestEvaluateSwing:
    def test_all_conditions_met(self):
        sig = evaluate_swing(
            ticker="005930", name="삼성전자",
            current_price=50000, rsi=25.0, bb_pctb=0.1,
            volume_ratio_20d=3.0, macd_signal_cross=1,
            confidence_score=120.0, ml_prob=0.8,
        )
        assert sig is not None
        assert sig.ticker == "005930"
        assert sig.name == "삼성전자"
        assert sig.entry_price == 50000
        assert sig.target_price > sig.entry_price
        assert sig.stop_price < sig.entry_price
        assert len(sig.reasons) == 6

    def test_exactly_3_conditions(self):
        sig = evaluate_swing(
            ticker="005930", name="삼성전자",
            current_price=50000, rsi=25.0, bb_pctb=0.1,
            volume_ratio_20d=3.0, macd_signal_cross=0,
            confidence_score=50.0, ml_prob=0.3,
        )
        assert sig is not None
        assert len(sig.reasons) == 3

    def test_2_conditions_returns_none(self):
        sig = evaluate_swing(
            ticker="005930", name="삼성전자",
            current_price=50000, rsi=25.0, bb_pctb=0.1,
            volume_ratio_20d=1.0, macd_signal_cross=0,
            confidence_score=50.0, ml_prob=0.3,
        )
        assert sig is None

    def test_no_conditions_returns_none(self):
        sig = evaluate_swing(
            ticker="005930", name="삼성전자",
            current_price=50000, rsi=60.0, bb_pctb=0.8,
            volume_ratio_20d=0.5, macd_signal_cross=0,
            confidence_score=50.0, ml_prob=0.3,
        )
        assert sig is None

    def test_rsi_boundary(self):
        sig = evaluate_swing(
            ticker="X", name="T", current_price=10000,
            rsi=30.0, bb_pctb=0.1, volume_ratio_20d=2.5,
            macd_signal_cross=0, confidence_score=50.0, ml_prob=0.3,
        )
        assert sig is not None
        assert any("RSI" in r for r in sig.reasons)

    def test_rsi_above_30_no_trigger(self):
        sig = evaluate_swing(
            ticker="X", name="T", current_price=10000,
            rsi=31.0, bb_pctb=0.1, volume_ratio_20d=2.5,
            macd_signal_cross=0, confidence_score=50.0, ml_prob=0.3,
        )
        assert sig is None

    def test_target_price_calculation(self):
        sig = evaluate_swing(
            ticker="X", name="T", current_price=100000,
            rsi=20.0, bb_pctb=0.05, volume_ratio_20d=5.0,
            macd_signal_cross=1, confidence_score=130.0, ml_prob=0.9,
        )
        assert sig is not None
        assert sig.target_price == 110000
        assert sig.stop_price == 95000

    def test_hold_days_high_confidence(self):
        sig = evaluate_swing(
            ticker="X", name="T", current_price=10000,
            rsi=20.0, bb_pctb=0.05, volume_ratio_20d=5.0,
            macd_signal_cross=1, confidence_score=130.0, ml_prob=0.9,
        )
        assert sig is not None
        assert sig.hold_days == 7

    def test_hold_days_lower_confidence(self):
        sig = evaluate_swing(
            ticker="X", name="T", current_price=10000,
            rsi=20.0, bb_pctb=0.05, volume_ratio_20d=5.0,
            macd_signal_cross=0, confidence_score=110.0, ml_prob=0.3,
        )
        assert sig is not None
        assert sig.hold_days == 5

    def test_message_populated(self):
        sig = evaluate_swing(
            ticker="X", name="테스트", current_price=10000,
            rsi=20.0, bb_pctb=0.05, volume_ratio_20d=5.0,
            macd_signal_cross=1, confidence_score=130.0, ml_prob=0.9,
        )
        assert sig is not None
        assert "스윙 매수 추천" in sig.message


# ---------------------------------------------------------------------------
# compute_swing_size
# ---------------------------------------------------------------------------
class TestComputeSwingSize:
    def test_default_25pct(self):
        result = compute_swing_size(total_eval=100_000_000)
        assert result == 25_000_000

    def test_custom_allocation(self):
        result = compute_swing_size(total_eval=100_000_000, swing_allocation_pct=10.0)
        assert result == 10_000_000

    def test_zero_portfolio(self):
        result = compute_swing_size(total_eval=0)
        assert result == 0.0


# ---------------------------------------------------------------------------
# format_swing_alert
# ---------------------------------------------------------------------------
class TestFormatSwingAlert:
    def test_basic_format(self):
        sig = SwingSignal(
            ticker="005930", name="SK하이닉스",
            entry_price=52000, target_price=57200,
            stop_price=49400, target_pct=10.0, stop_pct=-5.0,
            hold_days=7, confidence=120.0,
            reasons=["과매도 반등 (RSI 28.5)", "볼린저 하단 (%B 0.15)", "거래량 급증 (2.3배)"],
        )
        result = format_swing_alert(sig)
        assert "스윙 매수 추천" in result
        assert "SK하이닉스" in result
        assert "52,000원" in result
        assert "57,200원" in result
        assert "49,400원" in result

    def test_no_bold(self):
        sig = SwingSignal(
            ticker="X", name="테스트",
            entry_price=10000, target_price=11000,
            stop_price=9500, target_pct=10.0, stop_pct=-5.0,
            hold_days=5, confidence=100.0, reasons=["a", "b", "c"],
        )
        result = format_swing_alert(sig)
        assert "**" not in result

    def test_hold_label_format(self):
        sig = SwingSignal(
            ticker="X", name="테스트",
            entry_price=10000, target_price=11000,
            stop_price=9500, target_pct=10.0, stop_pct=-5.0,
            hold_days=7, confidence=100.0, reasons=["a", "b", "c"],
        )
        result = format_swing_alert(sig)
        assert "5~7일" in result

    def test_empty_reasons(self):
        sig = SwingSignal(
            ticker="X", name="테스트",
            entry_price=10000, target_price=11000,
            stop_price=9500, target_pct=10.0, stop_pct=-5.0,
            hold_days=5, confidence=100.0, reasons=[],
        )
        result = format_swing_alert(sig)
        assert "근거:" not in result


# ---------------------------------------------------------------------------
# SWING_RULES
# ---------------------------------------------------------------------------
class TestSwingRules:
    def test_hold_period(self):
        assert SWING_RULES["hold_period_days"]["min"] == 3
        assert SWING_RULES["hold_period_days"]["max"] == 10

    def test_target_pct(self):
        assert SWING_RULES["target_pct"]["min"] == 5.0
        assert SWING_RULES["target_pct"]["max"] == 15.0

    def test_stop_pct(self):
        assert SWING_RULES["stop_pct"] == -5.0

    def test_min_conditions(self):
        assert SWING_RULES["min_conditions"] == 3
