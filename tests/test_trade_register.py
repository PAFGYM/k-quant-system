"""Tests for kstock.bot.trade_register module.

Covers: horizon settings, text parsing, image OCR parsing,
trade validation, monitoring params, formatting, and registration.
"""

import pytest

from kstock.bot.trade_register import (
    HORIZON_SETTINGS,
    TradeInfo,
    RegisteredTrade,
    parse_trade_text,
    parse_trade_image_result,
    validate_trade_info,
    compute_monitoring_params,
    format_trade_confirmation,
    format_registered_trade,
    format_trade_image_preview,
    create_registered_trade,
)


# ---------------------------------------------------------------------------
# TestHorizonSettings
# ---------------------------------------------------------------------------
class TestHorizonSettings:
    """HORIZON_SETTINGS configuration validation."""

    def test_four_horizons_exist(self):
        assert len(HORIZON_SETTINGS) == 4
        assert set(HORIZON_SETTINGS.keys()) == {"scalp", "swing", "mid", "long"}

    def test_scalp_trailing_stop(self):
        assert HORIZON_SETTINGS["scalp"]["trailing_stop"] == 0.03

    def test_swing_trailing_stop(self):
        assert HORIZON_SETTINGS["swing"]["trailing_stop"] == 0.05

    def test_long_trailing_stop(self):
        assert HORIZON_SETTINGS["long"]["trailing_stop"] == 0.15


# ---------------------------------------------------------------------------
# TestParseTradeText
# ---------------------------------------------------------------------------
class TestParseTradeText:
    """parse_trade_text extracts TradeInfo from Korean trade messages."""

    def test_full_message_name_qty_price(self):
        result = parse_trade_text("에코프로 100주 178500원에 샀어")
        assert result is not None
        assert result.name == "에코프로"
        assert result.quantity == 100
        assert result.price == 178500.0

    def test_name_qty_buy_no_price(self):
        result = parse_trade_text("삼성전자 50주 매수")
        assert result is not None
        assert result.quantity == 50
        assert result.price == 0.0

    def test_ticker_code_qty_price(self):
        result = parse_trade_text("247540 30주 85000")
        assert result is not None
        assert result.ticker == "247540"
        assert result.quantity == 30
        assert result.price == 85000.0

    def test_name_qty_bought(self):
        # "에코프로 100주 샀어" -> pattern 5 (name, qty, no price)
        result = parse_trade_text("에코프로 100주 샀어")
        assert result is not None
        assert result.name == "에코프로"
        assert result.quantity == 100

    def test_gibberish_returns_none(self):
        result = parse_trade_text("오늘 날씨 좋다")
        assert result is None

    def test_empty_returns_none(self):
        result = parse_trade_text("")
        assert result is None


# ---------------------------------------------------------------------------
# TestParseTradeImageResult
# ---------------------------------------------------------------------------
class TestParseTradeImageResult:
    """parse_trade_image_result converts OCR JSON to TradeInfo list."""

    def test_valid_json_list_parsed(self):
        ocr = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 10, "price": 70000},
            {"ticker": "086520", "name": "에코프로", "quantity": 50, "price": 178500},
        ]
        result = parse_trade_image_result(ocr)
        assert len(result) == 2
        assert result[0].ticker == "005930"
        assert result[1].quantity == 50

    def test_nulls_filtered(self):
        ocr = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 10, "price": 70000},
            {"ticker": None, "name": None, "quantity": 0, "price": 0},  # invalid: no name/ticker
            {"ticker": "", "name": "", "quantity": 5, "price": 50000},  # invalid: no name/ticker
        ]
        result = parse_trade_image_result(ocr)
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        assert parse_trade_image_result([]) == []


# ---------------------------------------------------------------------------
# TestValidateTradeInfo
# ---------------------------------------------------------------------------
class TestValidateTradeInfo:
    """validate_trade_info checks trade data validity."""

    def test_valid_trade_passes(self):
        trade = TradeInfo(name="삼성전자", quantity=10, price=70000)
        is_valid, msg = validate_trade_info(trade)
        assert is_valid is True
        assert msg == ""

    def test_zero_quantity_fails(self):
        trade = TradeInfo(name="삼성전자", quantity=0, price=70000)
        is_valid, msg = validate_trade_info(trade)
        assert is_valid is False
        assert "수량" in msg

    def test_no_name_no_ticker_fails(self):
        trade = TradeInfo(name="", ticker="", quantity=10, price=70000)
        is_valid, msg = validate_trade_info(trade)
        assert is_valid is False
        assert "종목" in msg


