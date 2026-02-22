"""Tests for kstock.signal.profit_protector module."""

from __future__ import annotations

import pytest

from kstock.signal.profit_protector import (
    PROTECTION_TIERS,
    ProfitProtection,
    ProtectionRule,
    _get_tier,
    compute_protection,
    format_profit_report,
    should_block_sell,
)


# ---------------------------------------------------------------------------
# _get_tier
# ---------------------------------------------------------------------------
class TestGetTier:
    def test_a_high_above_50(self):
        rule = _get_tier(88.0)
        assert rule.tier == "A_high"
        assert rule.trailing_stop_pct == -10.0
        assert rule.max_sell_pct == 30.0

    def test_a_high_exactly_50(self):
        rule = _get_tier(50.0)
        assert rule.tier == "A_high"

    def test_a_mid_20_to_50(self):
        rule = _get_tier(35.0)
        assert rule.tier == "A_mid"
        assert rule.trailing_stop_pct == -8.0
        assert rule.max_sell_pct == 50.0

    def test_a_mid_exactly_20(self):
        rule = _get_tier(20.0)
        assert rule.tier == "A_mid"

    def test_a_low_5_to_20(self):
        rule = _get_tier(12.0)
        assert rule.tier == "A_low"
        assert rule.trailing_stop_pct == -7.0
        assert rule.max_sell_pct == 0.0

    def test_a_low_exactly_5(self):
        rule = _get_tier(5.0)
        assert rule.tier == "A_low"

    def test_b_below_5(self):
        rule = _get_tier(3.0)
        assert rule.tier == "B"
        assert rule.trailing_stop_pct == 0.0
        assert rule.max_sell_pct == 0.0

    def test_b_zero_profit(self):
        rule = _get_tier(0.0)
        assert rule.tier == "B"

    def test_b_negative_profit(self):
        rule = _get_tier(-10.0)
        assert rule.tier == "B"


# ---------------------------------------------------------------------------
# compute_protection
# ---------------------------------------------------------------------------
class TestComputeProtection:
    def test_high_profit_neutral(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=60.0,
            current_price=80000, high_price=85000,
        )
        assert p.tier == "A_high"
        assert p.trailing_stop_price > 0
        assert p.trailing_stop_price == 76500

    def test_high_profit_bullish_sector(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=60.0,
            current_price=80000, high_price=85000,
            sector_trend="bullish",
        )
        assert p.tier == "A_high"
        assert p.trailing_stop_pct == -12.0
        assert p.trailing_stop_price == 74800

    def test_high_profit_bearish_sector(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=60.0,
            current_price=80000, high_price=85000,
            sector_trend="bearish",
        )
        assert p.tier == "A_high"
        assert p.trailing_stop_pct == -8.0
        assert p.trailing_stop_price == 78200

    def test_mid_profit(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=30.0,
            current_price=65000, high_price=70000,
        )
        assert p.tier == "A_mid"
        assert p.trailing_stop_price == 64400

    def test_low_profit(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=8.0,
            current_price=54000, high_price=55000,
        )
        assert p.tier == "A_low"
        assert p.trailing_stop_price == 51150

    def test_b_tier_no_trailing(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=2.0,
            current_price=51000, high_price=52000,
        )
        assert p.tier == "B"
        assert p.trailing_stop_price == 0

    def test_trailing_stop_reached(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=55.0,
            current_price=70000, high_price=85000,
        )
        assert "트레일링 스탑 도달" in p.message

    def test_ml_upside(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=25.0,
            current_price=62000, high_price=65000, ml_prob=0.85,
        )
        assert "ml_upside" in p.additional_upside
        assert p.additional_upside["ml_upside"] > 0

    def test_no_ml_upside_low_prob(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=25.0,
            current_price=62000, high_price=65000, ml_prob=0.5,
        )
        assert "ml_upside" not in p.additional_upside

    def test_high_profit_high_ml_message(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=55.0,
            current_price=82000, high_price=85000, ml_prob=0.75,
        )
        assert "대박 종목" in p.message or "계속 들고" in p.message

    def test_sector_bonus_in_additional_upside(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=10.0,
            current_price=55000, high_price=56000,
            sector_trend="bullish",
        )
        assert p.additional_upside.get("sector_bonus") == 5.0

    def test_sector_penalty_in_additional_upside(self):
        p = compute_protection(
            ticker="005930", name="삼성전자", profit_pct=10.0,
            current_price=55000, high_price=56000,
            sector_trend="bearish",
        )
        assert p.additional_upside.get("sector_penalty") == -5.0


