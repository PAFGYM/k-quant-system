"""Tests for KIS broker module."""

from __future__ import annotations

import pytest

from kstock.broker.kis_broker import (
    KisBroker,
    OrderResult,
    SafetyLimits,
    format_kis_setup_guide,
    format_kis_status,
)


# ---------------------------------------------------------------------------
# OrderResult
# ---------------------------------------------------------------------------

class TestOrderResult:
    def test_default_values(self):
        result = OrderResult(success=True)
        assert result.success is True
        assert result.order_id == ""
        assert result.message == ""
        assert result.ticker == ""
        assert result.quantity == 0
        assert result.price == 0
        assert result.order_type == ""

    def test_failure_with_message(self):
        result = OrderResult(success=False, message="KIS 미연결")
        assert result.success is False
        assert result.message == "KIS 미연결"


# ---------------------------------------------------------------------------
# SafetyLimits
# ---------------------------------------------------------------------------

class TestSafetyLimits:
    def test_defaults(self):
        sl = SafetyLimits()
        assert sl.max_order_pct == 15.0
        assert sl.max_daily_orders == 10
        assert sl.daily_loss_limit_pct == -3.0
        assert sl.require_confirmation is True
        assert sl.daily_order_count == 0
        assert sl.daily_pnl_pct == 0.0

    def test_can_order_allows_normal(self):
        sl = SafetyLimits()
        ok, msg = sl.can_order(10.0)
        assert ok is True
        assert msg == ""

    def test_can_order_blocks_exceeding_max_order_pct(self):
        sl = SafetyLimits(max_order_pct=15.0)
        ok, msg = sl.can_order(20.0)
        assert ok is False
        assert "1회 주문 한도 초과" in msg
        assert "20.0%" in msg

    def test_can_order_allows_at_exact_max_order_pct(self):
        sl = SafetyLimits(max_order_pct=15.0)
        ok, msg = sl.can_order(15.0)
        assert ok is True
        assert msg == ""

    def test_can_order_blocks_exceeding_max_daily_orders(self):
        sl = SafetyLimits(max_daily_orders=10)
        sl.daily_order_count = 10
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "일일 주문 횟수 초과" in msg

    def test_can_order_blocks_when_daily_loss_limit_reached(self):
        sl = SafetyLimits(daily_loss_limit_pct=-3.0)
        sl.daily_pnl_pct = -3.0
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "일일 손실 한도 도달" in msg

    def test_can_order_blocks_when_daily_loss_exceeds_limit(self):
        sl = SafetyLimits(daily_loss_limit_pct=-3.0)
        sl.daily_pnl_pct = -5.0
        ok, msg = sl.can_order(5.0)
        assert ok is False
        assert "일일 손실 한도 도달" in msg

    def test_record_order_increments_counter(self):
        sl = SafetyLimits()
        assert sl.daily_order_count == 0
        sl.record_order()
        assert sl.daily_order_count == 1
        sl.record_order()
        assert sl.daily_order_count == 2

    def test_reset_daily_resets_counters(self):
        sl = SafetyLimits()
        sl.daily_order_count = 7
        sl.daily_pnl_pct = -1.5
        sl.reset_daily()
        assert sl.daily_order_count == 0
        assert sl.daily_pnl_pct == 0.0


# ---------------------------------------------------------------------------
# KisBroker (no actual pykis connection)
# ---------------------------------------------------------------------------

class TestKisBrokerNoConnection:
    def test_init_without_config_file(self, tmp_path):
        """Broker initialised with a non-existent config stays disconnected."""
        broker = KisBroker(config_path=str(tmp_path / "nonexistent.yaml"))
        assert broker.connected is False
        assert broker.kis is None
        assert broker.mode == "virtual"

    def test_get_balance_returns_none_when_not_connected(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert broker.get_balance() is None

    def test_get_realtime_price_returns_zero_when_not_connected(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert broker.get_realtime_price("005930") == 0.0

    def test_buy_returns_failure_when_not_connected(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        result = broker.buy("005930", 10)
        assert result.success is False
        assert "미연결" in result.message

    def test_sell_returns_failure_when_not_connected(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        result = broker.sell("005930", 5)
        assert result.success is False
        assert "미연결" in result.message

    def test_safety_limits_present(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert isinstance(broker.safety, SafetyLimits)


# ---------------------------------------------------------------------------
# KisBroker.compute_buy_quantity
# ---------------------------------------------------------------------------

class TestComputeBuyQuantity:
    def test_normal_calculation(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        # 10% of 10_000_000 = 1_000_000; 1_000_000 // 58_000 = 17
        qty = broker.compute_buy_quantity(58_000, 10_000_000, pct=10.0)
        assert qty == 17

    def test_with_zero_price(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert broker.compute_buy_quantity(0, 10_000_000) == 0

    def test_with_zero_total(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert broker.compute_buy_quantity(58_000, 0) == 0

    def test_with_negative_price(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        assert broker.compute_buy_quantity(-100, 10_000_000) == 0

    def test_fractional_quantity_truncated(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        # 10% of 1_000_000 = 100_000; 100_000 // 30_000 = 3 (remainder dropped)
        qty = broker.compute_buy_quantity(30_000, 1_000_000, pct=10.0)
        assert qty == 3
        assert isinstance(qty, int)


# ---------------------------------------------------------------------------
# format_kis_setup_guide
# ---------------------------------------------------------------------------

class TestFormatKisSetupGuide:
    def test_returns_nonempty_string(self):
        guide = format_kis_setup_guide()
        assert isinstance(guide, str)
        assert len(guide) > 0

    def test_contains_setup_steps(self):
        guide = format_kis_setup_guide()
        assert "1단계" in guide
        assert "2단계" in guide
        assert "3단계" in guide
        assert "4단계" in guide
        assert "5단계" in guide


# ---------------------------------------------------------------------------
# format_kis_status
# ---------------------------------------------------------------------------

class TestFormatKisStatus:
    def test_not_connected(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        status = format_kis_status(broker)
        assert "미연결" in status
        assert "/setup_kis" in status

    def test_connected_virtual(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        # Simulate a connected broker without requiring pykis
        broker.connected = True
        broker.mode = "virtual"
        broker.safety.max_order_pct = 15.0
        broker.safety.max_daily_orders = 10
        broker.safety.daily_loss_limit_pct = -3.0
        broker.safety.daily_order_count = 3

        status = format_kis_status(broker)
        assert "모의투자" in status
        assert "15%" in status
        assert "10회" in status
        assert "-3%" in status
        assert "3회" in status

    def test_connected_real(self, tmp_path):
        broker = KisBroker(config_path=str(tmp_path / "no.yaml"))
        broker.connected = True
        broker.mode = "real"

        status = format_kis_status(broker)
        assert "실전" in status
