"""Tests for alpha strategies: pair_signal, volatility_breakout, gap_trader."""

from __future__ import annotations

import pytest

from kstock.signal.pair_signal import (
    KNOWN_PAIRS,
    PairSignal,
    compute_pair_ratio,
    evaluate_pair,
    find_pair,
    format_pair_signal,
)
from kstock.signal.volatility_breakout import (
    BreakoutSignal,
    compute_breakout_price,
    evaluate_breakout,
    format_breakout_signal,
)
from kstock.signal.gap_trader import (
    GapSignal,
    detect_gap,
    format_gap_alert,
)


# ===========================================================================
# pair_signal tests
# ===========================================================================


class TestKnownPairs:
    def test_has_entries(self) -> None:
        assert len(KNOWN_PAIRS) > 0

    def test_each_pair_has_required_keys(self) -> None:
        for pair in KNOWN_PAIRS:
            assert "a" in pair
            assert "b" in pair
            assert "a_name" in pair
            assert "b_name" in pair
            assert "relationship" in pair


class TestComputePairRatio:
    def test_returns_three_values(self) -> None:
        prices_a = [float(i + 100) for i in range(25)]
        prices_b = [float(i + 50) for i in range(25)]
        result = compute_pair_ratio(prices_a, prices_b)
        assert len(result) == 3
        current, mean, std = result
        assert isinstance(current, float)
        assert isinstance(mean, float)
        assert isinstance(std, float)

    def test_mismatched_length_raises(self) -> None:
        with pytest.raises(ValueError, match="mismatch"):
            compute_pair_ratio([1.0] * 25, [1.0] * 20)

    def test_insufficient_data_raises(self) -> None:
        with pytest.raises(ValueError, match="Insufficient"):
            compute_pair_ratio([1.0] * 10, [1.0] * 10)

    def test_current_ratio_is_last(self) -> None:
        prices_a = [100.0] * 24 + [200.0]
        prices_b = [100.0] * 25
        current, _, _ = compute_pair_ratio(prices_a, prices_b)
        assert current == 2.0


class TestEvaluatePair:
    def _make_divergent_prices(self, direction: str):
        """Generate prices where A significantly diverges from B."""
        n = 30
        prices_b = [100.0] * n
        if direction == "a_high":
            # A rises sharply at end
            prices_a = [100.0] * (n - 5) + [130.0, 140.0, 150.0, 160.0, 170.0]
        elif direction == "a_low":
            # A drops at end
            prices_a = [100.0] * (n - 5) + [70.0, 60.0, 50.0, 40.0, 30.0]
        else:
            prices_a = [100.0] * n
        return prices_a, prices_b

    def test_high_zscore_a_overvalued(self) -> None:
        """Z-score > 2 -> A 고평가 signal."""
        pair = KNOWN_PAIRS[0]
        prices_a, prices_b = self._make_divergent_prices("a_high")
        result = evaluate_pair(pair, prices_a, prices_b)
        assert result.z_score > 2.0
        assert "고평가" in result.signal

    def test_low_zscore_b_overvalued(self) -> None:
        """Z-score < -2 -> B 고평가 signal."""
        pair = KNOWN_PAIRS[0]
        prices_a, prices_b = self._make_divergent_prices("a_low")
        result = evaluate_pair(pair, prices_a, prices_b)
        assert result.z_score < -2.0
        assert "고평가" in result.signal

    def test_normal_range(self) -> None:
        """Stable prices -> 정상 범위."""
        pair = KNOWN_PAIRS[0]
        prices_a = [100.0] * 30
        prices_b = [100.0] * 30
        result = evaluate_pair(pair, prices_a, prices_b)
        assert result.signal == "정상 범위" or "정상" in result.signal

    def test_insufficient_data(self) -> None:
        """Too few data points -> 데이터 부족."""
        pair = KNOWN_PAIRS[0]
        result = evaluate_pair(pair, [100.0] * 5, [100.0] * 5)
        assert "데이터 부족" in result.signal


