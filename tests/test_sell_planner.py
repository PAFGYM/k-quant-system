"""Tests for core/sell_planner.py - Phase 8."""

from __future__ import annotations

import pytest

from kstock.core.sell_planner import SellPlanner, SellPlan, format_sell_plans


class TestSellPlanner:
    def setup_method(self):
        self.planner = SellPlanner()

    def test_scalp_profit(self):
        """스캘핑 수익 5% 이상 → 트레일링 스탑."""
        holding = {
            "ticker": "005930", "name": "삼성전자",
            "buy_price": 70000, "current_price": 74000,
            "pnl_pct": 5.7, "horizon": "scalp",
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "high"
        assert "트레일링" in plan.strategy
        assert plan.horizon == "scalp"

    def test_scalp_loss(self):
        """스캘핑 손실 2% 이상 → 손절 경고."""
        holding = {
            "ticker": "005930", "name": "삼성전자",
            "buy_price": 70000, "current_price": 68500,
            "pnl_pct": -2.1, "horizon": "scalp",
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "high"
        assert "손절" in plan.strategy

    def test_scalp_neutral(self):
        """스캘핑 보합 → 관망."""
        holding = {
            "ticker": "005930", "name": "삼성전자",
            "buy_price": 70000, "current_price": 70500,
            "pnl_pct": 0.7, "horizon": "scalp",
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "medium"
        assert "홀딩" in plan.strategy

    def test_swing_profit(self):
        """스윙 수익 10% 이상 → 부분 익절."""
        holding = {
            "ticker": "035420", "name": "NAVER",
            "buy_price": 200000, "current_price": 222000,
            "pnl_pct": 11.0, "horizon": "swing",
            "ma5": 218000, "ma20": 210000,
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "medium"
        assert "익절" in plan.strategy

    def test_swing_below_ma20(self):
        """스윙 20일선 이탈 → 주의."""
        holding = {
            "ticker": "035420", "name": "NAVER",
            "buy_price": 200000, "current_price": 195000,
            "pnl_pct": -2.5, "horizon": "swing",
            "ma5": 197000, "ma20": 202000,
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "high"
        assert "20일선" in plan.strategy

    def test_mid_profit(self):
        """중기 수익 20% 이상 → 부분 익절."""
        holding = {
            "ticker": "005380", "name": "현대차",
            "buy_price": 180000, "current_price": 220000,
            "pnl_pct": 22.2, "horizon": "mid",
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "medium"
        assert "익절" in plan.strategy

    def test_mid_loss(self):
        """중기 손실 5% 이상 → 재점검."""
        holding = {
            "ticker": "005380", "name": "현대차",
            "buy_price": 180000, "current_price": 168000,
            "pnl_pct": -6.7, "horizon": "mid",
        }
        plan = self.planner.create_plan(holding)
        assert "투자 논리" in plan.strategy

    def test_long_big_profit(self):
        """장기 수익 40% 이상 → 부분 익절 고려."""
        holding = {
            "ticker": "373220", "name": "LG에너지솔루션",
            "buy_price": 300000, "current_price": 450000,
            "pnl_pct": 50.0, "horizon": "long",
        }
        plan = self.planner.create_plan(holding)
        assert "부분 익절" in plan.strategy

    def test_long_deep_loss(self):
        """장기 손실 10% 이상 → 재점검."""
        holding = {
            "ticker": "373220", "name": "LG에너지솔루션",
            "buy_price": 300000, "current_price": 260000,
            "pnl_pct": -13.3, "horizon": "long",
        }
        plan = self.planner.create_plan(holding)
        assert plan.urgency == "high"
        assert "투자 논리" in plan.strategy

    def test_create_plans_for_all(self):
        """여러 종목 일괄 계획 생성."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "buy_price": 70000,
             "current_price": 73000, "pnl_pct": 4.3, "horizon": "swing"},
            {"ticker": "035420", "name": "NAVER", "buy_price": 200000,
             "current_price": 195000, "pnl_pct": -2.5, "horizon": "mid"},
        ]
        plans = self.planner.create_plans_for_all(holdings)
        assert len(plans) == 2
        assert all(isinstance(p, SellPlan) for p in plans)

    def test_default_horizon_is_swing(self):
        """horizon 없으면 기본값 swing."""
        holding = {
            "ticker": "005930", "name": "삼성전자",
            "buy_price": 70000, "current_price": 72000,
            "pnl_pct": 2.9,
        }
        plan = self.planner.create_plan(holding)
        assert plan.horizon == "swing"


class TestFormatSellPlans:
    def test_empty_plans(self):
        result = format_sell_plans([])
        assert "없어" in result

    def test_format_with_plans(self):
        plans = [
            SellPlan(
                ticker="005930", name="삼성전자", horizon="swing",
                target="77,000원 (+10%)", stoploss="66,500원 (-5%)",
                strategy="정상 궤도. 20일선 위 유지 중.",
                urgency="low", pnl_pct=4.3,
            ),
            SellPlan(
                ticker="035420", name="NAVER", horizon="mid",
                target="250,000원 (+25%)", stoploss="184,000원 (-8%)",
                strategy="투자 논리 재점검 필요.",
                urgency="high", pnl_pct=-6.7,
            ),
        ]
        result = format_sell_plans(plans)
        assert "삼성전자" in result
        assert "NAVER" in result
        assert "매도 계획" in result
        assert "긴급 주의" in result  # NAVER is high urgency
