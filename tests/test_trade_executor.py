"""Tests for bot/trade_executor.py - Semi-automatic trade execution."""

import pytest
from kstock.bot.trade_executor import (
    TradeOrder,
    TrailingStop,
    SplitPlan,
    compute_order,
    compute_trailing_stop,
    check_trailing_stops,
    create_split_plan,
    format_order_confirmation,
    format_trailing_alert,
    format_split_plan,
)


# ---------------------------------------------------------------------------
# TestComputeOrder
# ---------------------------------------------------------------------------

class TestComputeOrder:
    """compute_order: 매수 주문 계산."""

    def test_buy_order_basic(self):
        """1000만원 예산, 76500원 주가 -> 올바른 수량/금액/수수료."""
        order = compute_order("005930", "삼성전자", 76_500, 10_000_000)
        assert order.direction == "buy"
        assert order.quantity > 0
        # 10_000_000 / (76_500 * 1.00015) ~ 130
        assert order.quantity == 130
        assert order.amount == 130 * 76_500
        assert order.commission == round(order.amount * 0.00015, 2)

    def test_buy_order_price_zero(self):
        """가격 0 -> 수량 0."""
        order = compute_order("005930", "삼성전자", 0, 10_000_000)
        assert order.quantity == 0

    def test_buy_order_budget_zero(self):
        """예산 0 -> 수량 0."""
        order = compute_order("005930", "삼성전자", 76_500, 0)
        assert order.quantity == 0

    def test_buy_order_insufficient_budget(self):
        """가격보다 예산이 적으면 수량 0."""
        order = compute_order("005930", "삼성전자", 76_500, 50_000)
        assert order.quantity == 0
        assert order.amount == 0.0

    def test_order_type_is_limit(self):
        """기본 주문유형은 지정가(limit)."""
        order = compute_order("005930", "삼성전자", 76_500, 10_000_000)
        assert order.order_type == "limit"


# ---------------------------------------------------------------------------
# TestComputeTrailingStop
# ---------------------------------------------------------------------------

class TestComputeTrailingStop:
    """compute_trailing_stop: 트레일링 스탑 계산."""

    def test_scalp_trailing_3pct(self):
        """scalp 호라이즌 -> 3% 트레일링."""
        stop = compute_trailing_stop("005930", "삼성전자", 100, 100, horizon="scalp")
        assert stop.trailing_pct == 0.03
        assert stop.stop_price == round(100 * (1 - 0.03), 0)

    def test_mid_trailing_8pct(self):
        """mid 호라이즌 -> 8% 트레일링."""
        stop = compute_trailing_stop("005930", "삼성전자", 100, 100, horizon="mid")
        assert stop.trailing_pct == 0.08
        assert stop.stop_price == round(100 * (1 - 0.08), 0)

    def test_long_trailing_15pct(self):
        """long 호라이즌 -> 15% 트레일링."""
        stop = compute_trailing_stop("005930", "삼성전자", 100, 100, horizon="long")
        assert stop.trailing_pct == 0.15
        assert stop.stop_price == round(100 * (1 - 0.15), 0)

    def test_stop_at_92_mid(self):
        """고점 100, 현재가 95, mid -> 스탑가 92 (100 * 0.92)."""
        stop = compute_trailing_stop("005930", "삼성전자", 95, 100, horizon="mid")
        assert stop.peak_price == 100
        assert stop.stop_price == 92
        # 95 > 92 -> not triggered
        assert stop.is_triggered is False

    def test_triggered_when_current_below_stop(self):
        """현재가가 스탑가 이하면 발동."""
        stop = compute_trailing_stop("005930", "삼성전자", 91, 100, horizon="mid")
        assert stop.stop_price == 92
        assert stop.is_triggered is True

    def test_peak_update_when_current_higher(self):
        """현재가가 기존 고점보다 높으면 고점 갱신."""
        stop = compute_trailing_stop("005930", "삼성전자", 110, 100, horizon="mid")
        assert stop.peak_price == 110
        assert stop.stop_price == round(110 * (1 - 0.08), 0)

    def test_short_trailing_5pct(self):
        """short 호라이즌 -> 5% 트레일링."""
        stop = compute_trailing_stop("005930", "삼성전자", 100, 100, horizon="short")
        assert stop.trailing_pct == 0.05


# ---------------------------------------------------------------------------
# TestCheckTrailingStops
# ---------------------------------------------------------------------------

