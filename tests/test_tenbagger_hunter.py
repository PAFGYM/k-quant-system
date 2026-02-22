"""Tests for tenbagger candidate scanner (Section 42)."""

from __future__ import annotations

import pytest

from kstock.signal.tenbagger_hunter import (
    TENBAGGER_THEMES,
    TenbaggerCandidate,
    _compute_bonus,
    _evaluate_conditions,
    format_tenbagger_alert,
    scan_tenbagger,
)


# ---------------------------------------------------------------------------
# Helpers: common kwargs for scan_tenbagger with all 5 conditions met
# ---------------------------------------------------------------------------

def _all_met_kwargs() -> dict:
    """Return keyword arguments that satisfy all 5 tenbagger conditions."""
    return {
        "ticker": "373220",
        "name": "LG에너지솔루션",
        "current_price": 200_000,
        "high_52w": 500_000,       # drop = -60%
        "market_cap": 5_000_000_000_000,  # 5조
        "market": "KOSPI",
        "sector": "2차전지",
        "revenue_growth_pct": 25.0,
        "foreign_buy_days_in_10": 8,
        "volume_ratio_20d": 2.0,
        "policy_support": True,
    }


def _four_met_kwargs() -> dict:
    """Return kwargs with 4/5 conditions met (foreign days = 5, insufficient)."""
    kw = _all_met_kwargs()
    kw["foreign_buy_days_in_10"] = 5  # fails condition 4
    return kw


def _three_met_kwargs() -> dict:
    """Return kwargs with only 3/5 conditions met."""
    kw = _all_met_kwargs()
    kw["foreign_buy_days_in_10"] = 5   # fail
    kw["volume_ratio_20d"] = 1.0       # fail
    return kw


# ===========================================================================
# TenbaggerCandidate dataclass
# ===========================================================================

class TestTenbaggerCandidateDataclass:
    """TenbaggerCandidate dataclass structure and defaults."""

    def test_required_fields(self) -> None:
        c = TenbaggerCandidate(
            ticker="005930",
            name="삼성전자",
            current_price=55_000,
            high_52w=80_000,
            drop_from_high_pct=-31.3,
            conditions_met=3,
            conditions_total=5,
        )
        assert c.ticker == "005930"
        assert c.name == "삼성전자"
        assert c.conditions_met == 3
        assert c.conditions_total == 5

    def test_default_values(self) -> None:
        c = TenbaggerCandidate(
            ticker="T",
            name="N",
            current_price=100,
            high_52w=200,
            drop_from_high_pct=-50.0,
            conditions_met=5,
            conditions_total=5,
        )
        assert c.ml_prob == 0.5
        assert c.sentiment_pct == 50.0
        assert c.score_bonus == 0
        assert c.message == ""
        assert c.conditions_detail == []

    def test_conditions_detail_mutable_default(self) -> None:
        """Two instances should not share the same conditions_detail list."""
        c1 = TenbaggerCandidate(
            ticker="A", name="A", current_price=1, high_52w=2,
            drop_from_high_pct=-50, conditions_met=5, conditions_total=5,
        )
        c2 = TenbaggerCandidate(
            ticker="B", name="B", current_price=1, high_52w=2,
            drop_from_high_pct=-50, conditions_met=5, conditions_total=5,
        )
        c1.conditions_detail.append("test")
        assert c2.conditions_detail == []


# ===========================================================================
# _evaluate_conditions
# ===========================================================================

class TestEvaluateConditions:
    """Internal condition evaluation helper."""

    def test_all_five_met(self) -> None:
        met, details = _evaluate_conditions(
            drop_from_high_pct=-60,
            policy_support=True,
            revenue_growth_pct=20,
            foreign_buy_days_in_10=8,
            volume_ratio_20d=2.0,
        )
        assert met == 5
        assert len(details) == 5
        assert all("충족" in d for d in details)

    def test_none_met(self) -> None:
        met, details = _evaluate_conditions(
            drop_from_high_pct=-10,
            policy_support=False,
            revenue_growth_pct=5,
            foreign_buy_days_in_10=3,
            volume_ratio_20d=0.8,
        )
        assert met == 0
        assert all("미충족" in d for d in details)


# ===========================================================================
# scan_tenbagger
# ===========================================================================

