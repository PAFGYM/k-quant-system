"""Tests for kstock.signal.cross_asset (cross-asset correlation analysis)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.signal.cross_asset import (
    AssetCorrelation,
    CrossAssetReport,
    CrossAssetSignal,
    TailDependency,
    compute_asset_correlations,
    compute_diversification_ratio,
    compute_regime_correlations,
    compute_tail_dependency,
    detect_cross_asset_signals,
    format_cross_asset_report,
    generate_cross_asset_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_returns(n: int = 300, seed: int = 42) -> pd.Series:
    """Generate random daily returns."""
    rng = np.random.RandomState(seed)
    return pd.Series(rng.randn(n) * 0.01, name="ret")


def _make_returns_map(n: int = 300, seed: int = 42) -> dict:
    """Generate a returns_map with 3 uncorrelated assets."""
    rng = np.random.RandomState(seed)
    return {
        "A": pd.Series(rng.randn(n) * 0.01),
        "B": pd.Series(rng.randn(n) * 0.01),
        "C": pd.Series(rng.randn(n) * 0.01),
    }


# ---------------------------------------------------------------------------
# TestCorrelations
# ---------------------------------------------------------------------------

class TestCorrelations:
    def test_identical_returns_corr_one(self) -> None:
        """Identical series -> correlation = 1."""
        s = _make_returns(100, seed=1)
        result = compute_asset_correlations({"X": s, "Y": s}, windows=[30])
        assert len(result) == 1
        assert result[0].correlation_30d == pytest.approx(1.0, abs=1e-6)

    def test_inverse_returns_corr_neg_one(self) -> None:
        """Perfectly inverse series -> correlation = -1."""
        s = _make_returns(100, seed=2)
        result = compute_asset_correlations({"X": s, "Y": -s}, windows=[30])
        assert len(result) == 1
        assert result[0].correlation_30d == pytest.approx(-1.0, abs=1e-6)

    def test_empty_input(self) -> None:
        """Empty or single-asset map -> empty list."""
        assert compute_asset_correlations({}) == []
        assert compute_asset_correlations({"A": _make_returns(50)}) == []

    def test_regime_classification(self) -> None:
        """Rolling change determines regime label."""
        s = _make_returns(300, seed=10)
        # Force a scenario with windows [30, 90]
        results = compute_asset_correlations(
            {"X": s, "Y": s * 0.5 + _make_returns(300, seed=20) * 0.5},
            windows=[30, 90, 252],
        )
        assert len(results) == 1
        assert results[0].regime in ("strengthening", "weakening", "stable")

    def test_multiple_pairs(self) -> None:
        """3 assets -> 3 pairs."""
        rm = _make_returns_map(100)
        results = compute_asset_correlations(rm, windows=[30])
        assert len(results) == 3  # C(3,2) = 3

    def test_short_series_returns_zero(self) -> None:
        """Series shorter than window -> correlation falls back to 0.0."""
        s = _make_returns(10, seed=3)
        result = compute_asset_correlations({"X": s, "Y": s}, windows=[30])
        assert len(result) == 1
        # Not enough data for 30-day window -> 0.0 fallback
        assert result[0].correlation_30d == 0.0


# ---------------------------------------------------------------------------
# TestTailDependency
# ---------------------------------------------------------------------------

class TestTailDependency:
    def test_independent_low_tail(self) -> None:
        """Independent random series -> low tail dependency."""
        rng = np.random.RandomState(99)
        a = pd.Series(rng.randn(2000) * 0.01)
        b = pd.Series(rng.randn(2000) * 0.01)
        td = compute_tail_dependency(a, b, asset_a="A", asset_b="B")
        # For independent series, conditional prob ~ threshold_pct/100
        assert td.lower_tail < 0.2
        assert td.upper_tail < 0.2

    def test_identical_high_tail(self) -> None:
        """Identical series -> very high tail dependency."""
        s = _make_returns(2000, seed=7)
        td = compute_tail_dependency(s, s, asset_a="X", asset_b="X")
        assert td.lower_tail > 0.8
        assert td.upper_tail > 0.8

    def test_joint_crash_prob_range(self) -> None:
        """Joint crash probability must be in [0, 1]."""
        a = _make_returns(500, seed=11)
        b = _make_returns(500, seed=12)
        td = compute_tail_dependency(a, b)
        assert 0.0 <= td.joint_crash_prob <= 1.0

    def test_insufficient_data(self) -> None:
        """Very short series -> zero tail dependency."""
        a = pd.Series([0.01, -0.01, 0.02])
        b = pd.Series([0.01, -0.02, 0.01])
        td = compute_tail_dependency(a, b)
        assert td.lower_tail == 0.0
        assert td.upper_tail == 0.0

    def test_asymmetry_sign(self) -> None:
        """Asymmetry = lower_tail - upper_tail."""
        s = _make_returns(2000, seed=13)
        td = compute_tail_dependency(s, s)
        assert td.asymmetry == pytest.approx(
            td.lower_tail - td.upper_tail, abs=1e-4,
        )


# ---------------------------------------------------------------------------
# TestRegimeCorrelation
# ---------------------------------------------------------------------------

class TestRegimeCorrelation:
    def test_bear_higher_corr(self) -> None:
        """Bear regime tends to have higher avg correlation than bull.

        We construct correlated down-moves in bear and independent in bull.
        """
        n = 600
        rng = np.random.RandomState(50)
        common = rng.randn(n) * 0.01

        # Bull: independent noise
        a_bull = rng.randn(n) * 0.01
        b_bull = rng.randn(n) * 0.01

        # Bear: correlated
        a_bear = common + rng.randn(n) * 0.002
        b_bear = common + rng.randn(n) * 0.002

        a = pd.Series(np.concatenate([a_bull, a_bear]))
        b = pd.Series(np.concatenate([b_bull, b_bear]))
        labels = ["bull"] * n + ["bear"] * n

        results = compute_regime_correlations({"A": a, "B": b}, labels)
        regimes = {r.regime: r for r in results}

        assert "bull" in regimes
        assert "bear" in regimes
        assert regimes["bear"].avg_correlation > regimes["bull"].avg_correlation

    def test_three_regimes(self) -> None:
        """Three distinct regimes are separated correctly."""
        n = 100
        rm = _make_returns_map(n * 3, seed=60)
        labels = ["bull"] * n + ["bear"] * n + ["neutral"] * n
        results = compute_regime_correlations(rm, labels)
        regime_names = {r.regime for r in results}
        assert regime_names == {"bear", "bull", "neutral"}

    def test_empty_labels(self) -> None:
        """Empty regime labels -> empty result."""
        rm = _make_returns_map(100)
        assert compute_regime_correlations(rm, []) == []


# ---------------------------------------------------------------------------
# TestSignals
# ---------------------------------------------------------------------------

class TestSignals:
    def test_correlation_breakdown(self) -> None:
        """Large rolling_change triggers correlation_breakdown signal."""
        corrs = [
            AssetCorrelation(
                asset_a="KOSPI",
                asset_b="SPX",
                correlation_30d=0.3,
                correlation_90d=0.7,
                correlation_252d=0.65,
                rolling_change=-0.4,
                regime="weakening",
            ),
        ]
        signals = detect_cross_asset_signals(corrs, [])
        types = [s.signal_type for s in signals]
        assert "correlation_breakdown" in types

    def test_safe_haven_activation(self) -> None:
        """Equity-GOLD inverse correlation strengthening triggers signal."""
        corrs = [
            AssetCorrelation(
                asset_a="GOLD",
                asset_b="KOSPI",
                correlation_30d=-0.4,
                correlation_90d=-0.1,
                correlation_252d=0.0,
                rolling_change=-0.3,
                regime="weakening",
            ),
        ]
        signals = detect_cross_asset_signals(corrs, [])
        types = [s.signal_type for s in signals]
        assert "safe_haven_activation" in types

    def test_contagion_risk(self) -> None:
        """High lower tail dependency triggers contagion_risk signal."""
        tails = [
            TailDependency(
                asset_a="KOSPI",
                asset_b="SPX",
                lower_tail=0.55,
                upper_tail=0.2,
                asymmetry=0.35,
                joint_crash_prob=0.04,
            ),
        ]
        signals = detect_cross_asset_signals([], tails)
        types = [s.signal_type for s in signals]
        assert "contagion_risk" in types

    def test_no_signals_on_calm_market(self) -> None:
        """Small changes and low tail -> no signals."""
        corrs = [
            AssetCorrelation(
                asset_a="A", asset_b="B",
                correlation_30d=0.5, correlation_90d=0.5,
                correlation_252d=0.5,
                rolling_change=0.0, regime="stable",
            ),
        ]
        tails = [
            TailDependency(
                asset_a="A", asset_b="B",
                lower_tail=0.1, upper_tail=0.1,
                asymmetry=0.0, joint_crash_prob=0.005,
            ),
        ]
        signals = detect_cross_asset_signals(corrs, tails)
        assert len(signals) == 0

    def test_bear_regime_amplifies(self) -> None:
        """Bear regime increases contagion_risk strength."""
        tails = [
            TailDependency(
                asset_a="KOSPI", asset_b="SPX",
                lower_tail=0.5, upper_tail=0.2,
                asymmetry=0.3, joint_crash_prob=0.03,
            ),
        ]
        neutral = detect_cross_asset_signals([], tails, "neutral")
        bear = detect_cross_asset_signals([], tails, "bear")
        s_neutral = [s for s in neutral if s.signal_type == "contagion_risk"]
        s_bear = [s for s in bear if s.signal_type == "contagion_risk"]
        assert len(s_neutral) == 1 and len(s_bear) == 1
        assert s_bear[0].strength >= s_neutral[0].strength


# ---------------------------------------------------------------------------
# TestDiversification
# ---------------------------------------------------------------------------

class TestDiversification:
    def test_uncorrelated_dr_above_one(self) -> None:
        """Uncorrelated assets -> DR > 1."""
        rm = _make_returns_map(500, seed=70)
        dr = compute_diversification_ratio(rm)
        assert dr > 1.0

    def test_perfectly_correlated_dr_near_one(self) -> None:
        """Identical returns -> DR approx 1."""
        s = _make_returns(500, seed=71)
        rm = {"A": s, "B": s, "C": s}
        dr = compute_diversification_ratio(rm)
        assert dr == pytest.approx(1.0, abs=0.05)

    def test_empty_returns(self) -> None:
        """Empty map -> DR = 1."""
        assert compute_diversification_ratio({}) == 1.0

    def test_custom_weights(self) -> None:
        """Custom weights are respected."""
        rm = _make_returns_map(500, seed=72)
        dr = compute_diversification_ratio(rm, weights={"A": 0.8, "B": 0.1, "C": 0.1})
        assert dr >= 1.0

    def test_single_asset(self) -> None:
        """Single asset -> DR = 1."""
        dr = compute_diversification_ratio({"A": _make_returns(100)})
        assert dr == 1.0


# ---------------------------------------------------------------------------
# TestFormat
# ---------------------------------------------------------------------------

class TestFormat:
    def test_returns_string(self) -> None:
        """format_cross_asset_report returns str."""
        report = CrossAssetReport(
            correlations=[], tail_deps=[], regime_correlations=[],
            signals=[], diversification_ratio=1.5,
        )
        text = format_cross_asset_report(report)
        assert isinstance(text, str)
        assert "크로스 에셋" in text

    def test_full_report_format(self) -> None:
        """End-to-end: generate + format."""
        rm = _make_returns_map(300, seed=80)
        labels = ["bull"] * 150 + ["bear"] * 150
        report = generate_cross_asset_report(
            rm, weights={"A": 0.5, "B": 0.3, "C": 0.2}, regime_labels=labels,
        )
        text = format_cross_asset_report(report)
        assert isinstance(text, str)
        assert "분산 효과" in text
        assert len(text) > 50
