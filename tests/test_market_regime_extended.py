"""Tests for extended market regime detection with HMM/GMM (v6.3)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import numpy as np
import pytest

from kstock.signal.market_regime import (
    HMMRegimeState,
    RegimeAnalysis,
    _fit_gmm_regime,
    detect_hmm_regime,
    detect_volatility_regime,
    detect_trend_regime,
    analyze_regime_extended,
    format_regime_analysis,
)


# --- helpers ---

def _bull_returns(n: int = 252) -> list[float]:
    """Strongly positive drift returns."""
    np.random.seed(42)
    return list(np.random.normal(0.002, 0.005, n))


def _bear_returns(n: int = 252) -> list[float]:
    """Strongly negative drift returns."""
    np.random.seed(42)
    return list(np.random.normal(-0.002, 0.005, n))


def _flat_returns(n: int = 252) -> list[float]:
    """Nearly zero volatility returns."""
    return [0.0001] * n


def _high_vol_returns(n: int = 252) -> list[float]:
    """High volatility returns."""
    np.random.seed(42)
    return list(np.random.normal(0.0, 0.05, n))


def _alternating_returns(n: int = 120) -> list[float]:
    """Alternating positive/negative returns (mean-reverting)."""
    return [0.01 * ((-1) ** i) for i in range(n)]


def _monotone_returns(n: int = 120) -> list[float]:
    """Steadily increasing returns (trending)."""
    return [0.001 + 0.0001 * i for i in range(n)]


def _mock_macro(vix: float = 15.0, regime: str = "neutral"):
    macro = MagicMock()
    macro.vix = vix
    macro.regime = regime
    return macro


# --- test_gmm_bull_detection ---

def test_gmm_bull_detection():
    rets = _bull_returns()
    result = detect_hmm_regime(rets)
    assert result is not None
    # With positive drift, the current state should lean bull
    assert result.label in ("bull", "transition")
    assert result.mean_return > -0.01


# --- test_gmm_bear_detection ---

def test_gmm_bear_detection():
    rets = _bear_returns()
    result = detect_hmm_regime(rets)
    assert result is not None
    assert result.label in ("bear", "transition")
    assert result.mean_return < 0.01


# --- test_gmm_convergence ---

def test_gmm_convergence():
    rets = np.random.RandomState(123).normal(0.0, 0.01, 200)
    means, stds, weights, seq = _fit_gmm_regime(rets)
    # Means should be in reasonable range
    assert all(-0.05 < m < 0.05 for m in means), f"Means out of range: {means}"
    # Stds should be positive
    assert all(s > 0 for s in stds)
    # Weights should sum to ~1
    assert abs(sum(weights) - 1.0) < 0.01


# --- test_few_data_points ---

def test_few_data_points():
    result = detect_hmm_regime([0.001] * 10)
    assert result is None


# --- test_volatility_low ---

def test_volatility_low():
    # Very low recent vol vs long-term
    rets = _high_vol_returns(200) + _flat_returns(60)
    regime = detect_volatility_regime(rets, lookback=60)
    assert regime == "low_vol"


# --- test_volatility_high ---

def test_volatility_high():
    rets = _flat_returns(200) + _high_vol_returns(60)
    regime = detect_volatility_regime(rets, lookback=60)
    assert regime == "high_vol"


# --- test_trend_trending ---

def test_trend_trending():
    rets = _monotone_returns(120)
    regime = detect_trend_regime(rets, lookback=60)
    assert regime == "trending", f"Expected trending, got {regime}"


# --- test_trend_mean_reverting ---

def test_trend_mean_reverting():
    rets = _alternating_returns(120)
    regime = detect_trend_regime(rets, lookback=60)
    assert regime == "mean_reverting", f"Expected mean_reverting, got {regime}"


# --- test_regime_analysis_integration ---

def test_regime_analysis_integration():
    macro = _mock_macro(vix=15.0, regime="neutral")
    rets = _bull_returns(252)

    analysis = analyze_regime_extended(
        macro=macro,
        daily_returns=rets,
        kospi_60d_return=5.0,
    )
    assert isinstance(analysis, RegimeAnalysis)
    assert analysis.current_regime is not None
    assert analysis.volatility_regime in ("low_vol", "normal", "high_vol")
    assert analysis.trend_regime in ("trending", "mean_reverting", "random")
    assert 0.0 <= analysis.transition_probability <= 1.0


# --- test_format_output ---

def test_format_output():
    macro = _mock_macro(vix=15.0, regime="neutral")
    rets = _bull_returns(252)
    analysis = analyze_regime_extended(
        macro=macro,
        daily_returns=rets,
    )
    text = format_regime_analysis(analysis)
    assert isinstance(text, str)
    assert len(text) > 0
    assert "시장 레짐" in text
