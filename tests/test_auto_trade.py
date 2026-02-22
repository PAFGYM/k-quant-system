"""Tests for auto-trade workflow integration.

Tests the KIS broker safety limits and trade computation logic together,
plus DataRouter source detection as part of the end-to-end auto-trade path.
"""

from __future__ import annotations

import pytest

from kstock.broker.kis_broker import KisBroker, SafetyLimits
from kstock.ingest.data_router import DataRouter


# ---------------------------------------------------------------------------
# Helpers / mock objects
# ---------------------------------------------------------------------------

class FakeKISBroker:
    """Minimal stand-in for a KIS broker with controllable state."""

    def __init__(self, *, connected: bool = True, mode: str = "virtual") -> None:
        self.connected = connected
        self.mode = mode
        self.safety = SafetyLimits()

    def compute_buy_quantity(self, price: float, total_eval: float, pct: float = 10.0) -> int:
        if price <= 0 or total_eval <= 0:
            return 0
        amount = total_eval * pct / 100
        return int(amount // price)


class FakeYFClient:
    """Minimal stand-in for a yfinance client object."""


class FakeDB:
    """Minimal stand-in for a database object."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def safety():
    """Fresh SafetyLimits with defaults."""
    return SafetyLimits()


@pytest.fixture
def broker(tmp_path):
    """KisBroker initialised without real config (disconnected)."""
    return KisBroker(config_path=str(tmp_path / "nonexistent.yaml"))


@pytest.fixture
def fake_broker_connected():
    return FakeKISBroker(connected=True, mode="virtual")


@pytest.fixture
def fake_broker_disconnected():
    return FakeKISBroker(connected=False, mode="virtual")


@pytest.fixture
def yf_client():
    return FakeYFClient()


@pytest.fixture
def db():
    return FakeDB()


# ---------------------------------------------------------------------------
# 1. SafetyLimits allows order within limits
# ---------------------------------------------------------------------------

class TestSafetyLimitsAllowsNormal:
    def test_order_well_within_limits(self, safety):
        ok, msg = safety.can_order(5.0)
        assert ok is True
        assert msg == ""

    def test_order_at_exact_max_pct(self, safety):
        ok, msg = safety.can_order(15.0)
        assert ok is True
        assert msg == ""

    def test_order_at_zero_pct(self, safety):
        ok, msg = safety.can_order(0.0)
        assert ok is True
        assert msg == ""


# ---------------------------------------------------------------------------
# 2. SafetyLimits blocks order exceeding max_order_pct
# ---------------------------------------------------------------------------

class TestSafetyBlocksExceedingMaxOrderPct:
    def test_blocks_above_max(self, safety):
        ok, msg = safety.can_order(20.0)
        assert ok is False
        assert "1회 주문 한도 초과" in msg

    def test_blocks_slightly_above_max(self, safety):
        ok, msg = safety.can_order(15.1)
        assert ok is False
        assert "15.1%" in msg

    def test_message_contains_limit_info(self, safety):
        ok, msg = safety.can_order(50.0)
        assert ok is False
        assert "50.0%" in msg
        assert "15.0%" in msg


# ---------------------------------------------------------------------------
# 3. SafetyLimits blocks after max_daily_orders reached
# ---------------------------------------------------------------------------

class TestSafetyBlocksMaxDailyOrders:
    def test_blocks_at_max_count(self, safety):
        safety.daily_order_count = 10
        ok, msg = safety.can_order(5.0)
        assert ok is False
        assert "일일 주문 횟수 초과" in msg

    def test_blocks_above_max_count(self, safety):
        safety.daily_order_count = 15
        ok, msg = safety.can_order(5.0)
        assert ok is False
        assert "15/10" in msg

    def test_allows_one_below_max_count(self, safety):
        safety.daily_order_count = 9
        ok, msg = safety.can_order(5.0)
        assert ok is True


# ---------------------------------------------------------------------------
# 4. SafetyLimits blocks when daily_loss_limit reached
# ---------------------------------------------------------------------------

class TestSafetyBlocksDailyLossLimit:
    def test_blocks_at_exact_loss_limit(self, safety):
        safety.daily_pnl_pct = -3.0
        ok, msg = safety.can_order(5.0)
        assert ok is False
        assert "일일 손실 한도 도달" in msg

    def test_blocks_when_loss_exceeds_limit(self, safety):
        safety.daily_pnl_pct = -5.0
        ok, msg = safety.can_order(5.0)
        assert ok is False
        assert "-5.0%" in msg

    def test_allows_when_loss_above_limit(self, safety):
        safety.daily_pnl_pct = -2.9
        ok, msg = safety.can_order(5.0)
        assert ok is True


# ---------------------------------------------------------------------------
# 5. SafetyLimits record_order increments correctly
# ---------------------------------------------------------------------------

class TestRecordOrderIncrements:
    def test_single_record(self, safety):
        assert safety.daily_order_count == 0
        safety.record_order()
        assert safety.daily_order_count == 1

    def test_multiple_records(self, safety):
        for i in range(5):
            safety.record_order()
        assert safety.daily_order_count == 5


# ---------------------------------------------------------------------------
# 6. SafetyLimits reset_daily clears all counters
# ---------------------------------------------------------------------------

class TestResetDailyClearsCounters:
    def test_clears_order_count(self, safety):
        safety.daily_order_count = 7
        safety.reset_daily()
        assert safety.daily_order_count == 0

    def test_clears_pnl(self, safety):
        safety.daily_pnl_pct = -2.5
        safety.reset_daily()
        assert safety.daily_pnl_pct == 0.0

    def test_clears_both_simultaneously(self, safety):
        safety.daily_order_count = 8
        safety.daily_pnl_pct = -1.0
        safety.reset_daily()
        assert safety.daily_order_count == 0
        assert safety.daily_pnl_pct == 0.0


# ---------------------------------------------------------------------------
# 7. compute_buy_quantity calculates 10% of portfolio correctly
# ---------------------------------------------------------------------------

class TestComputeBuyQuantity10Pct:
    def test_samsung_10pct(self, broker):
        # 10% of 10,000,000 = 1,000,000; 1,000,000 // 58,000 = 17
        qty = broker.compute_buy_quantity(58_000, 10_000_000, pct=10.0)
        assert qty == 17

    def test_10pct_exact_division(self, broker):
        # 10% of 5,000,000 = 500,000; 500,000 // 50,000 = 10
        qty = broker.compute_buy_quantity(50_000, 5_000_000, pct=10.0)
        assert qty == 10


# ---------------------------------------------------------------------------
# 8. compute_buy_quantity with expensive stock returns fewer shares
# ---------------------------------------------------------------------------

class TestComputeBuyQuantityExpensiveStock:
    def test_expensive_stock_small_portfolio(self, broker):
        # 10% of 5,000,000 = 500,000; 500,000 // 300,000 = 1
        qty = broker.compute_buy_quantity(300_000, 5_000_000, pct=10.0)
        assert qty == 1

    def test_stock_price_exceeds_budget(self, broker):
        # 10% of 1,000,000 = 100,000; 100,000 // 200,000 = 0
        qty = broker.compute_buy_quantity(200_000, 1_000_000, pct=10.0)
        assert qty == 0


# ---------------------------------------------------------------------------
# 9. compute_buy_quantity with zero price returns 0
# ---------------------------------------------------------------------------

class TestComputeBuyQuantityZeroPrice:
    def test_zero_price(self, broker):
        assert broker.compute_buy_quantity(0, 10_000_000) == 0

    def test_negative_price(self, broker):
        assert broker.compute_buy_quantity(-100, 10_000_000) == 0

    def test_zero_total_eval(self, broker):
        assert broker.compute_buy_quantity(58_000, 0) == 0

    def test_negative_total_eval(self, broker):
        assert broker.compute_buy_quantity(58_000, -5_000_000) == 0


# ---------------------------------------------------------------------------
# 10. compute_buy_quantity with various pct values
# ---------------------------------------------------------------------------

class TestComputeBuyQuantityVariousPct:
    def test_5_pct(self, broker):
        # 5% of 10,000,000 = 500,000; 500,000 // 50,000 = 10
        qty = broker.compute_buy_quantity(50_000, 10_000_000, pct=5.0)
        assert qty == 10

    def test_15_pct(self, broker):
        # 15% of 10,000,000 = 1,500,000; 1,500,000 // 50,000 = 30
        qty = broker.compute_buy_quantity(50_000, 10_000_000, pct=15.0)
        assert qty == 30

    def test_1_pct(self, broker):
        # 1% of 10,000,000 = 100,000; 100,000 // 50,000 = 2
        qty = broker.compute_buy_quantity(50_000, 10_000_000, pct=1.0)
        assert qty == 2

    def test_result_is_always_int(self, broker):
        qty = broker.compute_buy_quantity(33_333, 10_000_000, pct=7.5)
        assert isinstance(qty, int)


# ---------------------------------------------------------------------------
# 11. Full workflow: compute quantity -> check safety -> record order
# ---------------------------------------------------------------------------

class TestFullWorkflow:
    def test_full_buy_workflow_passes(self, broker):
        """Simulate: compute quantity, check safety, record the order."""
        price = 58_000
        total_eval = 10_000_000
        order_pct = 10.0

        qty = broker.compute_buy_quantity(price, total_eval, pct=order_pct)
        assert qty > 0

        ok, msg = broker.safety.can_order(order_pct)
        assert ok is True
        assert msg == ""

        broker.safety.record_order()
        assert broker.safety.daily_order_count == 1

    def test_workflow_blocked_by_pct_limit(self, broker):
        """Order passes compute but is rejected by safety pct check."""
        price = 50_000
        total_eval = 10_000_000
        order_pct = 20.0  # exceeds default 15% max

        qty = broker.compute_buy_quantity(price, total_eval, pct=order_pct)
        assert qty > 0  # compute itself does not enforce safety

        ok, msg = broker.safety.can_order(order_pct)
        assert ok is False
        assert "한도 초과" in msg

    def test_workflow_blocked_after_max_daily_orders(self, broker):
        """Ten orders succeed; the eleventh is blocked."""
        for i in range(10):
            ok, _ = broker.safety.can_order(5.0)
            assert ok is True
            broker.safety.record_order()

        ok, msg = broker.safety.can_order(5.0)
        assert ok is False
        assert "횟수 초과" in msg

    def test_workflow_resumes_after_daily_reset(self, broker):
        """After hitting the daily order cap, reset_daily re-enables ordering."""
        for _ in range(10):
            broker.safety.record_order()
        ok, _ = broker.safety.can_order(5.0)
        assert ok is False

        broker.safety.reset_daily()
        ok, msg = broker.safety.can_order(5.0)
        assert ok is True
        assert msg == ""

    def test_workflow_compute_then_safety_then_record(self, broker):
        """Two consecutive orders in sequence, verifying count increments."""
        for expected_count in (1, 2, 3):
            qty = broker.compute_buy_quantity(50_000, 10_000_000, pct=10.0)
            assert qty == 20
            ok, _ = broker.safety.can_order(10.0)
            assert ok is True
            broker.safety.record_order()
            assert broker.safety.daily_order_count == expected_count


# ---------------------------------------------------------------------------
# 12. DataRouter detects KIS when broker is connected
# ---------------------------------------------------------------------------

class TestDataRouterDetectsKIS:
    def test_source_is_kis(self, fake_broker_connected, yf_client, db):
        router = DataRouter(kis_broker=fake_broker_connected, yf_client=yf_client, db=db)
        assert router.source_name == "kis"

    def test_kis_connected_property_true(self, fake_broker_connected):
        router = DataRouter(kis_broker=fake_broker_connected)
        assert router.kis_connected is True


# ---------------------------------------------------------------------------
# 13. DataRouter falls back to yfinance when broker not connected
# ---------------------------------------------------------------------------

class TestDataRouterFallsBackToYfinance:
    def test_disconnected_broker_falls_back(self, fake_broker_disconnected, yf_client):
        router = DataRouter(kis_broker=fake_broker_disconnected, yf_client=yf_client)
        assert router.source_name == "yfinance"

    def test_no_broker_falls_back(self, yf_client):
        router = DataRouter(kis_broker=None, yf_client=yf_client)
        assert router.source_name == "yfinance"

    def test_kis_connected_property_false_when_disconnected(self, fake_broker_disconnected):
        router = DataRouter(kis_broker=fake_broker_disconnected)
        assert router.kis_connected is False

    def test_kis_connected_property_false_when_none(self):
        router = DataRouter(kis_broker=None)
        assert router.kis_connected is False


# ---------------------------------------------------------------------------
# 14. SafetyLimits with custom limits
# ---------------------------------------------------------------------------

class TestSafetyLimitsCustom:
    def test_custom_max_order_pct(self):
        sl = SafetyLimits(max_order_pct=5.0)
        ok, _ = sl.can_order(5.0)
        assert ok is True
        ok, msg = sl.can_order(5.1)
        assert ok is False
        assert "5.1%" in msg

    def test_custom_max_daily_orders(self):
        sl = SafetyLimits(max_daily_orders=3)
        for _ in range(3):
            sl.record_order()
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "3/3" in msg

    def test_custom_loss_limit(self):
        sl = SafetyLimits(daily_loss_limit_pct=-1.0)
        sl.daily_pnl_pct = -1.0
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "-1.0%" in msg

    def test_very_strict_limits(self):
        sl = SafetyLimits(max_order_pct=1.0, max_daily_orders=1, daily_loss_limit_pct=-0.5)
        ok, _ = sl.can_order(0.5)
        assert ok is True
        sl.record_order()
        ok, msg = sl.can_order(0.5)
        assert ok is False
        assert "1/1" in msg

    def test_very_loose_limits(self):
        sl = SafetyLimits(max_order_pct=100.0, max_daily_orders=1000, daily_loss_limit_pct=-50.0)
        sl.daily_pnl_pct = -49.9
        for _ in range(999):
            sl.record_order()
        ok, _ = sl.can_order(99.9)
        assert ok is True


# ---------------------------------------------------------------------------
# 15. Multiple orders in sequence
# ---------------------------------------------------------------------------

class TestMultipleOrdersInSequence:
    def test_ten_orders_all_pass(self, safety):
        """Default limit is 10 daily orders; all 10 should succeed."""
        for i in range(10):
            ok, msg = safety.can_order(10.0)
            assert ok is True, f"Order {i+1} should pass but got: {msg}"
            safety.record_order()
        assert safety.daily_order_count == 10

    def test_eleventh_order_blocked(self, safety):
        for _ in range(10):
            safety.record_order()
        ok, msg = safety.can_order(10.0)
        assert ok is False
        assert "10/10" in msg

    def test_alternating_small_and_large_orders(self, safety):
        """Small orders pass; a large order should be blocked even if count is low."""
        ok, _ = safety.can_order(5.0)
        assert ok is True
        safety.record_order()

        ok, _ = safety.can_order(10.0)
        assert ok is True
        safety.record_order()

        ok, msg = safety.can_order(20.0)
        assert ok is False
        assert "한도 초과" in msg

    def test_loss_accumulation_blocks_mid_sequence(self):
        """Simulate P&L deterioration blocking further orders."""
        sl = SafetyLimits()
        sl.daily_pnl_pct = -1.0
        ok, _ = sl.can_order(5.0)
        assert ok is True
        sl.record_order()

        sl.daily_pnl_pct = -2.5
        ok, _ = sl.can_order(5.0)
        assert ok is True
        sl.record_order()

        sl.daily_pnl_pct = -3.5
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "손실 한도" in msg

    def test_reset_mid_day_re_enables_orders(self, safety):
        """Resetting counters mid-session should re-enable ordering."""
        for _ in range(10):
            safety.record_order()
        safety.daily_pnl_pct = -4.0

        ok, _ = safety.can_order(5.0)
        assert ok is False

        safety.reset_daily()

        ok, msg = safety.can_order(5.0)
        assert ok is True
        assert msg == ""
        assert safety.daily_order_count == 0
        assert safety.daily_pnl_pct == 0.0


# ---------------------------------------------------------------------------
# Edge cases / additional integration scenarios
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_priority_check_order_pct_checked_first(self):
        """When multiple limits are exceeded, the pct check fires first."""
        sl = SafetyLimits()
        sl.daily_order_count = 10
        sl.daily_pnl_pct = -5.0
        ok, msg = sl.can_order(20.0)
        assert ok is False
        # pct is checked first in can_order
        assert "1회 주문 한도 초과" in msg

    def test_priority_check_daily_count_before_loss(self):
        """When daily count and loss are both exceeded but pct is fine,
        the daily-count message should appear."""
        sl = SafetyLimits()
        sl.daily_order_count = 10
        sl.daily_pnl_pct = -5.0
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "일일 주문 횟수 초과" in msg

    def test_data_router_refresh_after_connection_change(self, fake_broker_connected, yf_client):
        """Router detects KIS initially, then falls back after disconnect."""
        router = DataRouter(kis_broker=fake_broker_connected, yf_client=yf_client)
        assert router.source_name == "kis"

        fake_broker_connected.connected = False
        name = router.refresh_source()
        assert name == "yfinance"
        assert router.kis_connected is False

    def test_compute_buy_quantity_default_pct(self, broker):
        """Default pct parameter is 10.0."""
        qty_explicit = broker.compute_buy_quantity(50_000, 10_000_000, pct=10.0)
        qty_default = broker.compute_buy_quantity(50_000, 10_000_000)
        assert qty_explicit == qty_default
