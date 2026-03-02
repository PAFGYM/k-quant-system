"""Tests for kstock.signal.multi_factor module."""

from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from kstock.signal.multi_factor import (
    FactorRanking,
    MultiFactorProfile,
    _zscore,
    build_factor_matrix,
    compute_factor_loadings,
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
    compute_volatility_factor,
    format_factor_profile,
    rank_by_factor,
    score_stock_multifactor,
)

# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

random.seed(42)
np.random.seed(42)


def _make_universe(n: int = 20) -> list[dict]:
    return [
        {
            "ticker": f"T{i:03d}",
            "name": f"Stock{i}",
            "market_cap": random.uniform(1e9, 1e12),
            "per": random.uniform(5, 50),
            "pbr": random.uniform(0.3, 5.0),
            "div_yield": random.uniform(0, 5),
            "roe": random.uniform(-5, 30),
            "debt_ratio": random.uniform(10, 200),
            "asset_growth_pct": random.uniform(-10, 30),
        }
        for i in range(n)
    ]


def _make_ohlcv(n_days: int = 300, trend: float = 0.0005) -> pd.DataFrame:
    """Generate synthetic OHLCV data with optional drift."""
    dates = pd.bdate_range(end="2026-03-01", periods=n_days)
    close = [10000.0]
    for _ in range(n_days - 1):
        ret = trend + np.random.normal(0, 0.02)
        close.append(close[-1] * (1 + ret))
    close = np.array(close)
    return pd.DataFrame(
        {
            "close": close,
            "open": close * (1 + np.random.normal(0, 0.005, n_days)),
            "high": close * (1 + abs(np.random.normal(0, 0.01, n_days))),
            "low": close * (1 - abs(np.random.normal(0, 0.01, n_days))),
            "volume": np.random.randint(1000, 100000, n_days),
        },
        index=dates,
    )


def _make_ohlcv_map(universe: list[dict], n_days: int = 300) -> dict[str, pd.DataFrame]:
    return {row["ticker"]: _make_ohlcv(n_days) for row in universe}


# ---------------------------------------------------------------------------
# Tests: _zscore
# ---------------------------------------------------------------------------


class TestZscore:
    def test_zscore_normalization(self):
        vals = {f"T{i}": random.gauss(50, 15) for i in range(100)}
        z = _zscore(vals)
        arr = np.array(list(z.values()))
        assert abs(np.mean(arr)) < 0.1, "z-score mean should be ~0"
        assert abs(np.std(arr, ddof=1) - 1.0) < 0.15, "z-score std should be ~1"

    def test_zscore_single_value(self):
        z = _zscore({"A": 100.0})
        assert z["A"] == 0.0

    def test_zscore_identical_values(self):
        z = _zscore({"A": 5.0, "B": 5.0, "C": 5.0})
        assert all(v == 0.0 for v in z.values())


# ---------------------------------------------------------------------------
# Tests: Individual factors
# ---------------------------------------------------------------------------


class TestSizeFactor:
    def test_size_factor(self):
        caps = {"small": 1e9, "mid": 1e10, "big": 1e12}
        z = compute_size_factor(caps)
        # Small cap should have highest (most positive) z
        assert z["small"] > z["big"], "Small cap should have positive tilt"


class TestValueFactor:
    def test_value_factor(self):
        per = {"cheap": 5.0, "mid": 15.0, "expensive": 50.0}
        pbr = {"cheap": 0.5, "mid": 1.5, "expensive": 5.0}
        dy = {"cheap": 4.0, "mid": 2.0, "expensive": 0.5}
        z = compute_value_factor(per, pbr, dy)
        assert z["cheap"] > z["expensive"], "Low PER should have higher value score"


class TestMomentumFactor:
    def test_momentum_factor(self):
        np.random.seed(99)
        up = _make_ohlcv(300, trend=0.003)
        down = _make_ohlcv(300, trend=-0.003)
        flat = _make_ohlcv(300, trend=0.0)
        ohlcv = {"up": up, "down": down, "flat": flat}
        z = compute_momentum_factor(ohlcv, lookback=252, skip=21)
        assert z["up"] > z["down"], "Up-trending stock should have higher momentum"


class TestQualityFactor:
    def test_quality_factor(self):
        roe = {"good": 25.0, "mid": 10.0, "bad": -5.0}
        debt = {"good": 20.0, "mid": 80.0, "bad": 180.0}
        z = compute_quality_factor(roe, debt)
        assert z["good"] > z["bad"], "High ROE + low debt = higher quality"


class TestVolatilityFactor:
    def test_volatility_factor(self):
        np.random.seed(77)
        # Low vol stock: tiny random noise
        dates = pd.bdate_range(end="2026-03-01", periods=100)
        low_vol = pd.DataFrame(
            {"close": 10000 + np.cumsum(np.random.normal(0, 5, 100))}, index=dates
        )
        high_vol = pd.DataFrame(
            {"close": 10000 + np.cumsum(np.random.normal(0, 200, 100))}, index=dates
        )
        z = compute_volatility_factor({"low": low_vol, "high": high_vol}, lookback=60)
        assert z["low"] > z["high"], "Low vol stock should have positive score"


# ---------------------------------------------------------------------------
# Tests: build_factor_matrix
# ---------------------------------------------------------------------------


