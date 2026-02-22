"""Tests for position manager: staged buy/sell planning (Section 38)."""

from __future__ import annotations

import pytest

from kstock.signal.position_manager import (
    BuyPlan,
    PositionAllocation,
    SellPlan,
    format_buy_plan,
    format_sell_plan,
    get_position_allocation,
    plan_buy,
    plan_sell_profit,
)


# ===========================================================================
# BuyPlan / SellPlan dataclasses
# ===========================================================================

class TestBuyPlanDataclass:
    """BuyPlan dataclass structure and defaults."""

    def test_required_fields(self) -> None:
        bp = BuyPlan(ticker="005930", name="삼성전자", total_budget=10_000_000)
        assert bp.ticker == "005930"
        assert bp.name == "삼성전자"
        assert bp.total_budget == 10_000_000

    def test_default_entries_empty(self) -> None:
        bp = BuyPlan(ticker="T", name="N", total_budget=1)
        assert bp.entries == []
        assert bp.message == ""

    def test_entries_isolation(self) -> None:
        bp1 = BuyPlan(ticker="A", name="A", total_budget=1)
        bp2 = BuyPlan(ticker="B", name="B", total_budget=1)
        bp1.entries.append({"phase": 1})
        assert bp2.entries == []


class TestSellPlanDataclass:
    """SellPlan dataclass structure and defaults."""

    def test_required_fields(self) -> None:
        sp = SellPlan(ticker="005930", name="삼성전자", profit_pct=15.0)
        assert sp.profit_pct == 15.0

    def test_default_entries_empty(self) -> None:
        sp = SellPlan(ticker="T", name="N", profit_pct=0.0)
        assert sp.entries == []
        assert sp.message == ""


# ===========================================================================
# plan_buy
# ===========================================================================

