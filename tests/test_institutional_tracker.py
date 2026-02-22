"""Tests for the institutional/program trading pattern detector."""

from __future__ import annotations

import pytest

from kstock.signal.institutional_tracker import (
    INSTITUTIONAL_PATTERNS,
    InstitutionalPattern,
    InstitutionalSignal,
    analyze_institutional,
    detect_foreign_turning,
    detect_program_trading,
    detect_tax_selling_risk,
    detect_window_dressing,
    format_institutional_summary,
)


# ---------------------------------------------------------------------------
# INSTITUTIONAL_PATTERNS
# ---------------------------------------------------------------------------


class TestInstitutionalPatterns:
    """Tests for the INSTITUTIONAL_PATTERNS constant."""

    def test_patterns_is_non_empty_dict(self) -> None:
        assert isinstance(INSTITUTIONAL_PATTERNS, dict)
        assert len(INSTITUTIONAL_PATTERNS) > 0

    def test_patterns_contains_expected_keys(self) -> None:
        expected = {"pension_fund", "asset_manager", "securities", "foreign", "insurance"}
        assert expected == set(INSTITUTIONAL_PATTERNS.keys())

    def test_each_pattern_list_is_non_empty(self) -> None:
        for key, patterns in INSTITUTIONAL_PATTERNS.items():
            assert isinstance(patterns, list), f"{key} should be a list"
            assert len(patterns) > 0, f"{key} should not be empty"

    def test_pattern_entries_are_dataclass_instances(self) -> None:
        for key, patterns in INSTITUTIONAL_PATTERNS.items():
            for p in patterns:
                assert isinstance(p, InstitutionalPattern), (
                    f"Entry in {key} is not InstitutionalPattern"
                )


# ---------------------------------------------------------------------------
# detect_program_trading
# ---------------------------------------------------------------------------


class TestDetectProgramTrading:
    """Tests for detect_program_trading."""

    def test_large_positive_buy_returns_bullish_signal(self) -> None:
        result = detect_program_trading(500)
        assert result["signal"] == "단기 상승 시그널"
        assert result["amount"] == 500

    def test_very_large_positive_buy(self) -> None:
        result = detect_program_trading(1000)
        assert result["signal"] == "단기 상승 시그널"
        assert "프로그램 매수세 유입" in result["description"]

    def test_large_negative_buy_returns_bearish_signal(self) -> None:
        result = detect_program_trading(-500)
        assert result["signal"] == "단기 하락 시그널"
        assert result["amount"] == -500

    def test_very_large_negative_buy(self) -> None:
        result = detect_program_trading(-1000)
        assert result["signal"] == "단기 하락 시그널"
        assert "프로그램 매도세 출회" in result["description"]

    def test_zero_returns_neutral(self) -> None:
        result = detect_program_trading(0)
        assert result["signal"] == "중립"
        assert result["amount"] == 0

    def test_small_positive_returns_neutral(self) -> None:
        result = detect_program_trading(100)
        assert result["signal"] == "중립"

    def test_small_negative_returns_neutral(self) -> None:
        result = detect_program_trading(-100)
        assert result["signal"] == "중립"

    def test_boundary_499_is_neutral(self) -> None:
        result = detect_program_trading(499)
        assert result["signal"] == "중립"

    def test_boundary_neg499_is_neutral(self) -> None:
        result = detect_program_trading(-499)
        assert result["signal"] == "중립"


# ---------------------------------------------------------------------------
# detect_foreign_turning
# ---------------------------------------------------------------------------


class TestDetectForeignTurning:
    """Tests for detect_foreign_turning."""

    def test_five_days_buy_returns_trend_change(self) -> None:
        result = detect_foreign_turning(5, 0.0, 0.0)
        assert result["signal"] == "추세 전환"
        assert result["risk_level"] == "긍정"

    def test_seven_days_buy_also_triggers(self) -> None:
        result = detect_foreign_turning(7, 0.0, 0.0)
        assert result["signal"] == "추세 전환"

    def test_three_days_sell_plus_fx_spike_returns_danger(self) -> None:
        result = detect_foreign_turning(-3, 1.5, 0.0)
        assert result["signal"] == "위험"
        assert result["risk_level"] == "부정"

    def test_three_days_sell_no_fx_spike_returns_neutral(self) -> None:
        result = detect_foreign_turning(-3, 0.5, 0.0)
        assert result["signal"] == "중립"

    def test_futures_buy_plus_spot_sell_returns_direction_change(self) -> None:
        result = detect_foreign_turning(-1, 0.0, 500.0)
        assert result["signal"] == "방향 전환 임박"
        assert result["risk_level"] == "주의"

    def test_zero_days_returns_neutral(self) -> None:
        result = detect_foreign_turning(0, 0.0, 0.0)
        assert result["signal"] == "중립"

    def test_two_days_buy_returns_neutral(self) -> None:
        result = detect_foreign_turning(2, 0.0, 0.0)
        assert result["signal"] == "중립"
        assert "순매수 중" in result["description"]


# ---------------------------------------------------------------------------
# detect_window_dressing
# ---------------------------------------------------------------------------


