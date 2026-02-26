"""E2E tests for the risk engine integration.

Tests VaR, Monte Carlo, stress testing, risk grading, advanced report.
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from kstock.core.risk_engine import (
    calculate_historical_var,
    calculate_parametric_var,
    run_monte_carlo,
    run_stress_test,
    generate_advanced_risk_report,
    format_advanced_risk_report,
    _calculate_risk_grade,
)

PORTFOLIO_VALUE = 100_000_000


def _make_returns(days: int = 252, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.0005, scale=0.02, size=days)


# ---------------------------------------------------------------------------
# 1. Full advanced risk report (async, mocked _fetch_price_histories)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_advanced_risk_report_full():
    """Mock _fetch_price_histories and verify report structure."""
    holdings = [
        {"ticker": "005930", "name": "Samsung", "quantity": 100, "market": "KOSPI"},
        {"ticker": "000660", "name": "SK Hynix", "quantity": 50, "market": "KOSPI"},
    ]

    dates = pd.bdate_range(end="2026-02-25", periods=120)
    rng = np.random.default_rng(42)
    price_histories = {
        "005930": pd.Series(
            (1 + rng.normal(0.0005, 0.02, 120)).cumprod() * 70000, index=dates,
        ),
        "000660": pd.Series(
            (1 + rng.normal(0.0003, 0.025, 120)).cumprod() * 130000, index=dates,
        ),
    }

    with patch("kstock.core.risk_engine._fetch_price_histories", return_value=price_histories):
        report = await generate_advanced_risk_report(
            portfolio_value=PORTFOLIO_VALUE,
            holdings=holdings,
        )

    assert report is not None
    assert report.portfolio_value == PORTFOLIO_VALUE
    assert report.risk_grade in ("A", "B", "C", "D", "F")
    assert 0 <= report.risk_score <= 100
    assert report.historical_var is not None
    assert report.monte_carlo is not None
    assert len(report.stress_results) == 5


# ---------------------------------------------------------------------------
# 2. VaR with a single stock
# ---------------------------------------------------------------------------

def test_var_with_single_stock():
    """Historical VaR for a single-stock portfolio."""
    returns = _make_returns()
    holdings = [{"weight": 1.0, "returns": returns.tolist()}]

    result = calculate_historical_var(
        portfolio_value=PORTFOLIO_VALUE,
        holdings=holdings,
        confidence=0.95,
    )

    assert result.method == "historical"
    assert result.var_95_pct != 0
    assert result.var_95 != 0
    assert result.cvar_95_pct <= result.var_95_pct  # CVaR is worse (more negative)


def test_parametric_var():
    """Parametric VaR with z-score method."""
    weights = np.array([0.6, 0.4])
    mean_returns = np.array([0.0005, 0.0003])
    cov_matrix = np.array([
        [0.0004, 0.0001],
        [0.0001, 0.0006],
    ])

    result = calculate_parametric_var(
        portfolio_value=PORTFOLIO_VALUE,
        weights=weights,
        mean_returns=mean_returns,
        cov_matrix=cov_matrix,
    )

    assert result.method == "parametric"
    assert result.var_95_pct != 0
    assert result.var_95 != 0


# ---------------------------------------------------------------------------
# 3. Stress test all 5 scenarios
# ---------------------------------------------------------------------------

def test_stress_test_all_scenarios():
    """run_stress_test must return exactly 5 predefined crisis scenarios."""
    holdings = [
        {"ticker": "005930", "name": "Samsung", "weight": 0.5, "sector": "반도체"},
        {"ticker": "000660", "name": "SK Hynix", "weight": 0.3, "sector": "반도체"},
        {"ticker": "035420", "name": "NAVER", "weight": 0.2, "sector": "IT"},
    ]

    results = run_stress_test(
        portfolio_value=PORTFOLIO_VALUE,
        holdings=holdings,
    )

    assert isinstance(results, list)
    assert len(results) == 5

    for r in results:
        assert isinstance(r.portfolio_impact_pct, (int, float))
        assert isinstance(r.portfolio_impact_amount, (int, float))
        assert r.recovery_days_estimate >= 0


# ---------------------------------------------------------------------------
# 4. Risk grade boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "var_pct, mdd, conc, corr, stress, expected_valid",
    [
        (0, 0, 0, 0, 0, True),          # Low risk
        (2, 10, 0.5, 0.5, 20, True),    # Medium risk
        (5, 20, 1.0, 1.0, 40, True),    # High risk
    ],
)
def test_risk_grade_bounds(var_pct, mdd, conc, corr, stress, expected_valid):
    grade, score = _calculate_risk_grade(
        var_95_pct=var_pct,
        max_dd_pct=mdd,
        concentration=conc,
        max_corr=corr,
        worst_stress_pct=stress,
    )

    assert grade in ("A", "B", "C", "D", "F")
    assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# 5. Monte Carlo distribution
# ---------------------------------------------------------------------------

def test_monte_carlo_distribution():
    """Monte Carlo output has sensible statistical properties."""
    weights = np.array([0.5, 0.5])
    mean_returns = np.array([0.0005, 0.0003])
    cov_matrix = np.array([
        [0.0004, 0.0001],
        [0.0001, 0.0006],
    ])

    result = run_monte_carlo(
        portfolio_value=PORTFOLIO_VALUE,
        weights=weights,
        mean_returns=mean_returns,
        cov_matrix=cov_matrix,
        simulations=5000,
        days=20,
    )

    assert result.simulations == 5000
    assert result.worst_case_pct <= result.best_case_pct
    assert result.var_95_pct != 0


# ---------------------------------------------------------------------------
# 6. Formatted report includes key sections
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_format_report_includes_sections():
    """format_advanced_risk_report output string contains key sections."""
    holdings = [
        {"ticker": "005930", "name": "Samsung", "quantity": 100, "market": "KOSPI"},
    ]

    dates = pd.bdate_range(end="2026-02-25", periods=120)
    rng = np.random.default_rng(42)
    price_histories = {
        "005930": pd.Series(
            (1 + rng.normal(0.0005, 0.02, 120)).cumprod() * 70000, index=dates,
        ),
    }

    with patch("kstock.core.risk_engine._fetch_price_histories", return_value=price_histories):
        report = await generate_advanced_risk_report(
            portfolio_value=PORTFOLIO_VALUE,
            holdings=holdings,
        )

    formatted = format_advanced_risk_report(report)
    assert isinstance(formatted, str)
    assert len(formatted) > 0
    # Check for key sections (Korean)
    lower = formatted.lower()
    assert "var" in lower or "리스크" in lower


# ---------------------------------------------------------------------------
# 7. Empty portfolio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_engine_empty_portfolio():
    """Empty portfolio returns default report without error."""
    report = await generate_advanced_risk_report(
        portfolio_value=0,
        holdings=[],
    )

    assert report is not None
    assert report.portfolio_value == 0
    assert report.risk_grade in ("A", "B", "C", "D", "F")
