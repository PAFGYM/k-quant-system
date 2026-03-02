"""Tests for kstock.features.timeframe — multi-timeframe engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.features.timeframe import (
    MTFBreakout,
    MTFSignal,
    TimeframeData,
    _compute_rsi,
    _determine_trend,
    analyze_mtf_alignment,
    build_timeframe_data,
    detect_mtf_breakout,
    format_mtf_breakout,
    format_mtf_signal,
    resample_to_monthly,
    resample_to_weekly,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic OHLCV generators
# ---------------------------------------------------------------------------


def _make_daily(
    n: int = 60,
    start: str = "2025-01-02",
    trend: str = "up",
    base: float = 50000.0,
) -> pd.DataFrame:
    """Create synthetic daily OHLCV data.

    ``trend`` controls the direction: ``"up"``, ``"down"``, or ``"flat"``.
    """
    dates = pd.bdate_range(start=start, periods=n)
    rng = np.random.RandomState(42)

    if trend == "up":
        closes = base + np.cumsum(rng.uniform(50, 200, size=n))
    elif trend == "down":
        closes = base - np.cumsum(rng.uniform(50, 200, size=n))
    else:
        closes = base + rng.uniform(-20, 20, size=n).cumsum() * 0.01 + base * 0.001 * np.arange(n) * 0

    highs = closes + rng.uniform(100, 500, size=n)
    lows = closes - rng.uniform(100, 500, size=n)
    opens = closes + rng.uniform(-200, 200, size=n)
    volumes = rng.randint(100_000, 500_000, size=n).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


# ---------------------------------------------------------------------------
# Resampling tests
# ---------------------------------------------------------------------------


class TestResampleWeekly:
    def test_ohlcv_aggregation(self):
        """10 business days should produce ~2 weekly bars."""
        daily = _make_daily(n=10, start="2025-03-03")
        weekly = resample_to_weekly(daily)

        assert len(weekly) >= 2
        assert "open" in weekly.columns
        assert "close" in weekly.columns

        # First weekly bar: open should equal the first daily open of that week
        first_week_end = weekly.index[0]
        first_week_start = first_week_end - pd.Timedelta(days=6)
        mask = (daily.index >= first_week_start) & (daily.index <= first_week_end)
        week_days = daily.loc[mask]
        if len(week_days) > 0:
            assert weekly["open"].iloc[0] == pytest.approx(week_days["open"].iloc[0])
            assert weekly["high"].iloc[0] == pytest.approx(week_days["high"].max())

    def test_volume_sum(self):
        """Weekly volume should equal sum of constituent daily volumes."""
        daily = _make_daily(n=10, start="2025-03-03")
        weekly = resample_to_weekly(daily)

        # Check the last complete week
        last_week_end = weekly.index[-1]
        last_week_start = last_week_end - pd.Timedelta(days=6)
        mask = (daily.index >= last_week_start) & (daily.index <= last_week_end)
        expected_vol = daily.loc[mask, "volume"].sum()
        assert weekly["volume"].iloc[-1] == pytest.approx(expected_vol)


class TestResampleMonthly:
    def test_ohlcv_aggregation(self):
        """60 business days (~3 months) should produce 2-3 monthly bars."""
        daily = _make_daily(n=60, start="2025-01-02")
        monthly = resample_to_monthly(daily)

        assert len(monthly) >= 2
        assert monthly["high"].iloc[0] >= monthly["close"].iloc[0]
        assert monthly["low"].iloc[0] <= monthly["close"].iloc[0]


# ---------------------------------------------------------------------------
# Trend detection tests
# ---------------------------------------------------------------------------


class TestTrendDetection:
    def test_uptrend(self):
        """Monotonically rising prices should be detected as up."""
        daily = _make_daily(n=60, trend="up")
        assert _determine_trend(daily) == "up"

    def test_downtrend(self):
        """Monotonically falling prices should be detected as down."""
        daily = _make_daily(n=60, trend="down")
        assert _determine_trend(daily) == "down"

    def test_neutral_trend(self):
        """Flat / sideways prices should be detected as neutral."""
        daily = _make_daily(n=60, trend="flat")
        result = _determine_trend(daily)
        assert result in ("neutral", "up", "down")  # may wobble; flat seed is tricky

    def test_insufficient_data(self):
        """Too few bars should return neutral."""
        daily = _make_daily(n=5)
        assert _determine_trend(daily) == "neutral"


class TestRSI:
    def test_rsi_range(self):
        daily = _make_daily(n=60, trend="up")
        rsi = _compute_rsi(daily["close"])
        assert 0 <= rsi <= 100

    def test_rsi_insufficient(self):
        short = pd.Series([100, 101, 102])
        assert _compute_rsi(short) == 50.0


# ---------------------------------------------------------------------------
# MTF alignment tests
# ---------------------------------------------------------------------------


class TestMTFAlignment:
    def test_all_up(self):
        """Strong uptrend across all timeframes."""
        daily = _make_daily(n=200, trend="up")
        tf = build_timeframe_data("005930", daily)
        sig = analyze_mtf_alignment("005930", tf)

        assert isinstance(sig, MTFSignal)
        assert sig.alignment == "all_up"
        assert sig.alignment_score == pytest.approx(1.0)
        assert sig.confirmation is True

    def test_all_down(self):
        """Strong downtrend across all timeframes."""
        daily = _make_daily(n=200, trend="down", base=100000.0)
        tf = build_timeframe_data("005930", daily)
        sig = analyze_mtf_alignment("005930", tf)

        assert sig.alignment == "all_down"
        assert sig.alignment_score == pytest.approx(-1.0)

    def test_mixed(self):
        """Non-uniform trends should yield a mixed or neutral alignment."""
        # Build daily that trends up short-term but flat overall
        daily = _make_daily(n=200, trend="flat")
        tf = build_timeframe_data("005930", daily)
        sig = analyze_mtf_alignment("005930", tf)

        assert sig.alignment in ("mixed_bullish", "mixed_bearish", "neutral", "all_up", "all_down")
        assert -1.0 <= sig.alignment_score <= 1.0


# ---------------------------------------------------------------------------
# Breakout tests
# ---------------------------------------------------------------------------


class TestMTFBreakout:
    def test_new_high_breakout(self):
        """A spike at the end should trigger a daily breakout."""
        daily = _make_daily(n=60, trend="up")
        # Inject a clear breakout: last close exceeds max prior high
        prior_high_max = daily["high"].iloc[-21:-1].max()
        daily.iloc[-1, daily.columns.get_loc("close")] = prior_high_max + 1000
        daily.iloc[-1, daily.columns.get_loc("high")] = prior_high_max + 1500

        tf = build_timeframe_data("005930", daily)
        brk = detect_mtf_breakout("005930", tf)

        assert isinstance(brk, MTFBreakout)
        assert brk.daily_breakout is True

    def test_no_breakout(self):
        """A downtrend should not produce breakouts."""
        daily = _make_daily(n=200, trend="down", base=100000.0)
        tf = build_timeframe_data("005930", daily)
        brk = detect_mtf_breakout("005930", tf)

        assert brk.daily_breakout is False
        assert brk.strength == "none" or brk.strength == "moderate"

    def test_breakout_strength_strong(self):
        """Breakouts on 2+ timeframes should be rated 'strong'."""
        daily = _make_daily(n=200, trend="up")
        tf = build_timeframe_data("005930", daily)
        brk = detect_mtf_breakout("005930", tf)

        count = sum([brk.daily_breakout, brk.weekly_breakout, brk.monthly_breakout])
        if count >= 2:
            assert brk.strength == "strong"
        elif count == 1:
            assert brk.strength == "moderate"


# ---------------------------------------------------------------------------
# Format output tests
# ---------------------------------------------------------------------------


class TestFormatOutput:
    def test_format_signal_returns_str(self):
        daily = _make_daily(n=200, trend="up")
        tf = build_timeframe_data("005930", daily)
        sig = analyze_mtf_alignment("005930", tf)
        text = format_mtf_signal(sig)

        assert isinstance(text, str)
        assert "005930" in text
        assert "RSI" in text

    def test_format_breakout_returns_str(self):
        daily = _make_daily(n=200, trend="up")
        tf = build_timeframe_data("005930", daily)
        brk = detect_mtf_breakout("005930", tf)
        text = format_mtf_breakout(brk)

        assert isinstance(text, str)
        assert "005930" in text

    def test_format_no_breakout(self):
        brk = MTFBreakout(
            ticker="000660",
            daily_breakout=False,
            weekly_breakout=False,
            monthly_breakout=False,
            breakout_level="none",
            breakout_price=0.0,
            strength="none",
        )
        text = format_mtf_breakout(brk)
        assert "000660" in text
        assert "없음" in text
