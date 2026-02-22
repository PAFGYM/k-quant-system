"""Tests for the risk manager module (core/risk_manager.py)."""

from __future__ import annotations

import pytest

from kstock.core.risk_manager import (
    RISK_LIMITS,
    RiskViolation,
    RiskReport,
    calculate_mdd,
    calculate_stock_weights,
    calculate_sector_weights,
    check_risk_limits,
    generate_rebalance_suggestions,
    format_risk_report,
    format_risk_alert,
)


# =========================================================================
# TestRiskLimits
# =========================================================================

class TestRiskLimits:
    """RISK_LIMITS 기본값 확인."""

    def test_max_portfolio_mdd(self):
        assert RISK_LIMITS["max_portfolio_mdd"] == -0.15

    def test_emergency_mdd(self):
        assert RISK_LIMITS["emergency_mdd"] == -0.20

    def test_max_daily_loss(self):
        assert RISK_LIMITS["max_daily_loss"] == -0.05

    def test_max_single_stock_weight(self):
        assert RISK_LIMITS["max_single_stock_weight"] == 0.40

    def test_max_sector_weight(self):
        assert RISK_LIMITS["max_sector_weight"] == 0.60

    def test_max_correlation(self):
        assert RISK_LIMITS["max_correlation"] == 0.85

    def test_max_margin_ratio(self):
        assert RISK_LIMITS["max_margin_ratio"] == 0.20

    def test_max_single_margin(self):
        assert RISK_LIMITS["max_single_margin"] == 0.30


# =========================================================================
# TestCalculateMDD
# =========================================================================

class TestCalculateMDD:
    """calculate_mdd 함수 테스트."""

    def test_peak_100_current_85(self):
        """고점 100, 현재 85 → MDD -15%."""
        mdd = calculate_mdd(85, 100)
        assert mdd == pytest.approx(-0.15, abs=0.001)

    def test_current_above_peak(self):
        """현재 > 고점 → MDD 0."""
        mdd = calculate_mdd(110, 100)
        assert mdd == 0.0

    def test_current_equals_peak(self):
        """현재 == 고점 → MDD 0."""
        mdd = calculate_mdd(100, 100)
        assert mdd == 0.0

    def test_zero_peak(self):
        """고점 0 → MDD 0."""
        mdd = calculate_mdd(50, 0)
        assert mdd == 0.0

    def test_negative_peak(self):
        """고점 음수 → MDD 0."""
        mdd = calculate_mdd(50, -10)
        assert mdd == 0.0

    def test_large_drawdown(self):
        """50% 하락."""
        mdd = calculate_mdd(50, 100)
        assert mdd == pytest.approx(-0.5, abs=0.001)


# =========================================================================
# TestCalculateWeights
# =========================================================================

