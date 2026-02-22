"""Tests for the sentiment analysis module."""

from __future__ import annotations

import pytest

from kstock.ml.sentiment import (
    SentimentResult,
    _build_sentiment_prompt,
    format_sentiment_summary,
    get_sentiment_bonus,
)


# ---------------------------------------------------------------------------
# SentimentResult dataclass
# ---------------------------------------------------------------------------


class TestSentimentResult:
    """Tests for SentimentResult dataclass defaults and construction."""

    def test_defaults(self) -> None:
        """All fields should have sensible zero/empty defaults."""
        result = SentimentResult()
        assert result.positive_pct == 0.0
        assert result.negative_pct == 0.0
        assert result.neutral_pct == 0.0
        assert result.summary == ""
        assert result.headline_count == 0

    def test_custom_values(self) -> None:
        """SentimentResult should store custom values correctly."""
        result = SentimentResult(
            positive_pct=60.0,
            negative_pct=20.0,
            neutral_pct=20.0,
            summary="긍정적 뉴스 다수",
            headline_count=10,
        )
        assert result.positive_pct == 60.0
        assert result.negative_pct == 20.0
        assert result.neutral_pct == 20.0
        assert result.summary == "긍정적 뉴스 다수"
        assert result.headline_count == 10

    def test_partial_custom_values(self) -> None:
        """Providing only some fields should leave others at defaults."""
        result = SentimentResult(positive_pct=75.0, headline_count=5)
        assert result.positive_pct == 75.0
        assert result.negative_pct == 0.0
        assert result.neutral_pct == 0.0
        assert result.summary == ""
        assert result.headline_count == 5


# ---------------------------------------------------------------------------
# get_sentiment_bonus
# ---------------------------------------------------------------------------


class TestGetSentimentBonus:
    """Tests for the score bonus/penalty computation."""

    def test_strong_positive_returns_10(self) -> None:
        """positive_pct >= 70 should yield +10."""
        result = SentimentResult(positive_pct=80.0, headline_count=5)
        assert get_sentiment_bonus(result) == 10

    def test_moderate_positive_returns_5(self) -> None:
        """positive_pct in [50, 70) should yield +5."""
        result = SentimentResult(positive_pct=55.0, headline_count=5)
        assert get_sentiment_bonus(result) == 5

    def test_strong_negative_returns_minus_10(self) -> None:
        """negative_pct >= 50 should yield -10."""
        result = SentimentResult(negative_pct=60.0, headline_count=5)
        assert get_sentiment_bonus(result) == -10

    def test_insufficient_headlines_returns_0(self) -> None:
        """headline_count < 3 should always return 0, regardless of pcts."""
        result = SentimentResult(positive_pct=90.0, headline_count=2)
        assert get_sentiment_bonus(result) == 0

    def test_zero_headlines_returns_0(self) -> None:
        """Zero headlines should return 0."""
        result = SentimentResult(positive_pct=90.0, headline_count=0)
        assert get_sentiment_bonus(result) == 0

    def test_balanced_sentiment_returns_0(self) -> None:
        """Balanced sentiment (no rule triggered) should return 0."""
        result = SentimentResult(
            positive_pct=30.0,
            negative_pct=30.0,
            neutral_pct=40.0,
            headline_count=10,
        )
        assert get_sentiment_bonus(result) == 0

    def test_boundary_positive_70_returns_10(self) -> None:
        """Exactly 70% positive should trigger the +10 bonus."""
        result = SentimentResult(positive_pct=70.0, headline_count=5)
        assert get_sentiment_bonus(result) == 10

    def test_boundary_positive_50_returns_5(self) -> None:
        """Exactly 50% positive should trigger the +5 bonus."""
        result = SentimentResult(positive_pct=50.0, headline_count=5)
        assert get_sentiment_bonus(result) == 5

    def test_boundary_negative_50_returns_minus_10(self) -> None:
        """Exactly 50% negative (with low positive) should trigger -10."""
        result = SentimentResult(
            positive_pct=10.0,
            negative_pct=50.0,
            headline_count=5,
        )
        assert get_sentiment_bonus(result) == -10

    def test_positive_takes_priority_over_negative(self) -> None:
        """When positive >= 50 and negative >= 50, positive rule fires first."""
        result = SentimentResult(
            positive_pct=55.0,
            negative_pct=55.0,
            headline_count=5,
        )
        # positive_pct >= 50 is checked before negative_pct >= 50
        assert get_sentiment_bonus(result) == 5

    def test_boundary_headline_count_3_is_sufficient(self) -> None:
        """Exactly 3 headlines should be enough to compute a bonus."""
        result = SentimentResult(positive_pct=75.0, headline_count=3)
        assert get_sentiment_bonus(result) == 10

    def test_just_below_positive_50_returns_0_or_negative(self) -> None:
        """positive_pct=49.9 should not trigger the +5 bonus."""
        result = SentimentResult(
            positive_pct=49.9,
            negative_pct=20.0,
            headline_count=5,
        )
        assert get_sentiment_bonus(result) == 0


