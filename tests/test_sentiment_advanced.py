"""Tests for the advanced sentiment analysis module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.ml.sentiment_advanced import (
    ContrarianSignal,
    NewsImpact,
    SentimentRegime,
    SentimentReport,
    SentimentStrength,
    classify_sentiment_regime,
    compute_news_decay,
    compute_news_impact,
    compute_sentiment_strength,
    detect_contrarian_signal,
    format_sentiment_report,
    generate_sentiment_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_price_df(
    prices: list[float],
    start: str = "2026-01-01 09:00",
    freq: str = "h",
) -> pd.DataFrame:
    """Create a simple price DataFrame with a DatetimeIndex."""
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="Asia/Seoul")
    return pd.DataFrame({"close": prices}, index=idx)


# ---------------------------------------------------------------------------
# TestNewsImpact
# ---------------------------------------------------------------------------


class TestNewsImpact:
    """Tests for compute_news_impact."""

    def test_positive_news_with_price_rise(self) -> None:
        """Positive sentiment + price increase => high impact."""
        prices = [100.0] * 3 + [105.0] * 5  # jumps at index 3
        pdf = _make_price_df(prices, freq="h")
        headlines = [
            {
                "headline": "실적 호조 기대감",
                "published_at": "2026-01-01 09:00",
                "sentiment": 0.8,
            },
        ]
        impacts = compute_news_impact(headlines, pdf)
        assert len(impacts) == 1
        assert impacts[0].impact_score > 0
        assert impacts[0].price_reaction_pct > 0
        assert impacts[0].category == "earnings"

    def test_empty_headlines_returns_empty(self) -> None:
        """No headlines => empty list."""
        pdf = _make_price_df([100.0, 101.0])
        assert compute_news_impact([], pdf) == []

    def test_empty_price_data_returns_empty(self) -> None:
        """Empty price data => empty list."""
        headlines = [{"headline": "test", "published_at": "2026-01-01 09:00", "sentiment": 0.5}]
        empty_df = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([], tz="Asia/Seoul"))
        assert compute_news_impact(headlines, empty_df) == []

    def test_bad_date_still_returns_result(self) -> None:
        """Unparseable published_at still produces a NewsImpact entry."""
        pdf = _make_price_df([100.0, 101.0])
        headlines = [{"headline": "no date", "published_at": "not-a-date", "sentiment": 0.3}]
        impacts = compute_news_impact(headlines, pdf)
        assert len(impacts) == 1
        assert impacts[0].impact_score == 0.0  # no price linkage possible

    def test_category_macro(self) -> None:
        """Headline with macro keywords classified as macro."""
        pdf = _make_price_df([100.0, 100.5])
        headlines = [{"headline": "금리 인상 전망", "published_at": "2026-01-01 09:00", "sentiment": -0.3}]
        impacts = compute_news_impact(headlines, pdf)
        assert impacts[0].category == "macro"


# ---------------------------------------------------------------------------
# TestSentimentStrength
# ---------------------------------------------------------------------------


class TestSentimentStrength:
    """Tests for compute_sentiment_strength."""

    def test_all_positive(self) -> None:
        """Uniformly positive sentiments => high score and high confidence."""
        result = compute_sentiment_strength([0.8, 0.9, 0.7, 0.85])
        assert result.raw_score > 0.7
        assert result.confidence > 0.8
        assert result.agreement_ratio == 1.0

    def test_mixed_sentiments_low_confidence(self) -> None:
        """Conflicting sentiments => lower confidence."""
        result = compute_sentiment_strength([0.9, -0.8, 0.7, -0.6, 0.5])
        assert result.confidence < 0.5

    def test_empty_sentiments(self) -> None:
        """Empty input => zero SentimentStrength."""
        result = compute_sentiment_strength([])
        assert result.raw_score == 0.0
        assert result.magnitude == 0.0

    def test_volume_weighting(self) -> None:
        """Volume weighting should shift score toward high-volume items."""
        sentiments = [0.8, -0.3]
        volumes = [1000, 100]  # first item dominates
        result = compute_sentiment_strength(sentiments, volumes=volumes)
        assert result.weighted_score > 0  # dominated by the positive

    def test_contrarian_flag_extreme(self) -> None:
        """Extreme average triggers contrarian_signal."""
        result = compute_sentiment_strength([0.9, 0.95, 0.85, 0.9])
        assert result.contrarian_signal is True

    def test_contrarian_flag_moderate(self) -> None:
        """Moderate average does not trigger contrarian_signal."""
        result = compute_sentiment_strength([0.3, 0.4, 0.2, 0.35])
        assert result.contrarian_signal is False


# ---------------------------------------------------------------------------
# TestContrarian
# ---------------------------------------------------------------------------


class TestContrarian:
    """Tests for detect_contrarian_signal."""

    def test_extreme_bullish_price_down(self) -> None:
        """Extreme bullish sentiment + falling price => signal detected."""
        # 25 moderate baseline + 5 extreme bullish => z > 2 with lookback=30
        sent = [0.05] * 25 + [0.95, 0.92, 0.98, 0.90, 0.96]
        # Price returns negative in last 5 days
        rets = [0.001] * 25 + [-0.02, -0.015, -0.01, -0.025, -0.03]
        signal = detect_contrarian_signal(sent, rets, lookback=30)
        assert signal is not None
        assert signal.sentiment_direction == "bullish"
        assert signal.price_direction == "down"
        assert signal.signal_strength > 0

    def test_normal_conditions_none(self) -> None:
        """No extreme sentiment => no signal."""
        sent = [0.1, 0.2, 0.15, 0.1, 0.05] * 4
        rets = [0.001, 0.002, -0.001, 0.0, 0.001] * 4
        signal = detect_contrarian_signal(sent, rets, lookback=20)
        assert signal is None

    def test_insufficient_data_none(self) -> None:
        """Not enough data => None."""
        assert detect_contrarian_signal([0.5] * 5, [0.01] * 5, lookback=20) is None


# ---------------------------------------------------------------------------
# TestSentimentRegime
# ---------------------------------------------------------------------------


class TestSentimentRegime:
    """Tests for classify_sentiment_regime."""

    def test_euphoria(self) -> None:
        """High consistent sentiment => euphoria."""
        history = [0.8] * 30
        regime = classify_sentiment_regime(history, lookback=30)
        assert regime.regime == "euphoria"
        assert regime.mean_reversion_prob > 0.5

    def test_panic(self) -> None:
        """Very low, dispersed sentiment => panic."""
        # avg < -0.7 AND std > 0.2 required for panic
        history = [-0.95, -0.4, -0.99, -0.5, -0.85, -1.0] * 5
        regime = classify_sentiment_regime(history, lookback=30)
        assert regime.regime == "panic"

    def test_neutral(self) -> None:
        """Moderate sentiment => neutral."""
        history = [0.1, -0.05, 0.0, 0.15, -0.1] * 6
        regime = classify_sentiment_regime(history, lookback=30)
        assert regime.regime == "neutral"

    def test_empty_history(self) -> None:
        """Empty => default neutral regime."""
        regime = classify_sentiment_regime([])
        assert regime.regime == "neutral"
        assert regime.duration_days == 0

    def test_optimism(self) -> None:
        """Moderately positive => optimism."""
        history = [0.45, 0.5, 0.4, 0.55, 0.35] * 6
        regime = classify_sentiment_regime(history, lookback=30)
        assert regime.regime == "optimism"

    def test_fear(self) -> None:
        """Moderately negative => fear."""
        history = [-0.4, -0.5, -0.35, -0.55, -0.45] * 6
        regime = classify_sentiment_regime(history, lookback=30)
        assert regime.regime == "fear"


# ---------------------------------------------------------------------------
# TestNewsDecay
# ---------------------------------------------------------------------------


class TestNewsDecay:
    """Tests for compute_news_decay."""

    def test_decaying_impact(self) -> None:
        """Impact decreasing over time => positive residual less than initial."""
        impacts = [10.0, 7.0, 5.0, 3.5, 2.0]
        hours = [0.0, 6.0, 12.0, 24.0, 48.0]
        residual = compute_news_decay(impacts, hours)
        assert 0 < residual < 10.0

    def test_empty_returns_zero(self) -> None:
        """Empty input => 0.0."""
        assert compute_news_decay([], []) == 0.0

    def test_mismatched_lengths(self) -> None:
        """Mismatched input lengths => 0.0."""
        assert compute_news_decay([1.0, 2.0], [1.0]) == 0.0

    def test_residual_decreases_with_more_time(self) -> None:
        """Later observations should yield smaller residual."""
        impacts_early = [10.0, 8.0, 6.0]
        hours_early = [0.0, 2.0, 4.0]
        impacts_late = [10.0, 8.0, 6.0, 3.0, 1.0]
        hours_late = [0.0, 2.0, 4.0, 12.0, 24.0]
        r_early = compute_news_decay(impacts_early, hours_early)
        r_late = compute_news_decay(impacts_late, hours_late)
        assert r_late < r_early


# ---------------------------------------------------------------------------
# TestGenerateReport
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """Tests for generate_sentiment_report."""

    def test_basic_report(self) -> None:
        """Should produce a valid SentimentReport."""
        prices = list(range(100, 130))
        pdf = _make_price_df([float(p) for p in prices], freq="D")
        headlines = [
            {"headline": "실적 서프라이즈", "published_at": "2026-01-05 10:00", "sentiment": 0.7},
            {"headline": "금리 인하 기대", "published_at": "2026-01-06 10:00", "sentiment": 0.5},
        ]
        sent_hist = [0.3] * 25 + [0.5] * 5
        report = generate_sentiment_report("005930", headlines, pdf, sent_hist)

        assert isinstance(report, SentimentReport)
        assert report.ticker == "005930"
        assert isinstance(report.current_sentiment, SentimentStrength)
        assert isinstance(report.regime, SentimentRegime)
        assert -1.0 <= report.composite_signal <= 1.0


# ---------------------------------------------------------------------------
# TestFormat
# ---------------------------------------------------------------------------


class TestFormat:
    """Tests for format_sentiment_report."""

    def test_returns_string(self) -> None:
        """Should return a non-empty string."""
        report = SentimentReport(
            ticker="005930",
            current_sentiment=SentimentStrength(
                ticker="005930", raw_score=0.4, magnitude=0.5,
                confidence=0.8, agreement_ratio=0.75,
            ),
            regime=SentimentRegime(regime="optimism", score=0.45,
                                   duration_days=5, mean_reversion_prob=0.4,
                                   historical_return_after=0.005),
            composite_signal=0.35,
        )
        text = format_sentiment_report(report)
        assert isinstance(text, str)
        assert len(text) > 50

    def test_contains_key_info(self) -> None:
        """Output contains ticker, regime, composite signal."""
        report = SentimentReport(
            ticker="000660",
            current_sentiment=SentimentStrength(ticker="000660", raw_score=-0.2),
            regime=SentimentRegime(regime="fear", score=-0.4, duration_days=3),
            composite_signal=-0.15,
        )
        text = format_sentiment_report(report)
        assert "000660" in text
        assert "FEAR" in text
        assert "-0.15" in text

    def test_contrarian_section(self) -> None:
        """When contrarian signal present, its section appears."""
        report = SentimentReport(
            ticker="035720",
            current_sentiment=SentimentStrength(ticker="035720"),
            contrarian=ContrarianSignal(
                ticker="035720", sentiment_direction="bullish",
                price_direction="down", divergence_days=3,
                signal_strength=0.6, historical_accuracy=0.65,
            ),
            regime=SentimentRegime(),
            composite_signal=0.1,
        )
        text = format_sentiment_report(report)
        assert "bullish" in text
        assert "down" in text

    def test_no_parse_mode_markers(self) -> None:
        """Plain text: no HTML/Markdown bold markers."""
        report = SentimentReport(
            ticker="005930",
            current_sentiment=SentimentStrength(ticker="005930"),
            regime=SentimentRegime(),
            composite_signal=0.0,
        )
        text = format_sentiment_report(report)
        assert "**" not in text
        assert "<b>" not in text
