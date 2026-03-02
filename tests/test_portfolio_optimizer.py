"""Tests for portfolio_optimizer module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.signal.portfolio_optimizer import (
    BlackLittermanInput,
    EfficientFrontier,
    OptimizedPortfolio,
    RiskContribution,
    _build_covariance_matrix,
    compute_efficient_frontier,
    compute_risk_contributions,
    format_portfolio_optimization,
    optimize_black_litterman,
    optimize_markowitz,
    optimize_min_variance,
    optimize_risk_parity,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(
    n_days: int = 252,
    drift: float = 0.0005,
    vol: float = 0.02,
    seed: int | None = None,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    if seed is not None:
        rng = np.random.RandomState(seed)
    else:
        rng = np.random.RandomState()
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    close = [100.0]
    for _ in range(n_days - 1):
        close.append(close[-1] * (1 + drift + vol * rng.randn()))
    close = np.array(close)
    return pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 1_000_000,
        },
        index=dates,
    )


def _make_multi_ohlcv(
    n_assets: int = 5,
    n_days: int = 252,
    drifts: list[float] | None = None,
    vols: list[float] | None = None,
    base_seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Generate synthetic OHLCV for multiple assets."""
    if drifts is None:
        drifts = [0.0003 + 0.0002 * i for i in range(n_assets)]
    if vols is None:
        vols = [0.015 + 0.005 * i for i in range(n_assets)]
    tickers = [f"{i+1:06d}" for i in range(n_assets)]
    return {
        t: _make_ohlcv(n_days=n_days, drift=d, vol=v, seed=base_seed + i)
        for i, (t, d, v) in enumerate(zip(tickers, drifts, vols))
    }


# ---------------------------------------------------------------------------
# Test: _build_covariance_matrix
# ---------------------------------------------------------------------------