class TestPlanBuy:
    """Phased buy plan generation."""

    def test_returns_3_phases(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        assert len(plan.entries) == 3
        assert plan.entries[0]["phase"] == 1
        assert plan.entries[1]["phase"] == 2
        assert plan.entries[2]["phase"] == 3

    def test_default_balanced_allocation(self) -> None:
        """Default confidence (100) -> 50/30/20 split."""
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000, confidence_score=100)
        amounts = [e["amount"] for e in plan.entries]
        total = sum(amounts)
        # Phase 1 should be roughly 50% of total spent
        phase1_ratio = amounts[0] / total
        assert 0.40 <= phase1_ratio <= 0.60

    def test_high_confidence_aggressive(self) -> None:
        """Confidence > 130 -> 70/20/10 split."""
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000, confidence_score=150)
        amounts = [e["amount"] for e in plan.entries]
        total = sum(amounts)
        phase1_ratio = amounts[0] / total
        assert phase1_ratio >= 0.60  # 70% allocation front-loaded

    def test_low_confidence_conservative(self) -> None:
        """Confidence < 90 -> 30/30/40 split."""
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000, confidence_score=80)
        amounts = [e["amount"] for e in plan.entries]
        total = sum(amounts)
        phase1_ratio = amounts[0] / total
        assert phase1_ratio <= 0.40  # cautious first phase

    def test_phase2_pullback_price(self) -> None:
        """Phase 2 price should be ~3% below current."""
        plan = plan_buy("005930", "삼성전자", 100_000, 10_000_000)
        assert plan.entries[1]["price"] == 97_000  # 100000 * 0.97

    def test_quantities_positive(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        for entry in plan.entries:
            assert entry["quantity"] >= 1

    def test_total_budget_set(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 15_000_000)
        assert plan.total_budget == 15_000_000

    def test_returns_buy_plan_instance(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        assert isinstance(plan, BuyPlan)

    def test_message_populated(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        assert plan.message != ""

    def test_small_budget_still_works(self) -> None:
        """Even with very small budget, each phase has at least 1 share."""
        plan = plan_buy("005930", "삼성전자", 50_000, 100_000)
        for entry in plan.entries:
            assert entry["quantity"] >= 1


# ===========================================================================
# plan_sell_profit
# ===========================================================================

class TestPlanSellProfit:
    """Phased sell plan generation."""

    def test_profitable_returns_3_phases(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 15.0, 60_000, 100)
        assert len(plan.entries) == 3
        assert plan.entries[0]["quantity_pct"] == 30.0
        assert plan.entries[1]["quantity_pct"] == 30.0
        assert plan.entries[2]["quantity_pct"] == 40.0

    def test_profitable_conditions(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 10.0, 55_000, 100)
        assert "목표가" in plan.entries[0]["condition"]
        assert "트레일링" in plan.entries[1]["condition"]

    def test_losing_position_returns_2_options(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", -8.0, 46_000, 100)
        assert len(plan.entries) == 2
        assert "Option A" in plan.entries[0]["condition"]
        assert "Option B" in plan.entries[1]["condition"]

    def test_losing_option_a_is_100_pct(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", -5.0, 47_500, 100)
        assert plan.entries[0]["quantity_pct"] == 100.0

    def test_losing_option_b_is_50_pct(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", -5.0, 47_500, 100)
        assert plan.entries[1]["quantity_pct"] == 50.0

    def test_zero_profit_is_profitable_path(self) -> None:
        """profit_pct == 0 -> treated as profitable (3 phases)."""
        plan = plan_sell_profit("005930", "삼성전자", 0.0, 50_000, 100)
        assert len(plan.entries) == 3

    def test_returns_sell_plan_instance(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 10.0, 55_000, 100)
        assert isinstance(plan, SellPlan)

    def test_message_populated(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 10.0, 55_000, 100)
        assert plan.message != ""


# ===========================================================================
# get_position_allocation
# ===========================================================================

class TestGetPositionAllocation:
    """Portfolio allocation modes."""

    def test_balanced_mode(self) -> None:
        alloc = get_position_allocation("balanced")
        assert alloc.core_holding_pct == 55.0
        assert alloc.swing_pct == 25.0
        assert alloc.leverage_pct == 0.0
        assert alloc.cash_pct == 20.0
        total = alloc.core_holding_pct + alloc.swing_pct + alloc.leverage_pct + alloc.cash_pct
        assert total == 100.0

    def test_aggressive_mode(self) -> None:
        alloc = get_position_allocation("aggressive")
        assert alloc.leverage_pct == 15.0
        assert alloc.cash_pct == 5.0
        total = alloc.core_holding_pct + alloc.swing_pct + alloc.leverage_pct + alloc.cash_pct
        assert total == 100.0

    def test_defensive_mode(self) -> None:
        alloc = get_position_allocation("defensive")
        assert alloc.cash_pct == 50.0
        assert alloc.leverage_pct == 0.0
        total = alloc.core_holding_pct + alloc.swing_pct + alloc.leverage_pct + alloc.cash_pct
        assert total == 100.0

    def test_unknown_mode_falls_back_to_balanced(self) -> None:
        alloc = get_position_allocation("unknown_mode")
        balanced = get_position_allocation("balanced")
        assert alloc.core_holding_pct == balanced.core_holding_pct
        assert alloc.cash_pct == balanced.cash_pct

    def test_returns_position_allocation_instance(self) -> None:
        alloc = get_position_allocation("balanced")
        assert isinstance(alloc, PositionAllocation)


# ===========================================================================
# format_buy_plan
# ===========================================================================

class TestFormatBuyPlan:
    """Telegram buy plan formatting."""

    def test_contains_phase_info(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        text = format_buy_plan(plan)
        assert "1차" in text
        assert "2차" in text
        assert "3차" in text

    def test_contains_name(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        text = format_buy_plan(plan)
        assert "삼성전자" in text

    def test_contains_budget(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        text = format_buy_plan(plan)
        assert "총 예산" in text

    def test_returns_string(self) -> None:
        plan = plan_buy("005930", "삼성전자", 50_000, 10_000_000)
        text = format_buy_plan(plan)
        assert isinstance(text, str)
        assert len(text) > 0


# ===========================================================================
# format_sell_plan
# ===========================================================================

class TestFormatSellPlan:
    """Telegram sell plan formatting."""

    def test_profitable_header(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 12.5, 56_000, 100)
        text = format_sell_plan(plan)
        assert "수익" in text
        assert "삼성전자" in text

    def test_losing_header(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", -7.0, 46_500, 100)
        text = format_sell_plan(plan)
        assert "손실" in text

    def test_contains_phase_labels(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 10.0, 55_000, 100)
        text = format_sell_plan(plan)
        assert "1차" in text

    def test_returns_string(self) -> None:
        plan = plan_sell_profit("005930", "삼성전자", 10.0, 55_000, 100)
        text = format_sell_plan(plan)
        assert isinstance(text, str)
        assert len(text) > 0