class TestDetectWindowDressing:
    """Tests for detect_window_dressing."""

    def test_december_late_is_window_dressing(self) -> None:
        result = detect_window_dressing(12, 20)
        assert result["is_window_dressing"] is True
        assert len(result["institutions"]) > 0

    def test_december_early_foreign_only(self) -> None:
        result = detect_window_dressing(12, 10)
        assert result["is_window_dressing"] is True
        inst_names = [i["institution"] for i in result["institutions"]]
        assert "외국인 (글로벌 펀드)" in inst_names

    def test_june_late_is_window_dressing(self) -> None:
        result = detect_window_dressing(6, 25)
        assert result["is_window_dressing"] is True
        inst_names = [i["institution"] for i in result["institutions"]]
        assert "자산운용사" in inst_names

    def test_june_early_no_window_dressing(self) -> None:
        result = detect_window_dressing(6, 10)
        assert result["is_window_dressing"] is False
        assert len(result["institutions"]) == 0

    def test_march_late_has_securities_and_insurance(self) -> None:
        result = detect_window_dressing(3, 25)
        assert result["is_window_dressing"] is True
        inst_names = [i["institution"] for i in result["institutions"]]
        assert "증권사" in inst_names
        assert "보험사" in inst_names

    def test_july_no_window_dressing(self) -> None:
        result = detect_window_dressing(7, 15)
        assert result["is_window_dressing"] is False


# ---------------------------------------------------------------------------
# detect_tax_selling_risk
# ---------------------------------------------------------------------------


class TestDetectTaxSellingRisk:
    """Tests for detect_tax_selling_risk."""

    def test_kosdaq_small_cap_high_return_dec_high_risk(self) -> None:
        result = detect_tax_selling_risk("KOSDAQ", 3000, 120.0, 12)
        assert result["risk_level"] == "높음"
        assert len(result["factors"]) == 3

    def test_kospi_large_cap_dec_low_risk(self) -> None:
        result = detect_tax_selling_risk("KOSPI", 50000, 120.0, 12)
        # KOSPI is not KOSDAQ small cap, so only high-return + december
        assert result["risk_level"] == "중간"

    def test_kosdaq_small_cap_low_return_dec(self) -> None:
        result = detect_tax_selling_risk("KOSDAQ", 3000, 10.0, 12)
        assert result["risk_level"] in ("낮음", "중간")

    def test_non_dec_no_risk(self) -> None:
        result = detect_tax_selling_risk("KOSPI", 50000, 10.0, 6)
        assert result["risk_level"] == "해당없음"
        assert len(result["factors"]) == 0

    def test_november_adds_half_score(self) -> None:
        result = detect_tax_selling_risk("KOSDAQ", 3000, 120.0, 11)
        assert result["risk_level"] in ("중간", "높음")
        assert any("11월" in f for f in result["factors"])

    def test_kosdaq_variant_spelling(self) -> None:
        result = detect_tax_selling_risk("코스닥", 3000, 120.0, 12)
        assert result["risk_level"] == "높음"


# ---------------------------------------------------------------------------
# analyze_institutional
# ---------------------------------------------------------------------------


class TestAnalyzeInstitutional:
    """Tests for analyze_institutional."""

    def test_returns_institutional_signal(self) -> None:
        result = analyze_institutional()
        assert isinstance(result, InstitutionalSignal)

    def test_default_signals_are_neutral(self) -> None:
        result = analyze_institutional()
        assert result.pension_signal == "중립"
        assert result.asset_mgr_signal == "중립"
        assert result.securities_signal == "중립"
        assert result.insurance_signal == "중립"

    def test_pension_buy_signal(self) -> None:
        result = analyze_institutional(pension_net_buy=200)
        assert "순매수" in result.pension_signal

    def test_pension_sell_signal(self) -> None:
        result = analyze_institutional(pension_net_buy=-200)
        assert "순매도" in result.pension_signal

    def test_program_net_buy_passed_through(self) -> None:
        result = analyze_institutional(program_net_buy_krw=800)
        assert result.program_net_buy == 800

    def test_summary_contains_program_info_on_large_buy(self) -> None:
        result = analyze_institutional(program_net_buy_krw=800)
        assert "프로그램" in result.summary or "비차익" in result.summary

    def test_summary_no_special_items_default(self) -> None:
        result = analyze_institutional()
        assert result.summary == "기관 수급 특이사항 없음"


# ---------------------------------------------------------------------------
# format_institutional_summary
# ---------------------------------------------------------------------------


class TestFormatInstitutionalSummary:
    """Tests for format_institutional_summary."""

    def test_returns_non_empty_string(self) -> None:
        signal = analyze_institutional()
        result = format_institutional_summary(signal)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_entity_labels(self) -> None:
        signal = analyze_institutional()
        result = format_institutional_summary(signal)
        assert "외국인" in result
        assert "연기금" in result
        assert "자산운용" in result
        assert "증권사" in result
        assert "보험" in result

    def test_contains_program_info(self) -> None:
        signal = analyze_institutional(program_net_buy_krw=300)
        result = format_institutional_summary(signal)
        assert "프로그램" in result
