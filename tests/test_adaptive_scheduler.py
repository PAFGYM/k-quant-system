"""적응형 모니터링 — VIX 레짐 분류 테스트."""
from __future__ import annotations

import pytest
from kstock.bot.mixins.scheduler import (
    ADAPTIVE_INTERVALS,
    _get_vix_regime,
)


class TestGetVixRegime:
    """_get_vix_regime VIX→레짐 매핑."""

    def test_calm(self):
        assert _get_vix_regime(12.0) == "calm"
        assert _get_vix_regime(17.9) == "calm"

    def test_normal(self):
        assert _get_vix_regime(18.0) == "normal"
        assert _get_vix_regime(24.9) == "normal"

    def test_fear(self):
        assert _get_vix_regime(25.0) == "fear"
        assert _get_vix_regime(29.9) == "fear"

    def test_panic(self):
        assert _get_vix_regime(30.0) == "panic"
        assert _get_vix_regime(80.0) == "panic"


class TestAdaptiveIntervals:
    """ADAPTIVE_INTERVALS 상수 검증."""

    def test_all_regimes_present(self):
        assert set(ADAPTIVE_INTERVALS) == {"calm", "normal", "fear", "panic"}

    def test_intervals_decrease_with_severity(self):
        """VIX가 높을수록 간격이 짧아져야 함."""
        regimes = ["calm", "normal", "fear", "panic"]
        for job in ("intraday_monitor", "market_pulse"):
            prev = float("inf")
            for regime in regimes:
                current = ADAPTIVE_INTERVALS[regime][job]
                assert current <= prev, f"{job}: {regime} interval should be <= {prev}"
                prev = current

    def test_panic_is_fastest(self):
        assert ADAPTIVE_INTERVALS["panic"]["intraday_monitor"] == 15
        assert ADAPTIVE_INTERVALS["panic"]["market_pulse"] == 15

    def test_normal_is_baseline(self):
        assert ADAPTIVE_INTERVALS["normal"]["intraday_monitor"] == 60
        assert ADAPTIVE_INTERVALS["normal"]["market_pulse"] == 60

    def test_calm_intervals(self):
        assert ADAPTIVE_INTERVALS["calm"]["intraday_monitor"] == 120
        assert ADAPTIVE_INTERVALS["calm"]["market_pulse"] == 180

    def test_fear_intervals(self):
        assert ADAPTIVE_INTERVALS["fear"]["intraday_monitor"] == 30
        assert ADAPTIVE_INTERVALS["fear"]["market_pulse"] == 30
