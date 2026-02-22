"""Tests for factor scoring (ROE + Investment)."""

from __future__ import annotations

import pytest

from kstock.signal.factor_scoring import compute_factor_score


class TestFactorScoring:
    def test_high_roe_low_growth(self):
        result = compute_factor_score(
            roe=20.0, per=10, pbr=0.8,
            debt_ratio=50, asset_growth_pct=3.0,
        )
        assert result.roe_factor == 15.0
        assert result.investment_factor == 5.0
        assert result.per_pbr_factor == 10.0
        assert result.total == 30.0

    def test_low_roe(self):
        result = compute_factor_score(roe=3.0, per=25, pbr=2.5, asset_growth_pct=20)
        assert result.roe_factor == 3.0
        assert result.investment_factor == 1.0

    def test_with_percentile(self):
        all_roe = [2, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        result = compute_factor_score(
            roe=25.0, per=12, pbr=1.5,
            asset_growth_pct=10,
            all_roe_values=all_roe,
        )
        assert result.roe_factor == 15.0  # top 30%

    def test_mid_roe_percentile(self):
        all_roe = [2, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        result = compute_factor_score(
            roe=12.0, per=15, pbr=1.0,
            all_roe_values=all_roe,
        )
        assert result.roe_factor == 10.0  # middle 40%

    def test_total_range(self):
        result = compute_factor_score(roe=0, per=0, pbr=0, asset_growth_pct=0)
        assert 0 <= result.total <= 30

    def test_moderate_values(self):
        result = compute_factor_score(
            roe=10.0, per=20, pbr=1.5,
            asset_growth_pct=10,
        )
        assert result.roe_factor == 12.0
        assert result.investment_factor == 3.0
        assert 15 <= result.total <= 25
