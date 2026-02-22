"""Tests for the market psychology indicators (Fear & Greed)."""

from __future__ import annotations

import pytest

from kstock.signal.market_psychology import (
    FearGreedIndex,
    compute_fear_greed,
    detect_retail_contrarian,
    format_psychology_summary,
    get_psychology_score_adj,
)


# ---------------------------------------------------------------------------
# FearGreedIndex dataclass
# ---------------------------------------------------------------------------


class TestFearGreedIndexDataclass:
    """Tests for the FearGreedIndex dataclass."""

    def test_dataclass_instantiation(self) -> None:
        fg = FearGreedIndex(
            vix_score=25.0,
            kospi_20d_score=25.0,
            volume_score=25.0,
            foreign_score=25.0,
            total=100.0,
            label="극단탐욕",
        )
        assert fg.total == 100.0
        assert fg.label == "극단탐욕"

    def test_dataclass_fields_are_accessible(self) -> None:
        fg = FearGreedIndex(
            vix_score=0.0,
            kospi_20d_score=0.0,
            volume_score=0.0,
            foreign_score=0.0,
            total=0.0,
            label="극단공포",
        )
        assert fg.vix_score == 0.0
        assert fg.label == "극단공포"


# ---------------------------------------------------------------------------
# compute_fear_greed
# ---------------------------------------------------------------------------


class TestComputeFearGreed:
    """Tests for compute_fear_greed."""

    def test_low_vix_positive_return_high_vol_foreign_buy_is_greed(self) -> None:
        """Low VIX + positive return + high volume + foreign buy -> 탐욕."""
        fg = compute_fear_greed(
            vix=12.0,
            kospi_20d_return_pct=6.0,
            volume_ratio=1.6,
            foreign_net_days=5,
        )
        assert fg.label in ("탐욕", "극단탐욕")
        assert fg.total >= 60

    def test_all_max_signals_extreme_greed(self) -> None:
        fg = compute_fear_greed(
            vix=10.0,
            kospi_20d_return_pct=10.0,
            volume_ratio=2.0,
            foreign_net_days=7,
        )
        assert fg.total == 100.0
        assert fg.label == "극단탐욕"

    def test_high_vix_negative_return_low_vol_foreign_sell_is_fear(self) -> None:
        """High VIX + negative return + low volume + foreign sell -> 공포."""
        fg = compute_fear_greed(
            vix=35.0,
            kospi_20d_return_pct=-5.0,
            volume_ratio=0.3,
            foreign_net_days=-5,
        )
        assert fg.label in ("극단공포", "공포")
        assert fg.total < 40

    def test_all_min_signals_extreme_fear(self) -> None:
        fg = compute_fear_greed(
            vix=40.0,
            kospi_20d_return_pct=-10.0,
            volume_ratio=0.2,
            foreign_net_days=-10,
        )
        assert fg.total == 0.0
        assert fg.label == "극단공포"

    def test_neutral_inputs_neutral_label(self) -> None:
        """Neutral inputs -> 중립."""
        fg = compute_fear_greed(
            vix=22.0,
            kospi_20d_return_pct=1.0,
            volume_ratio=1.0,
            foreign_net_days=1,
        )
        assert fg.label == "중립"

    def test_score_bounds_0_to_100(self) -> None:
        """Score should always be between 0 and 100."""
        for vix in [5, 15, 25, 35, 45]:
            for ret in [-10, -2, 1, 4, 8]:
                for vol in [0.1, 0.6, 1.0, 1.3, 2.0]:
                    for days in [-7, -3, 1, 3, 7]:
                        fg = compute_fear_greed(vix, ret, vol, days)
                        assert 0 <= fg.total <= 100, (
                            f"Score {fg.total} out of bounds for "
                            f"vix={vix}, ret={ret}, vol={vol}, days={days}"
                        )

    def test_label_mapping_extreme_fear(self) -> None:
        fg = compute_fear_greed(vix=40.0, kospi_20d_return_pct=-10.0,
                                volume_ratio=0.1, foreign_net_days=-10)
        assert fg.label == "극단공포"

    def test_label_mapping_fear(self) -> None:
        fg = compute_fear_greed(vix=28.0, kospi_20d_return_pct=-1.0,
                                volume_ratio=0.6, foreign_net_days=-2)
        assert fg.label == "공포"

    def test_label_mapping_neutral(self) -> None:
        fg = compute_fear_greed(vix=22.0, kospi_20d_return_pct=1.0,
                                volume_ratio=1.0, foreign_net_days=1)
        assert fg.label == "중립"

    def test_label_mapping_greed(self) -> None:
        fg = compute_fear_greed(vix=12.0, kospi_20d_return_pct=3.0,
                                volume_ratio=1.3, foreign_net_days=4)
        assert fg.label == "탐욕"

    def test_label_mapping_extreme_greed(self) -> None:
        fg = compute_fear_greed(vix=10.0, kospi_20d_return_pct=7.0,
                                volume_ratio=2.0, foreign_net_days=6)
        assert fg.label == "극단탐욕"

    def test_component_scores_sum_to_total(self) -> None:
        fg = compute_fear_greed(vix=18.0, kospi_20d_return_pct=3.0,
                                volume_ratio=1.1, foreign_net_days=2)
        expected_total = fg.vix_score + fg.kospi_20d_score + fg.volume_score + fg.foreign_score
        assert fg.total == pytest.approx(expected_total)