class TestFormatPairSignal:
    def test_no_bold(self) -> None:
        sig = PairSignal(
            pair=KNOWN_PAIRS[0],
            ratio=1.2, mean_ratio=1.1, std_ratio=0.05,
            z_score=2.5, signal="A 고평가",
            suggestion="매수 고려",
        )
        msg = format_pair_signal(sig)
        assert "**" not in msg

    def test_contains_pair_names(self) -> None:
        sig = PairSignal(pair=KNOWN_PAIRS[0], signal="정상 범위")
        msg = format_pair_signal(sig)
        assert KNOWN_PAIRS[0]["a_name"] in msg
        assert KNOWN_PAIRS[0]["b_name"] in msg

    def test_normal_range_message(self) -> None:
        sig = PairSignal(
            pair=KNOWN_PAIRS[0],
            ratio=1.0, mean_ratio=1.0, std_ratio=0.05,
            z_score=0.5, signal="정상 범위",
            suggestion="대기",
        )
        msg = format_pair_signal(sig)
        assert "정상 범위" in msg
        assert "주호님" in msg


class TestFindPair:
    def test_find_existing_ticker(self) -> None:
        result = find_pair("005930")
        assert len(result) >= 1

    def test_find_nonexistent_ticker(self) -> None:
        result = find_pair("999999")
        assert len(result) == 0


# ===========================================================================
# volatility_breakout tests
# ===========================================================================


class TestComputeBreakoutPrice:
    def test_basic_formula(self) -> None:
        """breakout = open + (high - low) * k."""
        result = compute_breakout_price(
            open_price=70000, prev_high=72000, prev_low=68000, k=0.5,
        )
        expected = 70000 + (72000 - 68000) * 0.5  # 72000
        assert result == round(expected)

    def test_k_zero_returns_open(self) -> None:
        result = compute_breakout_price(70000, 72000, 68000, k=0.0)
        assert result == 70000

    def test_zero_range_returns_open(self) -> None:
        result = compute_breakout_price(70000, 70000, 70000, k=0.5)
        assert result == 70000

    def test_custom_k(self) -> None:
        result = compute_breakout_price(50000, 52000, 48000, k=0.7)
        expected = 50000 + (52000 - 48000) * 0.7  # 52800
        assert result == round(expected)


class TestEvaluateBreakout:
    def test_signal_when_above_breakout_with_volume(self) -> None:
        result = evaluate_breakout(
            ticker="005930", name="삼성전자",
            open_price=70000, current_price=73000,
            prev_high=72000, prev_low=68000,
            volume_ratio=2.0,
        )
        assert result is not None
        assert isinstance(result, BreakoutSignal)
        assert result.volume_confirmed is True

    def test_none_when_below_breakout(self) -> None:
        result = evaluate_breakout(
            ticker="005930", name="삼성전자",
            open_price=70000, current_price=70500,
            prev_high=72000, prev_low=68000,
            volume_ratio=2.0,
        )
        assert result is None

    def test_none_when_volume_insufficient(self) -> None:
        result = evaluate_breakout(
            ticker="005930", name="삼성전자",
            open_price=70000, current_price=73000,
            prev_high=72000, prev_low=68000,
            volume_ratio=1.0,
        )
        assert result is None

    def test_none_when_small_market_cap(self) -> None:
        """market_cap provided but too small -> None."""
        result = evaluate_breakout(
            ticker="005930", name="삼성전자",
            open_price=70000, current_price=73000,
            prev_high=72000, prev_low=68000,
            volume_ratio=2.0,
            market_cap=500_000_000_000,  # 0.5 trillion
        )
        assert result is None


