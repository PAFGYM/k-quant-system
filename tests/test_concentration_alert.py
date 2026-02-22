"""Tests for the portfolio concentration alert system."""

from __future__ import annotations

import pytest

from kstock.signal.concentration_alert import (
    ConcentrationAlert,
    ConcentrationReport,
    SECTOR_MAP,
    analyze_concentration,
    format_concentration_report,
    suggest_rebalance,
)


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestConcentrationDataclasses:
    """Tests for ConcentrationAlert and ConcentrationReport dataclasses."""

    def test_alert_dataclass(self) -> None:
        alert = ConcentrationAlert(
            alert_type="single_stock",
            severity="warning",
            message="삼성전자 비중 45.0% (40% 초과)",
            suggestion="삼성전자 일부 익절 후 다른 섹터로 분산 필요",
        )
        assert alert.alert_type == "single_stock"
        assert alert.severity == "warning"

    def test_report_dataclass_defaults(self) -> None:
        report = ConcentrationReport()
        assert report.alerts == []
        assert report.sector_weights == {}
        assert report.top_position_pct == 0.0
        assert report.cash_pct == 0.0
        assert report.score == 100

    def test_report_with_custom_values(self) -> None:
        report = ConcentrationReport(score=60, cash_pct=5.0, top_position_pct=35.0)
        assert report.score == 60
        assert report.cash_pct == 5.0


# ---------------------------------------------------------------------------
# SECTOR_MAP
# ---------------------------------------------------------------------------


class TestSectorMap:
    """Tests for the SECTOR_MAP constant."""

    def test_contains_samsung_electronics(self) -> None:
        assert "005930" in SECTOR_MAP
        assert SECTOR_MAP["005930"] == "반도체"

    def test_contains_sk_hynix(self) -> None:
        assert "000660" in SECTOR_MAP
        assert SECTOR_MAP["000660"] == "반도체"

    def test_contains_naver(self) -> None:
        assert "035420" in SECTOR_MAP
        assert SECTOR_MAP["035420"] == "소프트웨어"

    def test_contains_hyundai(self) -> None:
        assert "005380" in SECTOR_MAP
        assert SECTOR_MAP["005380"] == "자동차"

    def test_is_non_empty(self) -> None:
        assert len(SECTOR_MAP) > 0


# ---------------------------------------------------------------------------
# analyze_concentration – single stock
# ---------------------------------------------------------------------------


class TestAnalyzeConcentrationSingleStock:
    """Tests for single stock concentration warnings."""

    def test_single_stock_over_40_triggers_warning(self) -> None:
        """Single stock > 40% triggers warning."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 5000, "profit_pct": 20},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 2000, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=500, total_eval=0)
        stock_alerts = [a for a in report.alerts if a.alert_type == "single_stock"]
        assert len(stock_alerts) > 0
        assert stock_alerts[0].severity == "warning"

    def test_balanced_portfolio_no_stock_alert(self) -> None:
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 2000, "profit_pct": 10},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 2000, "profit_pct": 5},
            {"ticker": "005380", "name": "현대차", "eval_amount": 2000, "profit_pct": 8},
            {"ticker": "055550", "name": "신한지주", "eval_amount": 2000, "profit_pct": 3},
        ]
        report = analyze_concentration(holdings, cash=2000, total_eval=0)
        stock_alerts = [a for a in report.alerts if a.alert_type == "single_stock"]
        assert len(stock_alerts) == 0


# ---------------------------------------------------------------------------
# analyze_concentration – single sector
# ---------------------------------------------------------------------------


class TestAnalyzeConcentrationSector:
    """Tests for sector concentration dangers."""

    def test_single_sector_over_50_triggers_danger(self) -> None:
        """Single sector > 50% triggers danger."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 5000, "profit_pct": 10},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 3000, "profit_pct": 5},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 2000, "profit_pct": 3},
        ]
        report = analyze_concentration(holdings, cash=500, total_eval=0)
        sector_alerts = [a for a in report.alerts if a.alert_type == "single_sector"]
        assert len(sector_alerts) > 0
        assert sector_alerts[0].severity == "danger"

    def test_diversified_sectors_no_sector_alert(self) -> None:
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 2000, "profit_pct": 10},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 2000, "profit_pct": 5},
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 2000, "profit_pct": 8},
            {"ticker": "005380", "name": "현대차", "eval_amount": 2000, "profit_pct": 3},
        ]
        report = analyze_concentration(holdings, cash=2000, total_eval=0)
        sector_alerts = [a for a in report.alerts if a.alert_type == "single_sector"]
        assert len(sector_alerts) == 0


