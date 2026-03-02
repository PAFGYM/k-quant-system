"""Tests for rolling risk-adjusted metrics (v6.3)."""

from __future__ import annotations

import pytest

from kstock.core.performance_tracker import (
    RollingMetrics,
    compute_rolling_sharpe,
    compute_rolling_sortino,
    compute_information_ratio,
    compute_rolling_beta,
    compute_rolling_metrics,
)


# --- helpers ---

def _positive_returns(n: int = 120) -> list[float]:
    """Generate steadily positive daily returns (~+0.05%/day)."""
    return [0.0005 + 0.0001 * (i % 5) for i in range(n)]


def _negative_returns(n: int = 120) -> list[float]:
    return [-0.0005 - 0.0001 * (i % 5) for i in range(n)]


def _benchmark_returns(n: int = 120) -> list[float]:
    return [0.0002 + 0.00005 * (i % 3) for i in range(n)]


WINDOW = 60


# --- test_rolling_sharpe_positive ---

def test_rolling_sharpe_positive():
    rets = _positive_returns()
    sharpe = compute_rolling_sharpe(rets, window=WINDOW)
    assert len(sharpe) == len(rets)
    # After warmup, values should be positive for positive returns
    active = [v for v in sharpe[WINDOW - 1:] if v != 0.0]
    assert len(active) > 0
    assert all(v > 0 for v in active), f"Expected positive Sharpe, got {active[:5]}"


# --- test_rolling_sortino_ge_sharpe ---

def test_rolling_sortino_ge_sharpe():
    rets = _positive_returns()
    sharpe = compute_rolling_sharpe(rets, window=WINDOW)
    sortino = compute_rolling_sortino(rets, window=WINDOW)
    # Sortino >= Sharpe for positive returns (downside dev <= total std)
    for i in range(WINDOW - 1, len(rets)):
        assert sortino[i] >= sharpe[i] - 0.01, (
            f"day {i}: sortino={sortino[i]:.4f} < sharpe={sharpe[i]:.4f}"
        )


# --- test_information_ratio_outperformer ---

def test_information_ratio_outperformer():
    port = _positive_returns()
    bench = _benchmark_returns()
    ir = compute_information_ratio(port, bench, window=WINDOW)
    active = [v for v in ir[WINDOW - 1:] if v != 0.0]
    assert len(active) > 0
    assert all(v > 0 for v in active), "Outperformer should have positive IR"


# --- test_rolling_beta_self ---

def test_rolling_beta_self():
    rets = _positive_returns()
    beta = compute_rolling_beta(rets, rets, window=WINDOW)
    for i in range(WINDOW - 1, len(rets)):
        assert abs(beta[i] - 1.0) < 0.01, (
            f"day {i}: beta vs self should be ~1.0, got {beta[i]}"
        )


# --- test_warmup_period ---

def test_warmup_period():
    rets = _positive_returns()
    sharpe = compute_rolling_sharpe(rets, window=WINDOW)
    # First (window-1) values must be 0.0
    for i in range(WINDOW - 1):
        assert sharpe[i] == 0.0, f"Warmup index {i} should be 0.0"


# --- test_empty_returns ---

def test_empty_returns():
    assert compute_rolling_sharpe([]) == []
    assert compute_rolling_sortino([]) == []
    assert compute_information_ratio([], []) == []
    assert compute_rolling_beta([], []) == []
    assert compute_rolling_metrics([], []) == []


# --- test_rolling_metrics_integration ---

def test_rolling_metrics_integration():
    n = 130
    values = [10000.0]
    dates = ["2025-01-01"]
    for i in range(1, n):
        values.append(values[-1] * (1 + 0.0003 + 0.0001 * (i % 4)))
        dates.append(f"2025-{1 + i // 30:02d}-{1 + i % 28:02d}")

    bench_values = [10000.0]
    for i in range(1, n):
        bench_values.append(bench_values[-1] * (1 + 0.0002))

    metrics = compute_rolling_metrics(values, dates, bench_values, window=WINDOW)
    assert len(metrics) == n - 1  # returns start from index 1

    # Check type
    assert isinstance(metrics[0], RollingMetrics)

    # After warmup, sharpe should be populated
    m = metrics[-1]
    assert m.rolling_sharpe != 0.0
    assert m.rolling_sortino != 0.0
    assert m.date != ""
