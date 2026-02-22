"""Tests for technical indicator computation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.features.technical import (
    TechnicalIndicators,
    compute_disparity,
    compute_indicators,
    compute_near_high_pct,
    compute_weekly_trend,
    compute_relative_strength_rank,
)


def _make_ohlcv(days: int = 60, base_price: float = 50000) -> pd.DataFrame:
    rng = np.random.default_rng(seed=42)
    dates = pd.bdate_range(end="2024-01-15", periods=days)
    returns = rng.normal(0.001, 0.02, size=days)
    prices = base_price * np.cumprod(1 + returns)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": prices * (1 + rng.uniform(-0.01, 0.01, days)),
            "high": prices * (1 + rng.uniform(0.005, 0.03, days)),
            "low": prices * (1 - rng.uniform(0.005, 0.03, days)),
            "close": prices,
            "volume": rng.integers(100_000, 5_000_000, size=days),
        }
    )


class TestComputeIndicators:
    def test_returns_technical_indicators(self):
        result = compute_indicators(_make_ohlcv())
        assert isinstance(result, TechnicalIndicators)

    def test_rsi_in_range(self):
        result = compute_indicators(_make_ohlcv())
        assert 0 <= result.rsi <= 100

    def test_bb_pctb_reasonable(self):
        result = compute_indicators(_make_ohlcv())
        assert -1 <= result.bb_pctb <= 2

    def test_macd_cross_values(self):
        result = compute_indicators(_make_ohlcv())
        assert result.macd_signal_cross in (-1, 0, 1)

    def test_atr_positive(self):
        result = compute_indicators(_make_ohlcv())
        assert result.atr >= 0
        assert result.atr_pct >= 0

    def test_minimum_rows(self):
        result = compute_indicators(_make_ohlcv(days=35))
        assert isinstance(result, TechnicalIndicators)


class TestV25Fields:
    def test_ema_fields(self):
        result = compute_indicators(_make_ohlcv())
        assert result.ema_50 > 0
        assert result.ema_200 > 0

    def test_golden_dead_cross(self):
        result = compute_indicators(_make_ohlcv())
        assert isinstance(result.golden_cross, bool)
        assert isinstance(result.dead_cross, bool)

    def test_high_52w(self):
        result = compute_indicators(_make_ohlcv())
        assert result.high_52w > 0

    def test_high_20d(self):
        result = compute_indicators(_make_ohlcv())
        assert result.high_20d > 0

    def test_volume_ratio(self):
        result = compute_indicators(_make_ohlcv())
        assert result.volume_ratio > 0

    def test_bb_squeeze_is_bool(self):
        result = compute_indicators(_make_ohlcv())
        assert isinstance(result.bb_squeeze, bool)

    def test_return_3m(self):
        result = compute_indicators(_make_ohlcv())
        assert isinstance(result.return_3m_pct, float)


class TestWeeklyTrend:
    def test_returns_valid_direction(self):
        trend = compute_weekly_trend(_make_ohlcv())
        assert trend in ("up", "down", "neutral")

    def test_short_data_returns_neutral(self):
        trend = compute_weekly_trend(_make_ohlcv(days=10))
        assert trend == "neutral"


class TestRelativeStrengthRank:
    def test_rank_top(self):
        rank, pct = compute_relative_strength_rank(20.0, [5, 10, 15, 20, 25])
        assert rank <= 2
        assert pct <= 40

    def test_rank_bottom(self):
        rank, pct = compute_relative_strength_rank(2.0, [5, 10, 15, 20, 25])
        assert rank >= 4
        assert pct >= 80

    def test_empty_list(self):
        rank, pct = compute_relative_strength_rank(10.0, [])
        assert rank == 1
        assert pct == 50.0


class TestDisparity:
    def test_disparity_around_100(self):
        result = compute_disparity(_make_ohlcv())
        assert 80 <= result <= 120

    def test_near_high_pct(self):
        result = compute_near_high_pct(_make_ohlcv(), period=60)
        assert 0 <= result <= 100