class TestCheckTrailingStops:
    """check_trailing_stops: 복수 스탑 점검."""

    def test_one_triggered_one_not(self):
        """하나는 발동, 하나는 미발동 -> 발동된 것만 반환."""
        stops = [
            TrailingStop(
                ticker="A", name="종목A",
                peak_price=100, trailing_pct=0.08,
                stop_price=92, is_triggered=False,
            ),
            TrailingStop(
                ticker="B", name="종목B",
                peak_price=100, trailing_pct=0.08,
                stop_price=92, is_triggered=False,
            ),
        ]
        current_prices = {"A": 95, "B": 90}
        triggered = check_trailing_stops(stops, current_prices)
        tickers = [s.ticker for s in triggered]
        assert "B" in tickers
        assert "A" not in tickers

    def test_empty_stops(self):
        triggered = check_trailing_stops([], {})
        assert triggered == []

    def test_missing_price_skipped(self):
        """현재가 없는 종목은 건너뜀."""
        stops = [
            TrailingStop(
                ticker="A", name="종목A",
                peak_price=100, trailing_pct=0.08,
                stop_price=92, is_triggered=False,
            ),
        ]
        triggered = check_trailing_stops(stops, {})
        assert triggered == []


# ---------------------------------------------------------------------------
# TestCreateSplitPlan
# ---------------------------------------------------------------------------

class TestCreateSplitPlan:
    """create_split_plan: 분할매매 계획."""

    def test_3_tranches_150_shares(self):
        """3트랜치, 150주 -> 각 ~50주, 합 150주."""
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=76_500)
        assert len(plan.tranches) == 3
        total_qty = sum(t["quantity"] for t in plan.tranches)
        assert total_qty == 150

    def test_percentages_sum_to_100(self):
        """비중 합이 대략 100%."""
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=76_500)
        total_pct = sum(t["pct"] for t in plan.tranches)
        assert abs(total_pct - 100.0) < 1.0

    def test_single_tranche(self):
        """1트랜치 -> 전량."""
        plan = create_split_plan("005930", "삼성전자", 100, n_tranches=1, base_price=76_500)
        assert len(plan.tranches) == 1
        assert plan.tranches[0]["quantity"] == 100

    def test_zero_quantity(self):
        """수량 0 -> 빈 계획."""
        plan = create_split_plan("005930", "삼성전자", 0, n_tranches=3)
        assert plan.tranches == []

    def test_price_step_decreases(self):
        """각 트랜치 가격이 단계적으로 하락."""
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=100_000)
        prices = [t["price"] for t in plan.tranches]
        assert prices[0] > prices[1] > prices[2]


# ---------------------------------------------------------------------------
# TestFormatOrderConfirmation
# ---------------------------------------------------------------------------

class TestFormatOrderConfirmation:
    """format_order_confirmation: 주문 확인 메시지 포맷."""

    def test_no_bold_markers(self):
        order = TradeOrder(
            ticker="005930", name="삼성전자", direction="buy",
            quantity=130, price=76_500, amount=9_945_000, commission=1_492,
        )
        msg = format_order_confirmation(order)
        assert "**" not in msg

    def test_contains_confirmation_keyword(self):
        order = TradeOrder(
            ticker="005930", name="삼성전자", direction="buy",
            quantity=130, price=76_500, amount=9_945_000, commission=1_492,
        )
        msg = format_order_confirmation(order)
        # "체결" 관련 -> 주문 확인 메시지에는 "확인" 키워드가 있음
        assert "확인" in msg

    def test_korean_format(self):
        order = TradeOrder(
            ticker="005930", name="삼성전자", direction="buy",
            quantity=130, price=76_500, amount=9_945_000, commission=1_492,
        )
        msg = format_order_confirmation(order)
        assert "주호님" in msg
        assert "삼성전자" in msg
        assert "매수" in msg


# ---------------------------------------------------------------------------
# TestFormatTrailingAlert
# ---------------------------------------------------------------------------

class TestFormatTrailingAlert:
    """format_trailing_alert: 트레일링 스탑 알림 포맷."""

    def test_no_bold_markers(self):
        stop = TrailingStop(
            ticker="005930", name="삼성전자",
            peak_price=100_000, trailing_pct=0.08,
            stop_price=92_000, is_triggered=True,
        )
        msg = format_trailing_alert(stop)
        assert "**" not in msg

    def test_contains_stop_keyword(self):
        stop = TrailingStop(
            ticker="005930", name="삼성전자",
            peak_price=100_000, trailing_pct=0.08,
            stop_price=92_000, is_triggered=True,
        )
        msg = format_trailing_alert(stop)
        assert "스탑" in msg

    def test_contains_user_name(self):
        stop = TrailingStop(
            ticker="005930", name="삼성전자",
            peak_price=100_000, trailing_pct=0.08,
            stop_price=92_000, is_triggered=True,
        )
        msg = format_trailing_alert(stop)
        assert "주호님" in msg


# ---------------------------------------------------------------------------
# TestFormatSplitPlan
# ---------------------------------------------------------------------------

class TestFormatSplitPlan:
    """format_split_plan: 분할매매 계획 메시지 포맷."""

    def test_no_bold_markers(self):
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=76_500)
        msg = format_split_plan(plan)
        assert "**" not in msg

    def test_contains_split_keyword(self):
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=76_500)
        msg = format_split_plan(plan)
        assert "분할" in msg

    def test_contains_user_name(self):
        plan = create_split_plan("005930", "삼성전자", 150, n_tranches=3, base_price=76_500)
        msg = format_split_plan(plan)
        assert "주호님" in msg
