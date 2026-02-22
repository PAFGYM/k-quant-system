"""Tests for kstock.signal.financial_analyzer (Sections 51-52 - financial analysis)."""

from __future__ import annotations

import pytest

from kstock.signal.financial_analyzer import (
    FinancialData,
    FinancialScore,
    analyze_financials,
    format_financial_report,
    score_growth,
    score_profitability,
    score_stability,
    score_valuation,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_fin_data(**overrides) -> FinancialData:
    """Create FinancialData with reasonable defaults."""
    defaults = dict(
        ticker="005930",
        name="삼성전자",
        revenue=3000,
        operating_income=500,
        net_income=400,
        op_margin=16.0,
        roe=12.0,
        roa=8.0,
        debt_ratio=80.0,
        current_ratio=180.0,
        per=12.0,
        pbr=1.2,
        eps=5000,
        bps=50000,
        dps=1500,
        fcf=200,
        ebitda=700,
    )
    defaults.update(overrides)
    return FinancialData(**defaults)


# ---------------------------------------------------------------------------
# score_growth
# ---------------------------------------------------------------------------


class TestScoreGrowth:
    def test_high_growth(self) -> None:
        """Revenue +25%, op_income +30%, CAGR +20% -> score 25."""
        score, details = score_growth(25.0, 30.0, 20.0)
        assert score == 25
        assert len(details) == 3

    def test_medium_growth(self) -> None:
        """Revenue +12%, op_income +12%, CAGR +12% -> score ~20."""
        score, details = score_growth(12.0, 12.0, 12.0)
        assert score == 20  # 8 + 8 + 4

    def test_low_growth(self) -> None:
        """Revenue +6%, op_income +6%, CAGR +6% -> score ~15."""
        score, details = score_growth(6.0, 6.0, 6.0)
        assert score == 15  # 6 + 6 + 3

    def test_negative_growth(self) -> None:
        """Negative growth across all dimensions -> low score."""
        score, details = score_growth(-10.0, -15.0, -5.0)
        assert score == 5  # 2 + 2 + 1

    def test_zero_growth(self) -> None:
        """Zero growth -> moderate low score."""
        score, details = score_growth(0.0, 0.0, 0.0)
        assert score == 10  # 4 + 4 + 2

    def test_capped_at_25(self) -> None:
        """Even with maximum inputs, score should not exceed 25."""
        score, _ = score_growth(100.0, 100.0, 100.0)
        assert score <= 25

    def test_details_contain_korean_labels(self) -> None:
        _, details = score_growth(10.0, 10.0, 10.0)
        assert any("매출" in d for d in details)
        assert any("영업이익" in d for d in details)
        assert any("CAGR" in d for d in details)


# ---------------------------------------------------------------------------
# score_profitability
# ---------------------------------------------------------------------------


class TestScoreProfitability:
    def test_roe_15_plus(self) -> None:
        """ROE 18%, margin 22%, positive FCF -> score 25."""
        score, details = score_profitability(18.0, 22.0, 500)
        assert score == 25  # 10 + 10 + 5

    def test_roe_10_plus(self) -> None:
        """ROE 12%, margin 12%, positive FCF -> score 21."""
        score, details = score_profitability(12.0, 12.0, 100)
        assert score == 21  # 8 + 8 + 5

    def test_roe_5_plus(self) -> None:
        """ROE 7%, margin 7%, positive FCF -> score 17."""
        score, details = score_profitability(7.0, 7.0, 50)
        assert score == 17  # 6 + 6 + 5

    def test_low_profitability_negative_fcf(self) -> None:
        """ROE -2%, margin -5%, negative FCF -> low score."""
        score, details = score_profitability(-2.0, -5.0, -100)
        assert score == 3  # 1 + 1 + 1

    def test_capped_at_25(self) -> None:
        score, _ = score_profitability(50.0, 50.0, 1000)
        assert score <= 25


# ---------------------------------------------------------------------------
# score_stability
# ---------------------------------------------------------------------------


class TestScoreStability:
    def test_low_debt_high_current(self) -> None:
        """Debt < 100%, current >= 200%, coverage >= 10 -> 25."""
        score, details = score_stability(50.0, 250.0, 15.0)
        assert score == 25  # 10 + 10 + 5

    def test_medium_debt(self) -> None:
        """Debt 150%, current 160%, coverage 6 -> 19."""
        score, details = score_stability(150.0, 160.0, 6.0)
        assert score == 19  # 7 + 8 + 4

    def test_high_debt(self) -> None:
        """Debt >= 200% -> debt_score = 3."""
        score, details = score_stability(250.0, 120.0, 4.0)
        assert score == 12  # 3 + 6 + 3

    def test_current_ratio_warning(self) -> None:
        """Current ratio < 100% produces a warning in details."""
        score, details = score_stability(50.0, 80.0, 10.0)
        assert any("유동성 주의" in d for d in details)

    def test_low_interest_coverage_warning(self) -> None:
        """Interest coverage < 3 produces a warning."""
        score, details = score_stability(50.0, 200.0, 2.0)
        assert any("이자보상배율" in d and "주의" in d for d in details)

    def test_capped_at_25(self) -> None:
        score, _ = score_stability(10.0, 500.0, 50.0)
        assert score <= 25


# ---------------------------------------------------------------------------
# score_valuation
# ---------------------------------------------------------------------------


class TestScoreValuation:
    def test_cheap_per(self) -> None:
        """PER well below sector average -> high score."""
        score, details = score_valuation(per=7.0, sector_avg_per=20.0, pbr=0.5)
        assert score >= 20  # 12 + 8 + cheap bonus

    def test_fair_per(self) -> None:
        """PER slightly below sector -> moderate score."""
        score, details = score_valuation(per=17.0, sector_avg_per=20.0, pbr=1.0)
        assert 7 <= score <= 20

    def test_expensive_per(self) -> None:
        """PER well above sector -> low PER score."""
        score, details = score_valuation(per=30.0, sector_avg_per=20.0, pbr=2.0)
        assert score <= 12

    def test_negative_per(self) -> None:
        """Negative PER (loss-making) -> per_score = 2."""
        score, details = score_valuation(per=-5.0, sector_avg_per=15.0, pbr=1.0)
        assert any("적자" in d for d in details)

    def test_absolute_cheap_bonus(self) -> None:
        """PER < 8 and PBR < 0.7 -> absolute cheap bonus 5."""
        score, details = score_valuation(per=6.0, sector_avg_per=20.0, pbr=0.5, hist_pbr_median=1.5)
        assert any("절대 저평가" in d for d in details)

    def test_capped_at_25(self) -> None:
        score, _ = score_valuation(per=5.0, sector_avg_per=50.0, pbr=0.3, hist_pbr_median=2.0)
        assert score <= 25


# ---------------------------------------------------------------------------
# analyze_financials
# ---------------------------------------------------------------------------


class TestAnalyzeFinancials:
    def test_total_equals_sum_of_subscores(self) -> None:
        data = _make_fin_data()
        result = analyze_financials(data, revenue_yoy=15.0, op_income_yoy=12.0, cagr_3y=10.0)
        assert isinstance(result, FinancialScore)
        assert result.total == result.growth + result.profitability + result.stability + result.valuation

    def test_score_bonus_high_total(self) -> None:
        """Total >= 80 -> bonus = 15."""
        data = _make_fin_data(roe=20.0, op_margin=25.0, debt_ratio=30.0, current_ratio=300.0,
                              per=6.0, pbr=0.5, fcf=500)
        result = analyze_financials(
            data,
            revenue_yoy=25.0, op_income_yoy=25.0, cagr_3y=20.0,
            sector_avg_per=25.0, interest_coverage=15.0,
        )
        if result.total >= 80:
            assert result.score_bonus == 15

    def test_score_bonus_low_total(self) -> None:
        """Total < 30 -> bonus = -10."""
        data = _make_fin_data(roe=-5.0, op_margin=-10.0, debt_ratio=300.0, current_ratio=50.0,
                              per=-3.0, pbr=0.1, fcf=-200)
        result = analyze_financials(
            data,
            revenue_yoy=-20.0, op_income_yoy=-30.0, cagr_3y=-10.0,
            sector_avg_per=15.0, interest_coverage=1.0,
        )
        if result.total < 30:
            assert result.score_bonus == -10

    def test_details_has_four_dimensions(self) -> None:
        data = _make_fin_data()
        result = analyze_financials(data)
        assert "growth" in result.details
        assert "profitability" in result.details
        assert "stability" in result.details
        assert "valuation" in result.details


# ---------------------------------------------------------------------------
# format_financial_report
# ---------------------------------------------------------------------------


class TestFormatFinancialReport:
    def test_contains_growth_label(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "성장성" in msg

    def test_contains_profitability_label(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "수익성" in msg

    def test_contains_stability_label(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "안정성" in msg

    def test_contains_valuation_label(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "밸류에이션" in msg

    def test_no_bold_markers(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "**" not in msg

    def test_contains_ticker_name(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert "삼성전자" in msg
        assert "005930" in msg

    def test_contains_total_score(self) -> None:
        data = _make_fin_data()
        score = analyze_financials(data)
        msg = format_financial_report(data, score)
        assert f"{score.total}/100" in msg