# ---------------------------------------------------------------------------
# format_sentiment_summary
# ---------------------------------------------------------------------------


class TestFormatSentimentSummary:
    """Tests for the Telegram message formatter."""

    def test_empty_results(self) -> None:
        """Empty dict should produce a 'no results' message."""
        output = format_sentiment_summary({})
        assert "결과가 없습니다" in output

    def test_positive_stocks_appear(self) -> None:
        """Stocks with high positive_pct should appear in the positive section."""
        results = {
            "005930": SentimentResult(
                positive_pct=80.0,
                negative_pct=10.0,
                neutral_pct=10.0,
                summary="실적 호조",
                headline_count=8,
            ),
        }
        output = format_sentiment_summary(results)
        assert "005930" in output
        assert "80%" in output
        assert "실적 호조" in output

    def test_negative_stocks_appear(self) -> None:
        """Stocks with high negative_pct should appear in the negative section."""
        results = {
            "000660": SentimentResult(
                positive_pct=10.0,
                negative_pct=60.0,
                neutral_pct=30.0,
                summary="실적 부진 우려",
                headline_count=6,
            ),
        }
        output = format_sentiment_summary(results)
        assert "000660" in output
        assert "60%" in output
        assert "실적 부진 우려" in output

    def test_mixed_results(self) -> None:
        """Mixed positive and negative stocks should both appear."""
        results = {
            "005930": SentimentResult(
                positive_pct=75.0,
                negative_pct=10.0,
                neutral_pct=15.0,
                summary="긍정적",
                headline_count=5,
            ),
            "000660": SentimentResult(
                positive_pct=10.0,
                negative_pct=55.0,
                neutral_pct=35.0,
                summary="부정적",
                headline_count=5,
            ),
        }
        output = format_sentiment_summary(results)
        assert "005930" in output
        assert "000660" in output
        assert "긍정적" in output
        assert "부정적" in output

    def test_includes_timestamp(self) -> None:
        """Output should contain a KST timestamp line."""
        results = {
            "005930": SentimentResult(
                positive_pct=50.0,
                headline_count=3,
                summary="test",
            ),
        }
        output = format_sentiment_summary(results)
        assert "KST" in output

    def test_includes_powered_by(self) -> None:
        """Output should include the 'Powered by Claude' footer."""
        results = {
            "005930": SentimentResult(
                positive_pct=50.0,
                headline_count=3,
                summary="test",
            ),
        }
        output = format_sentiment_summary(results)
        assert "Powered by Claude" in output

    def test_neutral_stock_shows_no_match(self) -> None:
        """A stock with low positive and low negative shows (해당 없음)."""
        results = {
            "005930": SentimentResult(
                positive_pct=20.0,
                negative_pct=10.0,
                neutral_pct=70.0,
                summary="neutral",
                headline_count=5,
            ),
        }
        output = format_sentiment_summary(results)
        # Both positive (<40%) and negative (<30%) thresholds not met
        assert "해당 없음" in output


# ---------------------------------------------------------------------------
# _build_sentiment_prompt
# ---------------------------------------------------------------------------


class TestBuildSentimentPrompt:
    """Tests for the Claude prompt builder."""

    def test_includes_all_tickers(self) -> None:
        """All ticker keys should appear in the prompt."""
        headlines = {
            "005930": ["삼성전자 실적 호조"],
            "000660": ["SK하이닉스 투자 확대"],
        }
        prompt = _build_sentiment_prompt(headlines)
        assert "[005930]" in prompt
        assert "[000660]" in prompt

    def test_includes_headline_text(self) -> None:
        """Individual headline strings should appear in the prompt."""
        headlines = {
            "005930": ["삼성전자 실적 호조", "반도체 수출 증가"],
        }
        prompt = _build_sentiment_prompt(headlines)
        assert "삼성전자 실적 호조" in prompt
        assert "반도체 수출 증가" in prompt

    def test_empty_headlines_shows_no_news(self) -> None:
        """A ticker with empty headline list should show the no-news marker."""
        headlines = {"005930": []}
        prompt = _build_sentiment_prompt(headlines)
        assert "[005930]" in prompt
        assert "뉴스 없음" in prompt

    def test_empty_dict_produces_header_only(self) -> None:
        """An empty dict should still produce the instruction header."""
        prompt = _build_sentiment_prompt({})
        assert "감성을 분류" in prompt
        # No ticker sections
        assert "[" not in prompt.split("---")[-1].strip() or prompt.split("---")[-1].strip() == ""

    def test_headlines_are_numbered(self) -> None:
        """Headlines should be numbered starting from 1."""
        headlines = {
            "005930": ["첫째 뉴스", "둘째 뉴스", "셋째 뉴스"],
        }
        prompt = _build_sentiment_prompt(headlines)
        assert "1. 첫째 뉴스" in prompt
        assert "2. 둘째 뉴스" in prompt
        assert "3. 셋째 뉴스" in prompt

    def test_prompt_requests_json_format(self) -> None:
        """Prompt should instruct Claude to reply in JSON."""
        prompt = _build_sentiment_prompt({"X": ["h"]})
        assert "JSON" in prompt