class TestFormatBreakoutSignal:
    def test_no_bold(self) -> None:
        sig = BreakoutSignal(
            ticker="005930", name="삼성전자",
            breakout_price=72000, current_price=73000,
            prev_high=72000, prev_low=68000,
            prev_range=4000, k_value=0.5,
            stop_price=68600, target_price=75600,
            volume_confirmed=True,
        )
        msg = format_breakout_signal(sig)
        assert "**" not in msg

    def test_contains_name(self) -> None:
        sig = BreakoutSignal(
            ticker="005930", name="삼성전자",
            breakout_price=72000, current_price=73000,
            prev_high=72000, prev_low=68000,
            prev_range=4000, k_value=0.5,
            stop_price=68600, target_price=75600,
            volume_confirmed=True,
        )
        msg = format_breakout_signal(sig)
        assert "삼성전자" in msg
        assert "주호님" in msg


# ===========================================================================
# gap_trader tests
# ===========================================================================


class TestDetectGap:
    def test_gap_up_3_pct_with_volume(self) -> None:
        """Gap up +5% with 2.0x volume -> signal."""
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=70000, open_price=73500,
            current_price=74000, volume_ratio=2.0,
        )
        assert result is not None
        assert result.gap_type == "갭업"
        assert result.action == "추세 추종 매수"

    def test_gap_down_5_pct(self) -> None:
        """Gap down -6% -> signal."""
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=70000, open_price=65800,
            current_price=66000, volume_ratio=1.0,
        )
        assert result is not None
        assert result.gap_type == "갭다운"

    def test_small_gap_returns_none(self) -> None:
        """Gap of only 1% -> None."""
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=70000, open_price=70700,
            current_price=70800, volume_ratio=1.5,
        )
        assert result is None

    def test_gap_fill_signal(self) -> None:
        """Gap up but current < open -> 갭채우기."""
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=70000, open_price=72500,
            current_price=71000, volume_ratio=1.5,
        )
        assert result is not None
        assert result.gap_type == "갭채우기"

    def test_gap_up_without_volume_returns_none(self) -> None:
        """Gap up but insufficient volume -> None."""
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=70000, open_price=73000,
            current_price=74000, volume_ratio=1.0,
        )
        assert result is None

    def test_zero_prev_close_returns_none(self) -> None:
        result = detect_gap(
            ticker="005930", name="삼성전자",
            prev_close=0, open_price=70000,
            current_price=70000, volume_ratio=1.5,
        )
        assert result is None


class TestFormatGapAlert:
    def test_no_bold(self) -> None:
        sig = GapSignal(
            ticker="005930", name="삼성전자",
            gap_type="갭업", gap_pct=5.0,
            prev_close=70000, open_price=73500,
            current_price=74000, volume_ratio=2.0,
            action="추세 추종 매수", score_adj=10,
        )
        msg = format_gap_alert(sig)
        assert "**" not in msg

    def test_contains_juho(self) -> None:
        sig = GapSignal(
            ticker="005930", name="삼성전자",
            gap_type="갭업", gap_pct=5.0,
            prev_close=70000, open_price=73500,
            current_price=74000, volume_ratio=2.0,
            action="추세 추종 매수", score_adj=10,
        )
        msg = format_gap_alert(sig)
        assert "주호님" in msg

    def test_gap_down_message(self) -> None:
        sig = GapSignal(
            ticker="005930", name="삼성전자",
            gap_type="갭다운", gap_pct=-6.0,
            prev_close=70000, open_price=65800,
            current_price=66000, volume_ratio=1.0,
            action="반등 매수 후보", score_adj=-5,
        )
        msg = format_gap_alert(sig)
        assert "갭다운" in msg
        assert "주호님" in msg

    def test_gap_fill_message(self) -> None:
        sig = GapSignal(
            ticker="005930", name="삼성전자",
            gap_type="갭채우기", gap_pct=4.0,
            prev_close=70000, open_price=72800,
            current_price=71000, volume_ratio=1.5,
            action="매도 시그널", score_adj=-8,
        )
        msg = format_gap_alert(sig)
        assert "갭채우기" in msg
        assert "매도" in msg