# ---------------------------------------------------------------------------
# detect_retail_contrarian
# ---------------------------------------------------------------------------


class TestDetectRetailContrarian:
    """Tests for detect_retail_contrarian."""

    def test_retail_buy_foreign_sell_peak_warning(self) -> None:
        """Retail buy + foreign sell -> 고점 경고."""
        result = detect_retail_contrarian(
            retail_net_buy_krw=1_000_000_000,
            foreign_net_buy_krw=-500_000_000,
        )
        assert result["signal"] == "고점 경고"
        assert result["score_adj"] == -5

    def test_retail_sell_foreign_buy_bottom_signal(self) -> None:
        """Retail sell + foreign buy -> 저점 시그널."""
        result = detect_retail_contrarian(
            retail_net_buy_krw=-1_000_000_000,
            foreign_net_buy_krw=500_000_000,
        )
        assert result["signal"] == "저점 시그널"
        assert result["score_adj"] == 5

    def test_both_buying_returns_empty(self) -> None:
        result = detect_retail_contrarian(
            retail_net_buy_krw=100,
            foreign_net_buy_krw=100,
        )
        assert result == {}

    def test_both_selling_returns_empty(self) -> None:
        result = detect_retail_contrarian(
            retail_net_buy_krw=-100,
            foreign_net_buy_krw=-100,
        )
        assert result == {}


# ---------------------------------------------------------------------------
# get_psychology_score_adj
# ---------------------------------------------------------------------------


class TestGetPsychologyScoreAdj:
    """Tests for get_psychology_score_adj."""

    def test_extreme_fear_plus_10(self) -> None:
        fg = FearGreedIndex(0, 0, 0, 0, 10, "극단공포")
        assert get_psychology_score_adj(fg) == 10

    def test_fear_plus_5(self) -> None:
        fg = FearGreedIndex(6, 6, 6, 6, 24, "공포")
        assert get_psychology_score_adj(fg) == 5

    def test_neutral_zero(self) -> None:
        fg = FearGreedIndex(12, 12, 12, 12, 48, "중립")
        assert get_psychology_score_adj(fg) == 0

    def test_greed_minus_5(self) -> None:
        fg = FearGreedIndex(18, 18, 18, 18, 72, "탐욕")
        assert get_psychology_score_adj(fg) == -5

    def test_extreme_greed_minus_10(self) -> None:
        fg = FearGreedIndex(25, 25, 25, 25, 100, "극단탐욕")
        assert get_psychology_score_adj(fg) == -10


# ---------------------------------------------------------------------------
# format_psychology_summary
# ---------------------------------------------------------------------------


class TestFormatPsychologySummary:
    """Tests for format_psychology_summary."""

    def test_returns_string(self) -> None:
        fg = compute_fear_greed(20.0, 1.0, 1.0, 1)
        result = format_psychology_summary(fg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_score_info(self) -> None:
        fg = compute_fear_greed(20.0, 1.0, 1.0, 1)
        result = format_psychology_summary(fg)
        assert "/25" in result
        assert "/100" in result

    def test_includes_retail_signal_when_provided(self) -> None:
        fg = compute_fear_greed(20.0, 1.0, 1.0, 1)
        retail = {"signal": "고점 경고", "score_adj": -5}
        result = format_psychology_summary(fg, retail_signal=retail)
        assert "고점 경고" in result
        assert "-5" in result

    def test_no_retail_signal_no_crash(self) -> None:
        fg = compute_fear_greed(20.0, 1.0, 1.0, 1)
        result = format_psychology_summary(fg, retail_signal=None)
        assert isinstance(result, str)
