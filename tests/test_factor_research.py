"""Tests for kstock.signal.factor_research module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.signal.factor_research import (
    FactorDecay,
    FactorResearchReport,
    FactorShock,
    TurnoverAnalysis,
    compute_factor_crowding,
    compute_factor_decay,
    compute_turnover_analysis,
    detect_factor_shocks,
    format_factor_research,
    generate_factor_report,
    optimize_factor_weights,
)

# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

np.random.seed(42)

_FACTOR_NAMES = ["momentum", "value", "quality", "size", "volatility", "investment"]


def _make_factor_returns(
    n_days: int = 300,
    factors: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate random factor return series with known properties."""
    rng = np.random.RandomState(seed)
    if factors is None:
        factors = _FACTOR_NAMES

    dates = pd.bdate_range(end="2026-03-01", periods=n_days)
    data = {}
    for f in factors:
        data[f] = rng.normal(0.0005, 0.01, n_days)

    # Forward return: loosely correlated with first factor
    fwd = 0.3 * data[factors[0]] + rng.normal(0, 0.008, n_days)
    data["forward_return"] = fwd

    return pd.DataFrame(data, index=dates)


def _make_factor_matrix(
    n_days: int = 300,
    factors: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate time-series factor score matrix (without forward_return)."""
    rng = np.random.RandomState(seed)
    if factors is None:
        factors = _FACTOR_NAMES

    dates = pd.bdate_range(end="2026-03-01", periods=n_days)
    data = {f: rng.normal(0, 1, n_days) for f in factors}
    return pd.DataFrame(data, index=dates)


def _make_returns_series(n_days: int = 300, seed: int = 42) -> pd.Series:
    """Generate random return series."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(end="2026-03-01", periods=n_days)
    return pd.Series(rng.normal(0.0003, 0.015, n_days), index=dates)


def _make_portfolio_history(
    n_periods: int = 12,
    n_stocks: int = 10,
    seed: int = 42,
) -> list[dict]:
    """Generate portfolio history with gradual weight changes."""
    rng = np.random.RandomState(seed)
    tickers = [f"T{i:03d}" for i in range(n_stocks)]
    history = []

    dates = pd.bdate_range(end="2026-03-01", periods=n_periods, freq="MS")

    for i, dt in enumerate(dates):
        raw_weights = rng.dirichlet(np.ones(n_stocks))
        holdings = {t: float(w) for t, w in zip(tickers, raw_weights)}
        history.append({
            "date": dt.strftime("%Y-%m-%d"),
            "holdings": holdings,
        })

    return history


def _make_cross_sectional_factor_scores(
    n_stocks: int = 50,
    factors: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Factor scores (ticker x factors) for crowding tests."""
    rng = np.random.RandomState(seed)
    if factors is None:
        factors = _FACTOR_NAMES
    tickers = [f"T{i:03d}" for i in range(n_stocks)]
    data = {f: rng.normal(0, 1, n_stocks) for f in factors}
    return pd.DataFrame(data, index=tickers)


# ---------------------------------------------------------------------------
# Tests: Factor Decay
# ---------------------------------------------------------------------------


class TestFactorDecay:
    def test_decay_returns_all_factors(self):
        df = _make_factor_returns(300)
        decays = compute_factor_decay(df)
        factor_names = [c for c in df.columns if c != "forward_return"]
        assert len(decays) == len(factor_names)
        for d in decays:
            assert d.factor_name in factor_names

    def test_half_life_positive(self):
        df = _make_factor_returns(300)
        decays = compute_factor_decay(df)
        for d in decays:
            assert d.half_life_days > 0, (
                f"{d.factor_name} half_life={d.half_life_days} should be > 0"
            )

    def test_ic_decay_trend(self):
        """IC at short horizon should generally be >= IC at long horizon (in abs)."""
        rng = np.random.RandomState(123)
        n = 500
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        # Strong signal that decays with distance
        signal = rng.normal(0, 1, n)
        fwd_ret = 0.5 * signal + rng.normal(0, 0.5, n)
        df = pd.DataFrame(
            {"strong_signal": signal, "forward_return": fwd_ret},
            index=dates,
        )
        decays = compute_factor_decay(df, horizons=[1, 5, 20, 60])
        d = decays[0]
        # At minimum, IC at horizon 1 should be larger than at horizon 60
        assert abs(d.ic_by_horizon[1]) >= abs(d.ic_by_horizon[60]) - 0.1, (
            f"IC should generally decay: h1={d.ic_by_horizon[1]:.3f} "
            f"vs h60={d.ic_by_horizon[60]:.3f}"
        )

    def test_effective_horizon_positive(self):
        """With a real signal, effective_horizon should be > 0."""
        rng = np.random.RandomState(77)
        n = 400
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        signal = rng.normal(0, 1, n)
        fwd = 0.4 * signal + rng.normal(0, 0.5, n)
        df = pd.DataFrame(
            {"sig": signal, "forward_return": fwd}, index=dates,
        )
        decays = compute_factor_decay(df, horizons=[1, 5, 10, 20])
        assert decays[0].effective_horizon > 0

    def test_decay_curve_length_matches_horizons(self):
        horizons = [1, 5, 10, 20, 40]
        df = _make_factor_returns(200)
        decays = compute_factor_decay(df, horizons=horizons)
        for d in decays:
            assert len(d.decay_curve) == len(horizons)
            assert len(d.ic_by_horizon) == len(horizons)

    def test_empty_input(self):
        df = pd.DataFrame(columns=["forward_return"])
        decays = compute_factor_decay(df)
        assert decays == []


# ---------------------------------------------------------------------------
# Tests: Turnover Analysis
# ---------------------------------------------------------------------------


class TestTurnover:
    def test_avg_turnover_range(self):
        history = _make_portfolio_history(12, 10)
        fm = _make_cross_sectional_factor_scores(10)
        ta = compute_turnover_analysis(history, fm)
        assert 0.0 <= ta.avg_turnover_pct <= 200.0, (
            f"Avg turnover {ta.avg_turnover_pct} out of range"
        )

    def test_cost_positive(self):
        history = _make_portfolio_history(12, 10)
        fm = _make_cross_sectional_factor_scores(10)
        ta = compute_turnover_analysis(history, fm)
        assert ta.turnover_cost_pct >= 0.0

    def test_empty_history(self):
        fm = _make_cross_sectional_factor_scores(10)
        ta = compute_turnover_analysis([], fm)
        assert ta.avg_turnover_pct == 0.0
        assert ta.turnover_cost_pct == 0.0

    def test_single_period(self):
        history = _make_portfolio_history(1, 5)
        fm = _make_cross_sectional_factor_scores(5)
        ta = compute_turnover_analysis(history, fm)
        assert ta.avg_turnover_pct == 0.0

    def test_turnover_by_factor_keys(self):
        history = _make_portfolio_history(6, 10)
        fm = _make_cross_sectional_factor_scores(10)
        ta = compute_turnover_analysis(history, fm)
        for f in fm.columns:
            assert f in ta.turnover_by_factor


# ---------------------------------------------------------------------------
# Tests: Dynamic Weights
# ---------------------------------------------------------------------------


class TestDynamicWeights:
    def test_weights_sum_to_one(self):
        fm = _make_factor_matrix(200)
        rets = _make_returns_series(200)
        weights = optimize_factor_weights(fm, rets, method="ic_weighted")
        total = sum(dw.current_weight for dw in weights)
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_weights_sum_to_one_regime(self):
        fm = _make_factor_matrix(200)
        rets = _make_returns_series(200)
        weights = optimize_factor_weights(fm, rets, method="regime")
        total = sum(dw.current_weight for dw in weights)
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_weights_sum_to_one_momentum(self):
        fm = _make_factor_matrix(200)
        rets = _make_returns_series(200)
        weights = optimize_factor_weights(fm, rets, method="momentum")
        total = sum(dw.current_weight for dw in weights)
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_high_ic_factor_gets_higher_weight(self):
        """Factor strongly correlated with returns should get higher weight."""
        rng = np.random.RandomState(99)
        n = 200
        dates = pd.bdate_range(end="2026-03-01", periods=n)

        good_signal = rng.normal(0, 1, n)
        noise_signal = rng.normal(0, 1, n)
        returns = pd.Series(
            0.5 * good_signal + rng.normal(0, 0.3, n),
            index=dates,
        )
        fm = pd.DataFrame(
            {"good_factor": good_signal, "noise_factor": noise_signal},
            index=dates,
        )
        weights = optimize_factor_weights(fm, returns, method="ic_weighted")
        w_map = {dw.factor_name: dw.current_weight for dw in weights}
        assert w_map["good_factor"] > w_map["noise_factor"], (
            f"Good factor weight {w_map['good_factor']:.4f} should exceed "
            f"noise {w_map['noise_factor']:.4f}"
        )

    def test_empty_factor_matrix(self):
        fm = pd.DataFrame()
        rets = pd.Series(dtype=float)
        weights = optimize_factor_weights(fm, rets)
        assert weights == []

    def test_all_methods_return_correct_count(self):
        fm = _make_factor_matrix(100, factors=["a", "b", "c"])
        rets = _make_returns_series(100)
        for method in ["ic_weighted", "momentum", "regime"]:
            weights = optimize_factor_weights(fm, rets, method=method)
            assert len(weights) == 3, (
                f"Method {method} returned {len(weights)} weights, expected 3"
            )


# ---------------------------------------------------------------------------
# Tests: Factor Shocks
# ---------------------------------------------------------------------------


class TestFactorShock:
    def test_no_shocks_normal_market(self):
        """Normal random returns should produce no shocks."""
        rng = np.random.RandomState(42)
        n = 300
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        df = pd.DataFrame(
            {"f1": rng.normal(0, 0.01, n), "f2": rng.normal(0, 0.01, n)},
            index=dates,
        )
        shocks = detect_factor_shocks(df, lookback=252, threshold=2.5)
        # Should have few/no shocks with normal data
        assert len(shocks) <= 2, (
            f"Normal market should have few shocks, got {len(shocks)}"
        )

    def test_extreme_return_triggers_shock(self):
        """Injecting an extreme return should trigger a shock."""
        rng = np.random.RandomState(42)
        n = 300
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        vals = rng.normal(0, 0.01, n)
        # Inject extreme value at the end
        vals[-1] = 0.10  # 10x normal std
        df = pd.DataFrame({"extreme_factor": vals}, index=dates)
        shocks = detect_factor_shocks(df, lookback=252, threshold=2.5)
        assert len(shocks) >= 1, "Extreme return should trigger shock"
        assert shocks[0].factor_name == "extreme_factor"
        assert abs(shocks[0].z_score) > 2.5

    def test_negative_extreme_triggers_long_squeeze(self):
        """Large negative return should be a long_squeeze."""
        rng = np.random.RandomState(42)
        n = 300
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        vals = rng.normal(0, 0.01, n)
        vals[-1] = -0.10
        df = pd.DataFrame({"crash_factor": vals}, index=dates)
        shocks = detect_factor_shocks(df, lookback=252, threshold=2.5)
        assert len(shocks) >= 1
        assert shocks[0].direction == "long_squeeze"

    def test_recommendation_severity(self):
        """Higher z-score should produce stronger recommendation."""
        rng = np.random.RandomState(42)
        n = 300
        dates = pd.bdate_range(end="2026-03-01", periods=n)
        vals = rng.normal(0, 0.01, n)
        vals[-1] = 0.15  # ~15x std -> z > 4
        df = pd.DataFrame({"severe": vals}, index=dates)
        shocks = detect_factor_shocks(df, lookback=252, threshold=2.5)
        assert len(shocks) >= 1
        assert shocks[0].recommendation == "reduce_exposure"

    def test_empty_input(self):
        df = pd.DataFrame()
        shocks = detect_factor_shocks(df)
        assert shocks == []

    def test_short_input(self):
        df = pd.DataFrame({"f": [0.01, 0.02]})
        shocks = detect_factor_shocks(df)
        assert shocks == []


# ---------------------------------------------------------------------------
# Tests: Factor Crowding
# ---------------------------------------------------------------------------


class TestFactorCrowding:
    def test_uniform_distribution_low_crowding(self):
        """Equal weights across all stocks should yield low crowding."""
        n_stocks = 50
        scores = _make_cross_sectional_factor_scores(n_stocks, seed=42)
        holdings = [
            {"ticker": f"T{i:03d}", "weight": 1.0 / n_stocks}
            for i in range(n_stocks)
        ]
        crowding = compute_factor_crowding(scores, holdings)
        for f, c in crowding.items():
            assert c < 0.3, (
                f"Uniform weights should have low crowding, got {f}={c:.4f}"
            )

    def test_concentrated_holdings_high_crowding(self):
        """All weight in top quintile should yield high crowding."""
        n_stocks = 50
        factors = ["test_factor"]
        rng = np.random.RandomState(42)
        scores_raw = rng.normal(0, 1, n_stocks)
        tickers = [f"T{i:03d}" for i in range(n_stocks)]
        scores = pd.DataFrame({"test_factor": scores_raw}, index=tickers)

        # Put all weight in the top 10 scoring stocks
        sorted_tickers = scores["test_factor"].sort_values(ascending=False).index
        top_10 = list(sorted_tickers[:10])
        holdings = [
            {"ticker": t, "weight": 0.1 if t in top_10 else 0.0}
            for t in tickers
        ]
        crowding = compute_factor_crowding(scores, holdings)
        assert crowding["test_factor"] > 0.3, (
            f"Concentrated holdings should have high crowding, "
            f"got {crowding['test_factor']:.4f}"
        )

    def test_empty_scores(self):
        scores = pd.DataFrame()
        holdings = [{"ticker": "A", "weight": 1.0}]
        crowding = compute_factor_crowding(scores, holdings)
        assert crowding == {}

    def test_empty_holdings(self):
        scores = _make_cross_sectional_factor_scores(20)
        crowding = compute_factor_crowding(scores, [])
        for v in crowding.values():
            assert v == 0.0


# ---------------------------------------------------------------------------
# Tests: Report Generation & Formatting
# ---------------------------------------------------------------------------


class TestFormat:
    def test_format_returns_string(self):
        fm = _make_factor_returns(200)
        rets = _make_returns_series(200)
        history = _make_portfolio_history(6, 10)
        report = generate_factor_report(fm, rets, history)
        text = format_factor_research(report)
        assert isinstance(text, str)

    def test_format_contains_sections(self):
        fm = _make_factor_returns(200)
        rets = _make_returns_series(200)
        history = _make_portfolio_history(6, 10)
        report = generate_factor_report(fm, rets, history)
        text = format_factor_research(report)
        assert "IC Decay" in text
        assert "Turnover" in text
        assert "Dynamic Weights" in text
        assert "Regime Tilts" in text

    def test_report_dataclass_fields(self):
        fm = _make_factor_returns(200)
        rets = _make_returns_series(200)
        history = _make_portfolio_history(6, 10)
        report = generate_factor_report(fm, rets, history)
        assert isinstance(report, FactorResearchReport)
        assert isinstance(report.factor_decays, list)
        assert isinstance(report.turnover, TurnoverAnalysis)
        assert isinstance(report.dynamic_weights, list)
        assert isinstance(report.shocks, list)
        assert isinstance(report.regime_tilts, dict)

    def test_report_with_shocks(self):
        """Inject a shock and verify it appears in the report."""
        rng = np.random.RandomState(42)
        n = 300
        dates = pd.bdate_range(end="2026-03-01", periods=n)

        factor_data = {f: rng.normal(0, 0.01, n) for f in _FACTOR_NAMES}
        factor_data["momentum"][-1] = 0.15  # extreme shock
        fwd = rng.normal(0, 0.01, n)
        factor_data["forward_return"] = fwd
        fm = pd.DataFrame(factor_data, index=dates)
        rets = pd.Series(fwd, index=dates)
        history = _make_portfolio_history(6, 10)

        report = generate_factor_report(fm, rets, history)
        text = format_factor_research(report)

        # Should have at least the momentum shock
        shock_names = [s.factor_name for s in report.shocks]
        assert "momentum" in shock_names, (
            f"Expected momentum shock, got shocks: {shock_names}"
        )
        assert "Factor Shocks" in text

    def test_regime_bull(self):
        fm = _make_factor_returns(200)
        rets = _make_returns_series(200)
        history = _make_portfolio_history(4, 5)
        report = generate_factor_report(fm, rets, history, regime="bull")
        assert report.regime_tilts["momentum"] > 1.0
        assert report.regime_tilts["quality"] < 1.0
