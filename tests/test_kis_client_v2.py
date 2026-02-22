"""Tests for kstock.core.kis_client module (v2 extended coverage)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from kstock.core.kis_client import (
    RATE_LIMITS,
    SAFETY,
    KisBalance,
    KisConfig,
    KisOrderResult,
    KisPrice,
    KisSafety,
    check_rate_limit,
    compute_order_amount,
    format_kis_balance,
    format_kis_not_configured,
    format_kis_price,
    format_order_confirmation,
    format_order_result,
    get_default_safety,
    load_kis_config,
    validate_order,
)


# ---------------------------------------------------------------------------
# TestSafety
# ---------------------------------------------------------------------------
class TestSafety:
    def test_require_confirmation_true(self):
        assert SAFETY["require_confirmation"] is True

    def test_auto_trade_false(self):
        assert SAFETY["auto_trade"] is False

    def test_max_daily_order_pct(self):
        assert SAFETY["max_daily_order_pct"] == 0.20

    def test_max_single_order_pct(self):
        assert SAFETY["max_single_order_pct"] == 0.10


# ---------------------------------------------------------------------------
# TestRateLimits
# ---------------------------------------------------------------------------
class TestRateLimits:
    def test_rest_per_second(self):
        assert RATE_LIMITS["rest_per_second"] == 20

    def test_token_per_minute(self):
        assert RATE_LIMITS["token_per_minute"] == 1

    def test_websocket_max_stocks(self):
        assert RATE_LIMITS["websocket_max_stocks"] == 40


# ---------------------------------------------------------------------------
# TestKisConfig
# ---------------------------------------------------------------------------
class TestKisConfig:
    def test_default_not_configured(self):
        cfg = KisConfig()
        assert cfg.is_configured is False

    def test_fields_present(self):
        cfg = KisConfig(
            app_key="test_key",
            app_secret="test_secret",
            account_no="12345678-01",
            is_configured=True,
        )
        assert cfg.app_key == "test_key"
        assert cfg.app_secret == "test_secret"
        assert cfg.account_no == "12345678-01"

    def test_is_virtual_default(self):
        cfg = KisConfig()
        assert cfg.is_virtual is True


# ---------------------------------------------------------------------------
# TestLoadKisConfig
# ---------------------------------------------------------------------------
class TestLoadKisConfig:
    def test_no_env_vars_not_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = load_kis_config()
            assert cfg.is_configured is False

    def test_loads_from_env(self):
        env = {
            "KIS_APP_KEY": "myAppKey123",
            "KIS_APP_SECRET": "mySecret456",
            "KIS_ACCOUNT_NO": "50123456-01",
            "KIS_HTS_ID": "myHtsId",
            "KIS_IS_VIRTUAL": "true",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = load_kis_config()
            assert cfg.is_configured is True
            assert cfg.app_key == "myAppKey123"
            assert cfg.is_virtual is True


# ---------------------------------------------------------------------------
# TestValidateOrder
# ---------------------------------------------------------------------------
class TestValidateOrder:
    def test_normal_order_valid(self):
        ok, msg = validate_order(
            ticker="005930",
            quantity=10,
            price=70000,
            total_assets=100_000_000,
            daily_ordered=0,
        )
        # 10 * 70000 = 700,000 -> single pct 0.7%, daily pct 0.7%
        assert ok is True
        assert msg == ""

    def test_exceeds_single_order_limit(self):
        ok, msg = validate_order(
            ticker="005930",
            quantity=200,
            price=70000,
            total_assets=100_000_000,
            daily_ordered=0,
        )
        # 200 * 70000 = 14,000,000 -> 14% > 10% limit
        assert ok is False
        assert "단일 주문 비중" in msg

    def test_exceeds_daily_limit(self):
        ok, msg = validate_order(
            ticker="005930",
            quantity=10,
            price=70000,
            total_assets=100_000_000,
            daily_ordered=19_500_000,
        )
        # daily_ordered + 700,000 = 20,200,000 -> 20.2% > 20% limit
        assert ok is False
        assert "누적 주문 비중" in msg

    def test_zero_quantity_fails(self):
        # Note: validate_order checks single/daily pct FIRST, then quantity.
        # With quantity=0 the order_amount is 0, so pct checks pass, then quantity check fails.
        ok, msg = validate_order(
            ticker="005930",
            quantity=0,
            price=70000,
            total_assets=100_000_000,
            daily_ordered=0,
        )
        assert ok is False
        assert "수량" in msg

    def test_returns_tuple(self):
        result = validate_order(
            ticker="005930",
            quantity=1,
            price=70000,
            total_assets=100_000_000,
            daily_ordered=0,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


# ---------------------------------------------------------------------------
# TestFormatKisPrice
# ---------------------------------------------------------------------------
class TestFormatKisPrice:
    def test_no_bold(self):
        price = KisPrice(
            ticker="005930",
            price=72000,
            change=1500,
            change_pct=2.13,
            volume=15_000_000,
            high=73000,
            low=70500,
            open_price=71000,
        )
        msg = format_kis_price(price)
        assert "**" not in msg

    def test_contains_price_info(self):
        price = KisPrice(
            ticker="005930",
            price=72000,
            change=1500,
            change_pct=2.13,
            volume=15_000_000,
            high=73000,
            low=70500,
            open_price=71000,
        )
        msg = format_kis_price(price)
        assert "72,000" in msg
        assert "005930" in msg


# ---------------------------------------------------------------------------
# TestFormatKisBalance
# ---------------------------------------------------------------------------
class TestFormatKisBalance:
    def test_no_bold(self):
        balance = KisBalance(
            cash=5_000_000,
            total_eval=25_000_000,
            total_pnl=1_200_000,
            total_pnl_rate=5.04,
            holdings=[],
        )
        msg = format_kis_balance(balance)
        assert "**" not in msg

    def test_contains_balance_keywords(self):
        balance = KisBalance(
            cash=5_000_000,
            total_eval=25_000_000,
            total_pnl=1_200_000,
            total_pnl_rate=5.04,
            holdings=[],
        )
        msg = format_kis_balance(balance)
        # Should contain either 잔고 or 평가
        assert "잔고" in msg or "평가" in msg


# ---------------------------------------------------------------------------
# TestFormatOrderConfirmation
# ---------------------------------------------------------------------------
class TestFormatOrderConfirmation:
    def test_no_bold(self):
        msg = format_order_confirmation(
            ticker="005930",
            name="삼성전자",
            quantity=10,
            price=72000,
            order_type="buy",
        )
        assert "**" not in msg

    def test_contains_juhonim_or_confirmation(self):
        msg = format_order_confirmation(
            ticker="005930",
            name="삼성전자",
            quantity=10,
            price=72000,
            order_type="buy",
        )
        assert "주호님" in msg or "확인" in msg


# ---------------------------------------------------------------------------
# TestFormatOrderResult
# ---------------------------------------------------------------------------
class TestFormatOrderResult:
    def test_success_message(self):
        result = KisOrderResult(
            success=True,
            order_id="ORD20250101001",
            ticker="005930",
            name="삼성전자",
            quantity=10,
            price=72000,
            message="",
        )
        msg = format_order_result(result)
        assert "정상 접수" in msg or "주문" in msg
        assert "삼성전자" in msg

    def test_failure_message(self):
        result = KisOrderResult(
            success=False,
            order_id="",
            ticker="005930",
            name="삼성전자",
            quantity=10,
            price=72000,
            message="잔고 부족",
        )
        msg = format_order_result(result)
        assert "실패" in msg
        assert "잔고 부족" in msg


# ---------------------------------------------------------------------------
# TestFormatKisNotConfigured
# ---------------------------------------------------------------------------
class TestFormatKisNotConfigured:
    def test_no_bold(self):
        msg = format_kis_not_configured()
        assert "**" not in msg

    def test_contains_juhonim_and_api(self):
        msg = format_kis_not_configured()
        assert "주호님" in msg
        assert "API" in msg


# ---------------------------------------------------------------------------
# TestCheckRateLimit
# ---------------------------------------------------------------------------
class TestCheckRateLimit:
    def test_within_limit_true(self):
        # Within 1 second, only 5 requests (well below 20)
        # window_start must be slightly in the past so elapsed > 0
        now = time.time() - 0.5
        assert check_rate_limit(5, now) is True

    def test_exceeded_false(self):
        # Within 1 second, 25 requests (over 20 limit)
        now = time.time()
        assert check_rate_limit(25, now) is False


# ---------------------------------------------------------------------------
# TestComputeOrderAmount
# ---------------------------------------------------------------------------
class TestComputeOrderAmount:
    def test_basic_multiplication(self):
        assert compute_order_amount(70000, 10) == 700_000.0

    def test_zero_price(self):
        assert compute_order_amount(0, 100) == 0.0


# ---------------------------------------------------------------------------
# TestGetDefaultSafety
# ---------------------------------------------------------------------------
class TestGetDefaultSafety:
    def test_returns_kis_safety(self):
        safety = get_default_safety()
        assert isinstance(safety, KisSafety)

    def test_confirmation_required(self):
        safety = get_default_safety()
        assert safety.require_confirmation is True
        assert safety.auto_trade is False
        assert safety.max_daily_order_pct == 0.20
        assert safety.max_single_order_pct == 0.10