class TestCalculateStockWeights:
    """calculate_stock_weights 함수 테스트."""

    def test_equal_holdings(self):
        """동일 금액 → 동일 비중."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 100},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 100},
        ]
        weights = calculate_stock_weights(holdings)
        assert weights["005930"] == pytest.approx(0.5, abs=0.01)
        assert weights["000660"] == pytest.approx(0.5, abs=0.01)

    def test_single_holding(self):
        """단일 종목 → 100%."""
        holdings = [{"ticker": "005930", "name": "삼성전자", "eval_amount": 1000}]
        weights = calculate_stock_weights(holdings)
        assert weights["005930"] == pytest.approx(1.0, abs=0.001)

    def test_empty_holdings(self):
        """빈 리스트 → 빈 dict."""
        assert calculate_stock_weights([]) == {}

    def test_zero_amounts(self):
        """모든 금액 0 → 빈 dict."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 0},
        ]
        assert calculate_stock_weights(holdings) == {}

    def test_unequal_holdings(self):
        """비균등 비중 계산."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 300},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 100},
        ]
        weights = calculate_stock_weights(holdings)
        assert weights["005930"] == pytest.approx(0.75, abs=0.01)
        assert weights["000660"] == pytest.approx(0.25, abs=0.01)


# =========================================================================
# TestCalculateSectorWeights
# =========================================================================

class TestCalculateSectorWeights:
    """calculate_sector_weights 함수 테스트."""

    def test_same_sector_tickers(self):
        """같은 섹터(반도체) 2종목 → 반도체 100%."""
        holdings = [
            {"ticker": "005930", "eval_amount": 500},
            {"ticker": "000660", "eval_amount": 500},
        ]
        weights = calculate_sector_weights(holdings)
        assert "반도체" in weights
        assert weights["반도체"] == pytest.approx(1.0, abs=0.001)

    def test_different_sector_tickers(self):
        """다른 섹터 종목들 → 섹터별 비중 분산."""
        holdings = [
            {"ticker": "005930", "eval_amount": 500},  # 반도체
            {"ticker": "035420", "eval_amount": 500},  # 소프트웨어
        ]
        weights = calculate_sector_weights(holdings)
        assert weights.get("반도체", 0) == pytest.approx(0.5, abs=0.01)
        assert weights.get("소프트웨어", 0) == pytest.approx(0.5, abs=0.01)

    def test_unknown_ticker_goes_to_etc(self):
        """미등록 종목 → '기타' 섹터."""
        holdings = [
            {"ticker": "999999", "eval_amount": 100},
        ]
        weights = calculate_sector_weights(holdings)
        assert "기타" in weights

    def test_empty_holdings(self):
        assert calculate_sector_weights([]) == {}


# =========================================================================
# TestCheckRiskLimits
# =========================================================================

class TestCheckRiskLimits:
    """check_risk_limits 함수 테스트."""

    def test_healthy_portfolio_no_violations(self):
        """건전한 포트폴리오 → 위반 없음."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 30_000_000},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 30_000_000},
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 40_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=100_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=0.01,
        )
        assert len(report.violations) == 0
        assert report.is_buy_blocked is False

    def test_mdd_breach(self):
        """MDD -16% → MDD_BREACH 위반, 매수 차단."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 84_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=84_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=-0.02,
        )
        violation_types = [v.violation_type for v in report.violations]
        assert "MDD_BREACH" in violation_types
        assert report.is_buy_blocked is True

    def test_emergency_mdd(self):
        """MDD -22% → EMERGENCY_MDD 위반."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 78_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=78_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=-0.01,
        )
        violation_types = [v.violation_type for v in report.violations]
        assert "EMERGENCY_MDD" in violation_types
        assert report.is_buy_blocked is True

    def test_daily_loss_breach(self):
        """일일 손실 -6% → DAILY_LOSS 위반."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 100_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=100_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=-0.06,
        )
        violation_types = [v.violation_type for v in report.violations]
        assert "DAILY_LOSS" in violation_types
        assert report.is_buy_blocked is True

    def test_stock_concentration_breach(self):
        """단일 종목 비중 45% → CONCENTRATION 위반."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 45_000_000},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 55_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=100_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=0.0,
        )
        violation_types = [v.violation_type for v in report.violations]
        assert "CONCENTRATION" in violation_types

    def test_sector_concentration_breach(self):
        """섹터 비중 > 60% → SECTOR 위반."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 35_000_000},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 30_000_000},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 35_000_000},
        ]
        report = check_risk_limits(
            holdings=holdings,
            total_value=100_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=0.0,
        )
        violation_types = [v.violation_type for v in report.violations]
        assert "SECTOR" in violation_types

    def test_buy_blocked_when_mdd_breached(self):
        """MDD 위반 시 is_buy_blocked=True."""
        report = check_risk_limits(
            holdings=[],
            total_value=80_000_000,
            peak_value=100_000_000,
            daily_pnl_pct=0.0,
        )
        assert report.is_buy_blocked is True


# =========================================================================
# TestRebalanceSuggestions
# =========================================================================

class TestRebalanceSuggestions:
    """generate_rebalance_suggestions 함수 테스트."""

    def test_generates_suggestion_for_concentration(self):
        """CONCENTRATION 위반 시 reduce 제안 생성."""
        report = RiskReport(
            violations=[
                RiskViolation(
                    violation_type="CONCENTRATION",
                    severity="high",
                    description="삼성전자 비중 45%",
                    recommended_action="비중 축소",
                    details={"ticker": "005930", "name": "삼성전자", "weight": 0.45, "threshold": 0.40},
                )
            ],
            stock_weights={"005930": 0.45},
        )
        suggestions = generate_rebalance_suggestions(report)
        assert len(suggestions) >= 1
        assert suggestions[0]["action"] == "reduce"
        assert suggestions[0]["ticker"] == "005930"

    def test_generates_suggestion_for_emergency_mdd(self):
        """EMERGENCY_MDD → reduce 제안, priority 1."""
        report = RiskReport(
            current_mdd=-0.22,
            violations=[
                RiskViolation(
                    violation_type="EMERGENCY_MDD",
                    severity="critical",
                    description="MDD -22%",
                    recommended_action="전 종목 점검",
                )
            ],
        )
        suggestions = generate_rebalance_suggestions(report)
        assert len(suggestions) >= 1
        assert suggestions[0]["priority"] == 1

    def test_no_violations_no_suggestions(self):
        """위반 없음 → 빈 리스트."""
        report = RiskReport(violations=[])
        suggestions = generate_rebalance_suggestions(report)
        assert suggestions == []

    def test_suggestions_sorted_by_priority(self):
        """제안이 우선순위로 정렬되는지 확인."""
        report = RiskReport(
            current_mdd=-0.16,
            daily_pnl_pct=-0.06,
            violations=[
                RiskViolation(
                    violation_type="CONCENTRATION",
                    severity="high",
                    description="비중 초과",
                    recommended_action="축소",
                    details={"ticker": "005930", "name": "삼성전자", "weight": 0.45, "threshold": 0.40},
                ),
                RiskViolation(
                    violation_type="MDD_BREACH",
                    severity="high",
                    description="MDD 초과",
                    recommended_action="매수 중단",
                ),
            ],
            stock_weights={"005930": 0.45},
        )
        suggestions = generate_rebalance_suggestions(report)
        if len(suggestions) >= 2:
            assert suggestions[0]["priority"] <= suggestions[1]["priority"]


# =========================================================================
# TestFormatRiskReport
# =========================================================================

class TestFormatRiskReport:
    """format_risk_report 함수 테스트."""

    @pytest.fixture
    def report_with_violations(self):
        return RiskReport(
            date="2025-06-15",
            total_value=85_000_000,
            peak_value=100_000_000,
            current_mdd=-0.15,
            daily_pnl_pct=-0.03,
            violations=[
                RiskViolation(
                    violation_type="MDD_BREACH",
                    severity="high",
                    description="MDD -15% 도달",
                    recommended_action="신규 매수 중단",
                )
            ],
            is_buy_blocked=True,
            stock_weights={"005930": 0.6},
            sector_weights={"반도체": 0.6},
        )

    def test_no_bold(self, report_with_violations):
        text = format_risk_report(report_with_violations)
        assert "**" not in text

    def test_contains_username(self, report_with_violations):
        text = format_risk_report(report_with_violations)
        assert "주호님" in text

    def test_contains_violation_info(self, report_with_violations):
        text = format_risk_report(report_with_violations)
        assert "위반" in text

    def test_healthy_report(self):
        report = RiskReport(
            date="2025-06-15",
            total_value=100_000_000,
            peak_value=100_000_000,
            violations=[],
        )
        text = format_risk_report(report)
        assert "주호님" in text
        assert "**" not in text


# =========================================================================
# TestFormatRiskAlert
# =========================================================================

class TestFormatRiskAlert:
    """format_risk_alert 함수 테스트."""

    def test_no_bold(self):
        violations = [
            RiskViolation(
                violation_type="EMERGENCY_MDD",
                severity="critical",
                description="MDD -22% 비상",
                recommended_action="전 종목 점검",
            )
        ]
        text = format_risk_alert(violations)
        assert "**" not in text

    def test_urgent_format(self):
        violations = [
            RiskViolation(
                violation_type="MDD_BREACH",
                severity="high",
                description="MDD -16% 도달",
                recommended_action="신규 매수 중단",
            )
        ]
        text = format_risk_alert(violations)
        assert "긴급" in text
        assert "주호님" in text

    def test_empty_violations_returns_empty_string(self):
        text = format_risk_alert([])
        assert text == ""

    def test_only_medium_severity_returns_empty(self):
        """medium 심각도만 → 빈 문자열 (critical/high 만 포함)."""
        violations = [
            RiskViolation(
                violation_type="SECTOR",
                severity="medium",
                description="섹터 비중 초과",
                recommended_action="분산 권장",
            )
        ]
        text = format_risk_alert(violations)
        assert text == ""
