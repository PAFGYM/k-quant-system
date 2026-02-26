"""Tests for advanced risk engine."""
import numpy as np
from kstock.core.risk_engine import (
    VaRResult,
    MonteCarloResult,
    StressTestResult,
    AdvancedRiskReport,
    calculate_historical_var,
    calculate_parametric_var,
    run_monte_carlo,
    run_stress_test,
    calculate_real_correlation,
    _calculate_risk_grade,
    format_advanced_risk_report,
    HISTORICAL_STRESS_SCENARIOS,
)
import pandas as pd


def test_historical_var_basic():
    returns = np.random.normal(0, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.var_95 < 0
    assert result.var_99 <= result.var_95  # 99% is more extreme loss
    assert result.method == "historical"


def test_var_95_less_than_99():
    returns = np.random.normal(0, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.var_99_pct <= result.var_95_pct


def test_cvar_gte_var():
    returns = np.random.normal(-0.001, 0.02, 252)
    holdings = [{"weight": 1.0, "returns": returns}]
    result = calculate_historical_var(10_000_000, holdings)
    assert result.cvar_95_pct <= result.var_95_pct


def test_parametric_var():
    weights = np.array([0.5, 0.5])
    mean_returns = np.array([0.001, 0.0005])
    cov_matrix = np.array([[0.0004, 0.0001], [0.0001, 0.0003]])
    result = calculate_parametric_var(10_000_000, weights, mean_returns, cov_matrix)
    assert result.var_95_pct < 0
    assert result.method == "parametric"


def test_monte_carlo_distribution():
    weights = np.array([0.6, 0.4])
    mean_returns = np.array([0.0005, 0.0003])
    cov_matrix = np.array([[0.0004, 0.00005], [0.00005, 0.0002]])
    result = run_monte_carlo(10_000_000, weights, mean_returns, cov_matrix, simulations=1000)
    assert result.simulations == 1000
    assert len(result.distribution) == 100
    assert result.best_case_pct > result.worst_case_pct


def test_stress_test_all_scenarios():
    holdings = [
        {"ticker": "005930", "name": "삼성전자", "weight": 0.5, "sector": "반도체"},
        {"ticker": "005380", "name": "현대차", "weight": 0.5, "sector": "자동차"},
    ]
    results = run_stress_test(10_000_000, holdings)
    assert len(results) == len(HISTORICAL_STRESS_SCENARIOS)
    for r in results:
        assert r.portfolio_impact_pct < 0


def test_stress_test_single():
    holdings = [{"ticker": "005930", "name": "삼성전자", "weight": 1.0, "sector": "반도체"}]
    results = run_stress_test(10_000_000, holdings, "covid_crash")
    assert len(results) == 1
    assert "코로나" in results[0].scenario_name


def test_risk_grade_a():
    grade, score = _calculate_risk_grade(var_95_pct=-0.5, max_dd_pct=-2, concentration=0.2, max_corr=0.3, worst_stress_pct=-10)
    assert grade in ("A", "B")
    assert score <= 40


def test_risk_grade_f():
    grade, score = _calculate_risk_grade(var_95_pct=-5, max_dd_pct=-30, concentration=0.9, max_corr=0.95, worst_stress_pct=-50)
    assert grade in ("D", "F")
    assert score >= 60


def test_real_correlation():
    dates = pd.date_range("2024-01-01", periods=100)
    prices = {
        "A": pd.Series(np.cumsum(np.random.randn(100)) + 100, index=dates),
        "B": pd.Series(np.cumsum(np.random.randn(100)) + 100, index=dates),
    }
    corr = calculate_real_correlation(prices)
    assert not corr.empty
    assert corr.shape == (2, 2)
    assert corr.iloc[0, 0] == 1.0


def test_format_report():
    report = AdvancedRiskReport(
        date="2025-02-25",
        portfolio_value=10_000_000,
        risk_grade="B",
        risk_score=35,
    )
    text = format_advanced_risk_report(report)
    assert "리스크" in text
    assert "B" in text


def test_empty_holdings_var():
    result = calculate_historical_var(10_000_000, [])
    assert result.var_95 == 0
    assert "데이터 부족" in result.confidence_text