# ---------------------------------------------------------------------------
# TestComputeMonitoringParams
# ---------------------------------------------------------------------------
class TestComputeMonitoringParams:
    """compute_monitoring_params returns correct prices and intervals."""

    def test_swing_params(self):
        params = compute_monitoring_params(100000.0, "swing")
        # trailing: 100000 * (1 - 0.05) = 95000
        assert params["trailing_stop_price"] == 95000.0
        # target: 100000 * (1 + 0.10) = 110000
        assert params["target_price"] == 110000.0
        # max: 100000 * (1 + 0.15) = 115000
        assert params["max_target_price"] == 115000.0
        assert params["check_interval_seconds"] == 300

    def test_scalp_params(self):
        params = compute_monitoring_params(50000.0, "scalp")
        # trailing: 50000 * (1 - 0.03) = 48500
        assert params["trailing_stop_price"] == 48500.0
        # target: 50000 * (1 + 0.05) = 52500
        assert params["target_price"] == 52500.0
        # max: 50000 * (1 + 0.08) = 54000
        assert params["max_target_price"] == 54000.0
        assert params["check_interval_seconds"] == 30


# ---------------------------------------------------------------------------
# TestFormatTradeConfirmation
# ---------------------------------------------------------------------------
class TestFormatTradeConfirmation:
    """format_trade_confirmation produces user-facing confirmation message."""

    def test_no_bold_markers(self):
        trade = TradeInfo(name="에코프로", ticker="086520", quantity=100, price=178500)
        text = format_trade_confirmation(trade)
        assert "**" not in text

    def test_contains_trade_info(self):
        trade = TradeInfo(name="에코프로", ticker="086520", quantity=100, price=178500)
        text = format_trade_confirmation(trade)
        assert "에코프로" in text
        assert "100" in text
        assert "178,500" in text
        assert "주호님" in text


# ---------------------------------------------------------------------------
# TestFormatRegisteredTrade (bonus - format_registered_trade)
# ---------------------------------------------------------------------------
class TestFormatRegisteredTrade:
    """format_registered_trade produces a registration complete message."""

    def test_no_bold_markers(self):
        trade = TradeInfo(name="삼성전자", ticker="005930", quantity=10, price=70000, total_amount=700000)
        reg = RegisteredTrade(
            trade_info=trade, horizon="swing",
            trailing_stop_pct=0.05, target_profit_pct=0.10,
            registered_at="2026-02-23 10:00:00 KST",
        )
        text = format_registered_trade(reg)
        assert "**" not in text

    def test_contains_monitoring_info(self):
        trade = TradeInfo(name="삼성전자", ticker="005930", quantity=10, price=70000, total_amount=700000)
        reg = RegisteredTrade(
            trade_info=trade, horizon="swing",
            trailing_stop_pct=0.05, target_profit_pct=0.10,
            registered_at="2026-02-23 10:00:00 KST",
        )
        text = format_registered_trade(reg)
        assert "트레일링 스탑" in text
        assert "주호님" in text


# ---------------------------------------------------------------------------
# TestCreateRegisteredTrade
# ---------------------------------------------------------------------------
class TestCreateRegisteredTrade:
    """create_registered_trade constructs a RegisteredTrade with correct params."""

    def test_sets_correct_horizon_params(self):
        trade = TradeInfo(name="에코프로", quantity=100, price=178500)
        reg = create_registered_trade(trade, "swing")
        assert reg.horizon == "swing"
        assert reg.trailing_stop_pct == 0.05
        assert reg.target_profit_pct == 0.10

    def test_trailing_stop_computed_for_scalp(self):
        trade = TradeInfo(name="에코프로", quantity=100, price=178500)
        reg = create_registered_trade(trade, "scalp")
        assert reg.trailing_stop_pct == 0.03
        assert reg.target_profit_pct == 0.05