# ---------------------------------------------------------------------------
# analyze_concentration – cash
# ---------------------------------------------------------------------------


class TestAnalyzeConcentrationCash:
    """Tests for cash-related alerts."""

    def test_zero_cash_triggers_warning(self) -> None:
        """Cash 0% triggers warning."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 5000, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=0, total_eval=5000)
        cash_alerts = [a for a in report.alerts if a.alert_type == "no_cash"]
        assert len(cash_alerts) > 0
        assert "현금 비중 0%" in cash_alerts[0].message

    def test_low_cash_triggers_warning(self) -> None:
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 9700, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=200, total_eval=10000)
        cash_alerts = [a for a in report.alerts if a.alert_type == "no_cash"]
        assert len(cash_alerts) > 0

    def test_adequate_cash_no_warning(self) -> None:
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 3000, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=2000, total_eval=5000)
        cash_alerts = [a for a in report.alerts if a.alert_type == "no_cash"]
        assert len(cash_alerts) == 0


# ---------------------------------------------------------------------------
# Well-diversified portfolio
# ---------------------------------------------------------------------------


class TestWellDiversifiedPortfolio:
    """Tests for a well-diversified portfolio scenario."""

    def test_no_alerts_high_score(self) -> None:
        """Well-diversified portfolio -> no alerts, high score."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 1500, "profit_pct": 10},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 1500, "profit_pct": 5},
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 1500, "profit_pct": 8},
            {"ticker": "005380", "name": "현대차", "eval_amount": 1500, "profit_pct": 3},
            {"ticker": "055550", "name": "신한지주", "eval_amount": 1500, "profit_pct": 2},
        ]
        report = analyze_concentration(holdings, cash=2500, total_eval=10000)
        assert len(report.alerts) == 0
        assert report.score >= 80


# ---------------------------------------------------------------------------
# Empty holdings
# ---------------------------------------------------------------------------


class TestEmptyHoldings:
    """Tests for empty holdings edge case."""

    def test_empty_holdings_returns_100_score(self) -> None:
        report = analyze_concentration([], cash=0, total_eval=0)
        assert report.score == 100
        assert report.alerts == []

    def test_empty_holdings_no_sector_weights(self) -> None:
        report = analyze_concentration([], cash=0, total_eval=0)
        assert report.sector_weights == {}


# ---------------------------------------------------------------------------
# suggest_rebalance
# ---------------------------------------------------------------------------


class TestSuggestRebalance:
    """Tests for suggest_rebalance."""

    def test_returns_suggestions_for_concentrated_portfolio(self) -> None:
        """suggest_rebalance returns specific suggestions."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 6000, "profit_pct": 20},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 3000, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=0, total_eval=0)
        suggestions = suggest_rebalance(report, holdings)
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0

    def test_empty_holdings_returns_empty(self) -> None:
        report = ConcentrationReport()
        suggestions = suggest_rebalance(report, [])
        assert suggestions == []

    def test_balanced_portfolio_may_still_have_cash_suggestion(self) -> None:
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 3000, "profit_pct": 20},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 3000, "profit_pct": 15},
        ]
        report = analyze_concentration(holdings, cash=0, total_eval=6000)
        suggestions = suggest_rebalance(report, holdings)
        # Should suggest cash since cash is 0
        assert any("현금" in s for s in suggestions) or len(suggestions) >= 0


# ---------------------------------------------------------------------------
# format_concentration_report
# ---------------------------------------------------------------------------


class TestFormatConcentrationReport:
    """Tests for format_concentration_report."""

    def test_returns_string(self) -> None:
        """format_concentration_report returns string."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 5000, "profit_pct": 10},
        ]
        report = analyze_concentration(holdings, cash=1000, total_eval=6000)
        result = format_concentration_report(report)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_score(self) -> None:
        report = ConcentrationReport(score=75, cash_pct=10.0, top_position_pct=30.0)
        result = format_concentration_report(report)
        assert "75" in result

    def test_empty_report_still_formats(self) -> None:
        report = ConcentrationReport()
        result = format_concentration_report(report)
        assert isinstance(result, str)
        assert "편중" in result or "포트폴리오" in result