class TestBuildCovarianceMatrix:
    def test_shape_and_symmetry(self):
        ohlcv = _make_multi_ohlcv(n_assets=4)
        cov, tickers, mu = _build_covariance_matrix(ohlcv)

        assert cov.shape == (4, 4)
        assert len(tickers) == 4
        assert len(mu) == 4
        # Symmetry
        np.testing.assert_allclose(cov, cov.T, atol=1e-10)

    def test_positive_semidefinite(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        cov, _, _ = _build_covariance_matrix(ohlcv)
        eigenvalues = np.linalg.eigvalsh(cov)
        assert np.all(eigenvalues >= -1e-10)

    def test_empty_input(self):
        cov, tickers, mu = _build_covariance_matrix({})
        assert cov.shape == (0, 0)
        assert tickers == []
        assert len(mu) == 0


# ---------------------------------------------------------------------------
# Test: Markowitz optimization
# ---------------------------------------------------------------------------

class TestMarkowitz:
    def test_weights_sum_to_one(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        result = optimize_markowitz(ohlcv)

        assert isinstance(result, OptimizedPortfolio)
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-4

    def test_max_weight_constraint(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        max_w = 0.25
        result = optimize_markowitz(ohlcv, max_weight=max_w)

        for w in result.weights.values():
            assert w <= max_w + 1e-4

    def test_target_return(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        target = 0.10
        result = optimize_markowitz(ohlcv, target_return=target, max_weight=0.5)

        assert isinstance(result, OptimizedPortfolio)
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-4

    def test_nonnegative_weights(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        result = optimize_markowitz(ohlcv)

        for w in result.weights.values():
            assert w >= -1e-6

    def test_method_name(self):
        ohlcv = _make_multi_ohlcv(n_assets=3)
        result = optimize_markowitz(ohlcv)
        assert result.method == "markowitz"


# ---------------------------------------------------------------------------
# Test: Min variance
# ---------------------------------------------------------------------------

class TestMinVariance:
    def test_basic(self):
        ohlcv = _make_multi_ohlcv(n_assets=5)
        result = optimize_min_variance(ohlcv, max_weight=0.4)

        assert result.method == "min_variance"
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-4

    def test_lower_vol_than_equal_weight(self):
        """Min variance should have lower or equal vol than equal weight."""
        ohlcv = _make_multi_ohlcv(n_assets=5, base_seed=99)
        result = optimize_min_variance(ohlcv, max_weight=0.5)

        # Compute equal-weight volatility
        cov, tickers, mu = _build_covariance_matrix(ohlcv)
        N = len(tickers)
        w_eq = np.full(N, 1.0 / N)
        eq_vol = float(np.sqrt(w_eq @ cov @ w_eq))

        assert result.expected_volatility <= eq_vol + 1e-4


# ---------------------------------------------------------------------------
# Test: Risk parity
# ---------------------------------------------------------------------------

class TestRiskParity:
    def test_equal_vol_assets(self):
        """If all assets have similar volatility, risk parity -> near equal weight."""
        ohlcv = _make_multi_ohlcv(
            n_assets=4,
            vols=[0.02, 0.02, 0.02, 0.02],
            drifts=[0.0005, 0.0005, 0.0005, 0.0005],
            base_seed=123,
        )
        result = optimize_risk_parity(ohlcv)

        assert result.method == "risk_parity"
        weights = list(result.weights.values())
        # All weights should be close to 0.25
        for w in weights:
            assert abs(w - 0.25) < 0.10  # within 10% of equal

    def test_different_vol_assets(self):
        """Higher vol assets should get lower weight."""
        ohlcv = _make_multi_ohlcv(
            n_assets=3,
            vols=[0.01, 0.02, 0.04],
            drifts=[0.0005, 0.0005, 0.0005],
            base_seed=456,
        )
        result = optimize_risk_parity(ohlcv)

        tickers = list(result.weights.keys())
        weights = [result.weights[t] for t in tickers]
        # Lowest vol (first) should have highest weight
        assert weights[0] > weights[2]

    def test_weights_sum_to_one(self):
        ohlcv = _make_multi_ohlcv(n_assets=4)
        result = optimize_risk_parity(ohlcv)
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# Test: Black-Litterman
# ---------------------------------------------------------------------------

class TestBlackLitterman:
    def test_single_view_increases_weight(self):
        """A bullish view on one asset should increase its weight."""
        ohlcv = _make_multi_ohlcv(
            n_assets=4,
            vols=[0.02, 0.02, 0.02, 0.02],
            drifts=[0.0005, 0.0005, 0.0005, 0.0005],
            base_seed=789,
        )
        tickers = list(ohlcv.keys())
        market_weights = {t: 0.25 for t in tickers}

        # Bullish view on first asset
        views = [
            BlackLittermanInput(
                view_ticker=tickers[0],
                view_return=0.30,  # 30% expected return
                confidence=0.8,
            ),
        ]

        result = optimize_black_litterman(
            ohlcv, views=views, market_weights=market_weights,
            max_weight=0.6,
        )

        assert result.method == "black_litterman"
        # The viewed asset should have higher weight than market weight
        assert result.weights[tickers[0]] > 0.25

    def test_no_views_returns_market(self):
        """With no views, BL should approximate market weights."""
        ohlcv = _make_multi_ohlcv(n_assets=3, base_seed=101)
        tickers = list(ohlcv.keys())
        market_weights = {tickers[0]: 0.5, tickers[1]: 0.3, tickers[2]: 0.2}

        result = optimize_black_litterman(
            ohlcv, views=[], market_weights=market_weights,
        )

        assert result.method == "black_litterman"
        # Should be close to market weights
        for t in tickers:
            assert abs(result.weights[t] - market_weights[t]) < 0.15

    def test_weights_sum_to_one(self):
        ohlcv = _make_multi_ohlcv(n_assets=4, base_seed=202)
        tickers = list(ohlcv.keys())
        views = [
            BlackLittermanInput(view_ticker=tickers[0], view_return=0.15, confidence=0.5),
        ]
        market_weights = {t: 0.25 for t in tickers}

        result = optimize_black_litterman(ohlcv, views, market_weights)
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# Test: Efficient frontier
# ---------------------------------------------------------------------------

class TestEfficientFrontier:
    def test_monotonic_returns(self):
        """Frontier points should have increasing returns."""
        ohlcv = _make_multi_ohlcv(n_assets=5, base_seed=303)
        frontier = compute_efficient_frontier(ohlcv, n_points=15, max_weight=0.5)

        assert isinstance(frontier, EfficientFrontier)
        assert len(frontier.points) > 0

        returns = [p.target_return for p in frontier.points]
        # Returns should be non-decreasing (allowing small tolerance)
        for i in range(1, len(returns)):
            assert returns[i] >= returns[i - 1] - 1e-4

    def test_has_special_points(self):
        ohlcv = _make_multi_ohlcv(n_assets=4, base_seed=404)
        frontier = compute_efficient_frontier(ohlcv, n_points=10, max_weight=0.5)

        assert frontier.optimal_point is not None
        assert frontier.min_variance_point is not None
        assert frontier.max_return_point is not None

    def test_empty_input(self):
        frontier = compute_efficient_frontier({}, n_points=10)
        assert len(frontier.points) == 0


# ---------------------------------------------------------------------------
# Test: Risk contributions
# ---------------------------------------------------------------------------

class TestRiskContributions:
    def test_sum_to_100(self):
        """Risk contributions should sum to ~100%."""
        ohlcv = _make_multi_ohlcv(n_assets=4, base_seed=505)
        cov, tickers, _ = _build_covariance_matrix(ohlcv)

        weights = {t: 0.25 for t in tickers}
        contributions = compute_risk_contributions(weights, cov, tickers)

        assert len(contributions) == 4
        total_pct = sum(c.risk_contribution_pct for c in contributions)
        assert abs(total_pct - 100.0) < 1.0

    def test_structure(self):
        ohlcv = _make_multi_ohlcv(n_assets=3, base_seed=606)
        cov, tickers, _ = _build_covariance_matrix(ohlcv)

        weights = {tickers[0]: 0.5, tickers[1]: 0.3, tickers[2]: 0.2}
        contributions = compute_risk_contributions(weights, cov, tickers)

        for rc in contributions:
            assert isinstance(rc, RiskContribution)
            assert rc.ticker in tickers

    def test_empty_input(self):
        contributions = compute_risk_contributions({}, np.empty((0, 0)), [])
        assert contributions == []


# ---------------------------------------------------------------------------
# Test: Single asset edge case
# ---------------------------------------------------------------------------

class TestSingleAsset:
    def test_markowitz_single(self):
        ohlcv = {"005930": _make_ohlcv(seed=10)}
        result = optimize_markowitz(ohlcv)

        assert result.weights == {"005930": 1.0}
        assert result.effective_n == 1.0

    def test_risk_parity_single(self):
        ohlcv = {"005930": _make_ohlcv(seed=20)}
        result = optimize_risk_parity(ohlcv)

        assert result.weights == {"005930": 1.0}

    def test_min_variance_single(self):
        ohlcv = {"005930": _make_ohlcv(seed=30)}
        result = optimize_min_variance(ohlcv)

        assert result.weights == {"005930": 1.0}

    def test_bl_single(self):
        ohlcv = {"005930": _make_ohlcv(seed=40)}
        result = optimize_black_litterman(
            ohlcv, views=[], market_weights={"005930": 1.0},
        )
        assert result.weights == {"005930": 1.0}


# ---------------------------------------------------------------------------
# Test: Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_markowitz_empty(self):
        result = optimize_markowitz({})
        assert result.weights == {}
        assert result.expected_return == 0.0

    def test_risk_parity_empty(self):
        result = optimize_risk_parity({})
        assert result.weights == {}

    def test_min_variance_empty(self):
        result = optimize_min_variance({})
        assert result.weights == {}

    def test_bl_empty(self):
        result = optimize_black_litterman({}, views=[], market_weights={})
        assert result.weights == {}

    def test_frontier_empty(self):
        frontier = compute_efficient_frontier({})
        assert frontier.points == []

    def test_none_dataframes(self):
        ohlcv = {"A": None, "B": None}
        result = optimize_markowitz(ohlcv)
        assert result.weights == {}


# ---------------------------------------------------------------------------
# Test: Format output
# ---------------------------------------------------------------------------

class TestFormatOutput:
    def test_returns_string(self):
        ohlcv = _make_multi_ohlcv(n_assets=3, base_seed=707)
        result = optimize_markowitz(ohlcv)
        text = format_portfolio_optimization(result)

        assert isinstance(text, str)
        assert len(text) > 0

    def test_with_ticker_names(self):
        ohlcv = _make_multi_ohlcv(n_assets=3, base_seed=808)
        tickers = list(ohlcv.keys())
        result = optimize_markowitz(ohlcv)
        names = {tickers[0]: "삼성전자", tickers[1]: "SK하이닉스", tickers[2]: "NAVER"}
        text = format_portfolio_optimization(result, ticker_names=names)

        assert isinstance(text, str)
        # At least one name should appear
        assert any(name in text for name in names.values())

    def test_contains_key_metrics(self):
        ohlcv = _make_multi_ohlcv(n_assets=3, base_seed=909)
        result = optimize_markowitz(ohlcv)
        text = format_portfolio_optimization(result)

        assert "기대수익률" in text
        assert "변동성" in text
        assert "샤프비율" in text
        assert "포트폴리오 최적화 결과" in text

    def test_no_parse_mode_chars(self):
        """Ensure no HTML or Markdown formatting characters."""
        ohlcv = _make_multi_ohlcv(n_assets=4, base_seed=111)
        result = optimize_markowitz(ohlcv)
        text = format_portfolio_optimization(result)

        # No HTML tags
        assert "<b>" not in text
        assert "</b>" not in text
        assert "<i>" not in text
        # No Markdown bold
        assert "**" not in text