class TestScanTenbagger:
    """Core tenbagger screening logic."""

    def test_all_5_conditions_returns_candidate(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        assert isinstance(result, TenbaggerCandidate)
        assert result.conditions_met == 5
        assert result.conditions_total == 5

    def test_all_5_conditions_score_bonus_20_base(self) -> None:
        """5/5 gives base 20; additional bonuses depend on cap/market/sector."""
        kw = _all_met_kwargs()
        kw["market_cap"] = 500_000_000_000  # 0.5조 — outside 1-10조 range
        kw["market"] = "KOSPI"
        kw["sector"] = "기타"  # not in TENBAGGER_THEMES
        result = scan_tenbagger(**kw)
        assert result is not None
        assert result.score_bonus == 20  # base only

    def test_four_conditions_returns_candidate(self) -> None:
        result = scan_tenbagger(**_four_met_kwargs())
        assert result is not None
        assert result.conditions_met == 4

    def test_four_conditions_score_bonus_15_base(self) -> None:
        kw = _four_met_kwargs()
        kw["market_cap"] = 500_000_000_000
        kw["market"] = "KOSPI"
        kw["sector"] = "기타"
        result = scan_tenbagger(**kw)
        assert result is not None
        assert result.score_bonus == 15

    def test_three_conditions_returns_none(self) -> None:
        result = scan_tenbagger(**_three_met_kwargs())
        assert result is None

    def test_two_conditions_returns_none(self) -> None:
        kw = _all_met_kwargs()
        kw["foreign_buy_days_in_10"] = 2
        kw["volume_ratio_20d"] = 0.5
        kw["policy_support"] = False
        result = scan_tenbagger(**kw)
        assert result is None

    def test_drop_from_high_calculated_correctly(self) -> None:
        kw = _all_met_kwargs()
        kw["current_price"] = 250_000
        kw["high_52w"] = 500_000
        result = scan_tenbagger(**kw)
        assert result is not None
        assert result.drop_from_high_pct == -50.0

    def test_drop_policy_support_growth(self) -> None:
        """Scenario: deep drop + policy support + growth but weak foreign/volume."""
        kw = _all_met_kwargs()
        kw["current_price"] = 100_000
        kw["high_52w"] = 300_000  # -66.7%
        kw["policy_support"] = True
        kw["revenue_growth_pct"] = 30
        kw["foreign_buy_days_in_10"] = 3   # fail
        kw["volume_ratio_20d"] = 0.8       # fail
        result = scan_tenbagger(**kw)
        assert result is None  # only 3/5 met

    def test_invalid_52w_high_returns_none(self) -> None:
        kw = _all_met_kwargs()
        kw["high_52w"] = 0
        result = scan_tenbagger(**kw)
        assert result is None

    def test_negative_52w_high_returns_none(self) -> None:
        kw = _all_met_kwargs()
        kw["high_52w"] = -100
        result = scan_tenbagger(**kw)
        assert result is None

    def test_ml_prob_and_sentiment_passed_through(self) -> None:
        kw = _all_met_kwargs()
        kw["ml_prob"] = 0.85
        kw["sentiment_pct"] = 72.0
        result = scan_tenbagger(**kw)
        assert result is not None
        assert result.ml_prob == 0.85
        assert result.sentiment_pct == 72.0

    def test_message_5_of_5(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        assert "5/5" in result.message
        assert "강력 주목" in result.message

    def test_message_4_of_5(self) -> None:
        result = scan_tenbagger(**_four_met_kwargs())
        assert result is not None
        assert "4/5" in result.message
        assert "관찰 요망" in result.message


# ===========================================================================
# _compute_bonus
# ===========================================================================

class TestComputeBonus:
    """Bonus scoring by conditions, market cap, market, and sector."""

    def test_market_cap_1_to_10_trillion_bonus(self) -> None:
        bonus = _compute_bonus(5, 5_000_000_000_000, "KOSPI", "기타")
        # base 20 + cap 2 = 22
        assert bonus == 22

    def test_market_cap_below_1_trillion_no_bonus(self) -> None:
        bonus = _compute_bonus(5, 500_000_000_000, "KOSPI", "기타")
        assert bonus == 20  # base only

    def test_market_cap_above_10_trillion_no_bonus(self) -> None:
        bonus = _compute_bonus(5, 15_000_000_000_000, "KOSPI", "기타")
        assert bonus == 20

    def test_kosdaq_bonus(self) -> None:
        bonus = _compute_bonus(5, 500_000_000_000, "KOSDAQ", "기타")
        # base 20 + KOSDAQ 1 = 21
        assert bonus == 21

    def test_kosdaq_korean_name(self) -> None:
        bonus = _compute_bonus(5, 500_000_000_000, "코스닥", "기타")
        assert bonus == 21

    def test_theme_bonus_ai(self) -> None:
        bonus = _compute_bonus(5, 500_000_000_000, "KOSPI", "AI")
        # base 20 + theme 2 = 22
        assert bonus == 22

    def test_theme_bonus_bio(self) -> None:
        bonus = _compute_bonus(5, 500_000_000_000, "KOSPI", "바이오")
        assert bonus == 22

    def test_all_bonuses_combined(self) -> None:
        """5/5 + 1-10조 cap + KOSDAQ + theme sector."""
        bonus = _compute_bonus(5, 3_000_000_000_000, "KOSDAQ", "AI")
        # 20 + 2 + 1 + 2 = 25
        assert bonus == 25

    def test_3_conditions_zero_base(self) -> None:
        bonus = _compute_bonus(3, 3_000_000_000_000, "KOSDAQ", "AI")
        # base 0 + 2 + 1 + 2 = 5
        assert bonus == 5

    def test_tenbagger_themes_completeness(self) -> None:
        expected = {"AI", "로봇", "우주", "2차전지", "바이오"}
        assert TENBAGGER_THEMES == expected


# ===========================================================================
# format_tenbagger_alert
# ===========================================================================

class TestFormatTenbaggerAlert:
    """Telegram alert formatting."""

    def test_contains_tenbagger_keyword(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "텐배거" in text

    def test_contains_ticker_and_name(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert result.ticker in text
        assert result.name in text

    def test_contains_price_info(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "현재가" in text
        assert "52주 고점" in text

    def test_contains_conditions_count(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "5/5" in text

    def test_5_of_5_strong_recommendation(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "강하게 주목" in text

    def test_4_of_5_tracking_recommendation(self) -> None:
        result = scan_tenbagger(**_four_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "추적" in text

    def test_score_bonus_shown(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert "보너스" in text

    def test_returns_string(self) -> None:
        result = scan_tenbagger(**_all_met_kwargs())
        assert result is not None
        text = format_tenbagger_alert(result)
        assert isinstance(text, str)
        assert len(text) > 0