# ---------------------------------------------------------------------------
# should_block_sell
# ---------------------------------------------------------------------------
class TestShouldBlockSell:
    def test_block_high_profit_large_sell(self):
        blocked, msg = should_block_sell(profit_pct=55.0, sell_pct=50.0)
        assert blocked is True
        assert "30%" in msg

    def test_allow_high_profit_small_sell(self):
        blocked, msg = should_block_sell(profit_pct=55.0, sell_pct=25.0)
        assert blocked is False

    def test_block_profit_100pct_sell(self):
        blocked, msg = should_block_sell(profit_pct=10.0, sell_pct=100.0)
        assert blocked is True
        assert "전량 매도" in msg

    def test_allow_low_profit_sell(self):
        blocked, msg = should_block_sell(profit_pct=2.0, sell_pct=100.0)
        assert blocked is False

    def test_allow_normal_sell(self):
        blocked, msg = should_block_sell(profit_pct=15.0, sell_pct=30.0)
        assert blocked is False

    def test_exactly_50pct_profit_31pct_sell(self):
        blocked, _ = should_block_sell(profit_pct=50.0, sell_pct=31.0)
        assert blocked is True

    def test_exactly_5pct_profit_100_sell(self):
        blocked, _ = should_block_sell(profit_pct=5.0, sell_pct=100.0)
        assert blocked is True


# ---------------------------------------------------------------------------
# format_profit_report
# ---------------------------------------------------------------------------
class TestFormatProfitReport:
    def test_empty_list(self):
        assert format_profit_report([]) == ""

    def test_single_holding(self):
        p = ProfitProtection(
            ticker="005930", name="삼성전자", profit_pct=60.0,
            tier="A_high", trailing_stop_pct=-10.0,
            trailing_stop_price=76500, max_sell_pct=30.0,
            message="대박 종목 계속 들고 가세요",
        )
        result = format_profit_report([p])
        assert "수익 종목 현황" in result
        assert "삼성전자" in result
        assert "+60%" in result
        assert "76,500원" in result
        assert "최대 매도: 30%" in result

    def test_sorted_by_profit_desc(self):
        p1 = ProfitProtection(
            ticker="A", name="종목A", profit_pct=10.0,
            tier="A_low", trailing_stop_pct=-7.0,
            trailing_stop_price=93000, max_sell_pct=0.0,
        )
        p2 = ProfitProtection(
            ticker="B", name="종목B", profit_pct=55.0,
            tier="A_high", trailing_stop_pct=-10.0,
            trailing_stop_price=90000, max_sell_pct=30.0,
        )
        result = format_profit_report([p1, p2])
        idx_b = result.index("종목B")
        idx_a = result.index("종목A")
        assert idx_b < idx_a

    def test_no_trailing_for_b_tier(self):
        p = ProfitProtection(
            ticker="X", name="종목X", profit_pct=2.0,
            tier="B", trailing_stop_pct=0.0,
            trailing_stop_price=0, max_sell_pct=0.0,
            message="좀 더 지켜보세요",
        )
        result = format_profit_report([p])
        assert "트레일링 스탑" not in result
        assert "최대 매도" not in result

    def test_no_bold_formatting(self):
        p = ProfitProtection(
            ticker="005930", name="삼성전자", profit_pct=60.0,
            tier="A_high", trailing_stop_pct=-10.0,
            trailing_stop_price=76500, max_sell_pct=30.0,
        )
        result = format_profit_report([p])
        assert "**" not in result


# ---------------------------------------------------------------------------
# Protection tiers structure
# ---------------------------------------------------------------------------
class TestProtectionTiers:
    def test_four_tiers_defined(self):
        assert len(PROTECTION_TIERS) == 4

    def test_tier_names(self):
        names = [t.tier for t in PROTECTION_TIERS]
        assert names == ["A_high", "A_mid", "A_low", "B"]

    def test_all_have_messages(self):
        for tier in PROTECTION_TIERS:
            assert tier.message, f"Tier {tier.tier} has no message"