class TestBuildFactorMatrix:
    def test_build_factor_matrix_shape(self):
        universe = _make_universe(20)
        ohlcv = _make_ohlcv_map(universe)
        matrix = build_factor_matrix(universe, ohlcv)
        assert matrix.shape == (20, 6), f"Expected (20,6), got {matrix.shape}"
        assert list(matrix.columns) == [
            "size", "value", "momentum", "quality", "volatility", "investment"
        ]

    def test_build_empty(self):
        matrix = build_factor_matrix([], {})
        assert len(matrix) == 0


# ---------------------------------------------------------------------------
# Tests: factor loadings (OLS regression)
# ---------------------------------------------------------------------------


class TestFactorLoadings:
    def test_factor_loadings_r_squared(self):
        n = 100
        np.random.seed(123)
        factor = np.random.normal(0, 0.01, n)
        noise = np.random.normal(0, 0.005, n)
        port_ret = list(0.8 * factor + noise)
        factor_df = pd.DataFrame({"mkt": factor})
        result = compute_factor_loadings(port_ret, factor_df)
        assert 0.0 <= result.r_squared <= 1.0, "R-squared must be in [0,1]"
        assert result.r_squared > 0.3, "Should have meaningful R-squared"

    def test_factor_loadings_single_factor(self):
        """If portfolio = exactly 1.0 * factor, beta should be ~1.0."""
        n = 200
        np.random.seed(456)
        factor = np.random.normal(0, 0.01, n)
        rf_daily = 0.035 / 252.0
        # portfolio excess return = 1.0 * factor
        port_ret = list(factor + rf_daily)
        factor_df = pd.DataFrame({"mkt": factor})
        result = compute_factor_loadings(port_ret, factor_df, risk_free_rate=0.035)
        assert abs(result.factor_loadings["mkt"] - 1.0) < 0.1, (
            f"Beta should be ~1.0, got {result.factor_loadings['mkt']:.4f}"
        )
        assert result.r_squared > 0.95, f"R2 should be >0.95, got {result.r_squared:.4f}"

    def test_factor_loadings_too_few_obs(self):
        with pytest.raises(ValueError, match="at least 3"):
            compute_factor_loadings([0.01, 0.02], pd.DataFrame({"f": [0.01, 0.02]}))


# ---------------------------------------------------------------------------
# Tests: ranking
# ---------------------------------------------------------------------------


class TestRanking:
    def test_ranking_quintile_sizes(self):
        universe = _make_universe(25)
        ohlcv = _make_ohlcv_map(universe)
        matrix = build_factor_matrix(universe, ohlcv)
        ranking = rank_by_factor(matrix, "value")
        assert len(ranking.top_quintile) == 5
        assert len(ranking.bottom_quintile) == 5
        # No overlap
        assert set(ranking.top_quintile) & set(ranking.bottom_quintile) == set()

    def test_ic_perfect(self):
        """Perfect rank correlation should yield IC close to 1.0."""
        tickers = [f"T{i:03d}" for i in range(20)]
        # Factor values perfectly predict forward returns
        factor_vals = {t: float(i) for i, t in enumerate(tickers)}
        fwd_rets = {t: float(i) * 0.01 for i, t in enumerate(tickers)}
        matrix = pd.DataFrame({"test_factor": factor_vals}).rename_axis("ticker")
        ranking = rank_by_factor(matrix, "test_factor", forward_returns=fwd_rets)
        assert abs(ranking.ic - 1.0) < 0.05, f"IC should be ~1.0, got {ranking.ic:.4f}"

    def test_ranking_unknown_factor(self):
        matrix = pd.DataFrame({"size": [0.1]}, index=["T000"])
        with pytest.raises(ValueError, match="Unknown factor"):
            rank_by_factor(matrix, "nonexistent")


# ---------------------------------------------------------------------------
# Tests: composite scoring
# ---------------------------------------------------------------------------


class TestCompositeScoring:
    def test_composite_range(self):
        universe = _make_universe(30)
        ohlcv = _make_ohlcv_map(universe)
        matrix = build_factor_matrix(universe, ohlcv)
        for t in matrix.index:
            profile = score_stock_multifactor(t, matrix)
            assert 0.0 <= profile.composite_score <= 100.0, (
                f"Composite {profile.composite_score} out of range for {t}"
            )

    def test_quintile_range(self):
        universe = _make_universe(30)
        ohlcv = _make_ohlcv_map(universe)
        matrix = build_factor_matrix(universe, ohlcv)
        for t in matrix.index:
            profile = score_stock_multifactor(t, matrix)
            assert 1 <= profile.quintile <= 5, (
                f"Quintile {profile.quintile} out of range for {t}"
            )

    def test_missing_ticker(self):
        matrix = pd.DataFrame({"size": [0.1]}, index=["T000"])
        with pytest.raises(KeyError):
            score_stock_multifactor("MISSING", matrix)


# ---------------------------------------------------------------------------
# Tests: format output
# ---------------------------------------------------------------------------


class TestFormatOutput:
    def test_format_output(self):
        universe = _make_universe(10)
        ohlcv = _make_ohlcv_map(universe)
        matrix = build_factor_matrix(universe, ohlcv)
        profile = score_stock_multifactor("T000", matrix)
        text = format_factor_profile(profile)
        assert isinstance(text, str)
        assert "T000" in text
        assert "Composite" in text
        assert "Quintile" in text
        assert len(text) > 50, "Output should have meaningful content"
