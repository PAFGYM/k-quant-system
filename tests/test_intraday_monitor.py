"""Tests for kstock.bot.intraday_monitor module.

Covers: monitor settings, time checks, peak price updates,
target/trailing/volume checks, alert generation, cooldown,
and alert formatting.
"""

import pytest
from datetime import datetime, timedelta, timezone

from kstock.bot.intraday_monitor import (
    MONITOR_SETTINGS,
    MonitoredHolding,
    TradeAlert,
    MonitorState,
    should_check,
    update_peak_price,
    check_target_hit,
    check_max_target,
    check_trailing_stop,
    check_volume_spike,
    generate_alerts,
    is_cooldown,
    format_trade_alert,
    format_urgent_stop_alert,
    get_settings_for_horizon,
)

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# TestMonitorSettings
# ---------------------------------------------------------------------------
class TestMonitorSettings:
    """MONITOR_SETTINGS configuration validation."""

    def test_four_horizons_exist(self):
        assert len(MONITOR_SETTINGS) == 4
        assert set(MONITOR_SETTINGS.keys()) == {"scalp", "swing", "mid", "long"}

    def test_scalp_interval_30(self):
        assert MONITOR_SETTINGS["scalp"]["interval_seconds"] == 30

    def test_swing_interval_300(self):
        assert MONITOR_SETTINGS["swing"]["interval_seconds"] == 300

    def test_long_interval_86400(self):
        assert MONITOR_SETTINGS["long"]["interval_seconds"] == 86400


# ---------------------------------------------------------------------------
# TestShouldCheck
# ---------------------------------------------------------------------------
class TestShouldCheck:
    """should_check determines if enough time has elapsed."""

    def test_enough_time_returns_true(self):
        past = (datetime.now(KST) - timedelta(seconds=600)).isoformat()
        assert should_check(past, 300) is True

    def test_not_enough_time_returns_false(self):
        recent = (datetime.now(KST) - timedelta(seconds=10)).isoformat()
        assert should_check(recent, 300) is False

    def test_empty_time_returns_true(self):
        assert should_check("", 300) is True


# ---------------------------------------------------------------------------
# TestUpdatePeakPrice
# ---------------------------------------------------------------------------
class TestUpdatePeakPrice:
    """update_peak_price returns the higher of current and peak."""

    def test_current_greater_than_peak(self):
        assert update_peak_price(110.0, 100.0) == 110.0

    def test_current_less_than_peak(self):
        assert update_peak_price(90.0, 100.0) == 100.0

    def test_equal_returns_same(self):
        assert update_peak_price(100.0, 100.0) == 100.0


# ---------------------------------------------------------------------------
# TestCheckTargetHit
# ---------------------------------------------------------------------------
class TestCheckTargetHit:
    """check_target_hit detects when profit exceeds target percentage."""

    def test_above_target_returns_true(self):
        # profit = (110 - 100) / 100 = 0.10 >= 0.05
        assert check_target_hit(110.0, 100.0, 0.05) is True

    def test_below_target_returns_false(self):
        # profit = (102 - 100) / 100 = 0.02 < 0.05
        assert check_target_hit(102.0, 100.0, 0.05) is False

    def test_exact_target_returns_true(self):
        # profit = (105 - 100) / 100 = 0.05 >= 0.05
        assert check_target_hit(105.0, 100.0, 0.05) is True


# ---------------------------------------------------------------------------
# TestCheckMaxTarget
# ---------------------------------------------------------------------------
class TestCheckMaxTarget:
    """check_max_target detects when profit exceeds maximum target."""

    def test_above_max_returns_true(self):
        # profit = (120 - 100) / 100 = 0.20 >= 0.15
        assert check_max_target(120.0, 100.0, 0.15) is True

    def test_below_max_returns_false(self):
        # profit = (110 - 100) / 100 = 0.10 < 0.15
        assert check_max_target(110.0, 100.0, 0.15) is False


# ---------------------------------------------------------------------------
# TestCheckTrailingStop
# ---------------------------------------------------------------------------
class TestCheckTrailingStop:
    """check_trailing_stop detects drops from peak exceeding threshold."""

    def test_big_drop_from_peak_returns_true(self):
        # drop = (120 - 108) / 120 = 0.10 >= 0.05
        assert check_trailing_stop(108.0, 120.0, 0.05) is True

    def test_small_drop_returns_false(self):
        # drop = (120 - 119) / 120 = 0.0083 < 0.05
        assert check_trailing_stop(119.0, 120.0, 0.05) is False

    def test_at_boundary(self):
        # drop = (100 - 95) / 100 = 0.05 >= 0.05
        assert check_trailing_stop(95.0, 100.0, 0.05) is True


# ---------------------------------------------------------------------------
# TestCheckVolumeSpike
# ---------------------------------------------------------------------------
class TestCheckVolumeSpike:
    """check_volume_spike detects abnormally high volume."""

    def test_6x_returns_true(self):
        assert check_volume_spike(600000, 100000, 5.0) is True

    def test_3x_returns_false(self):
        assert check_volume_spike(300000, 100000, 5.0) is False


# ---------------------------------------------------------------------------
# TestGenerateAlerts
# ---------------------------------------------------------------------------
class TestGenerateAlerts:
    """generate_alerts creates alerts based on holding state and current price."""

    @staticmethod
    def _make_holding(entry=100000, peak=110000, horizon="swing"):
        return MonitoredHolding(
            ticker="005930", name="삼성전자",
            entry_price=entry, quantity=100,
            horizon=horizon, peak_price=peak,
        )

    def test_target_hit_generates_alert(self):
        holding = self._make_holding(entry=100000, peak=111000)
        # swing target_profit = 0.10 -> need price >= 110000
        alerts = generate_alerts(holding, current_price=111000)
        types = [a.alert_type for a in alerts]
        assert "TARGET_HIT" in types

    def test_trailing_stop_generates_alert(self):
        holding = self._make_holding(entry=100000, peak=120000)
        # swing trailing_stop = 0.05 -> 120000 * 0.95 = 114000
        # price at 113000 -> drop = 7000/120000 = 0.058 >= 0.05
        alerts = generate_alerts(holding, current_price=113000)
        types = [a.alert_type for a in alerts]
        assert "TRAILING_STOP" in types

    def test_no_triggers_returns_empty(self):
        holding = self._make_holding(entry=100000, peak=105000)
        # price 104000: profit 4% < 10% target, drop 1/105 = 0.95% < 5% trailing
        alerts = generate_alerts(holding, current_price=104000)
        assert alerts == []

    def test_volume_spike_generates_alert(self):
        holding = self._make_holding(entry=100000, peak=105000)
        alerts = generate_alerts(
            holding, current_price=104000,
            current_volume=600000, avg_volume=100000,
        )
        types = [a.alert_type for a in alerts]
        assert "VOLUME_SPIKE" in types


# ---------------------------------------------------------------------------
# TestIsCooldown
# ---------------------------------------------------------------------------
class TestIsCooldown:
    """is_cooldown prevents duplicate alerts within cooldown period."""

    def test_recent_alert_returns_true(self):
        recent = (datetime.now(KST) - timedelta(seconds=30)).isoformat()
        last_alerts = {"005930": {"TARGET_HIT": recent}}
        assert is_cooldown(last_alerts, "005930", "TARGET_HIT", 600) is True

    def test_old_alert_returns_false(self):
        old = (datetime.now(KST) - timedelta(seconds=3600)).isoformat()
        last_alerts = {"005930": {"TARGET_HIT": old}}
        assert is_cooldown(last_alerts, "005930", "TARGET_HIT", 600) is False


# ---------------------------------------------------------------------------
# TestFormatTradeAlert
# ---------------------------------------------------------------------------
class TestFormatTradeAlert:
    """format_trade_alert produces Telegram-safe text."""

    @staticmethod
    def _make_alert(alert_type="TARGET_HIT"):
        return TradeAlert(
            ticker="005930", name="삼성전자",
            alert_type=alert_type,
            message="삼성전자 목표 수익 +10.5% 도달!",
            action="부분 익절 또는 트레일링 스탑 전환 권장",
            current_price=110500, entry_price=100000,
            profit_pct=0.105, severity="warning",
        )

    @staticmethod
    def _make_holding():
        return MonitoredHolding(
            ticker="005930", name="삼성전자",
            entry_price=100000, quantity=100,
            horizon="swing", peak_price=112000,
        )

    def test_no_bold_markers(self):
        text = format_trade_alert(self._make_alert(), self._make_holding())
        assert "**" not in text

    def test_contains_user_name(self):
        text = format_trade_alert(self._make_alert(), self._make_holding())
        assert "주호님" in text

    def test_contains_alert_type_info(self):
        text = format_trade_alert(self._make_alert("TARGET_HIT"), self._make_holding())
        assert "목표 도달" in text or "목표" in text


# ---------------------------------------------------------------------------
# TestFormatUrgentStopAlert
# ---------------------------------------------------------------------------
class TestFormatUrgentStopAlert:
    """format_urgent_stop_alert produces emphasized stop loss text."""

    @staticmethod
    def _make_stop_alert():
        return TradeAlert(
            ticker="005930", name="삼성전자",
            alert_type="TRAILING_STOP",
            message="삼성전자 고점 대비 -6.0% 하락! 트레일링 스탑 발동",
            action="즉시 매도 검토",
            current_price=105000, entry_price=100000,
            profit_pct=0.05, severity="critical",
        )

    @staticmethod
    def _make_holding():
        return MonitoredHolding(
            ticker="005930", name="삼성전자",
            entry_price=100000, quantity=100,
            horizon="swing", peak_price=112000,
        )

    def test_no_bold_markers(self):
        text = format_urgent_stop_alert(self._make_stop_alert(), self._make_holding())
        assert "**" not in text

    def test_contains_urgent_or_stop(self):
        text = format_urgent_stop_alert(self._make_stop_alert(), self._make_holding())
        assert "긴급" in text or "스탑" in text or "TRAILING STOP" in text


# ---------------------------------------------------------------------------
# TestGetSettingsForHorizon
# ---------------------------------------------------------------------------
class TestGetSettingsForHorizon:
    """get_settings_for_horizon returns correct settings or swing default."""

    def test_valid_horizon_returns_correct(self):
        settings = get_settings_for_horizon("scalp")
        assert settings["interval_seconds"] == 30
        assert settings["trailing_stop"] == 0.03

    def test_unknown_returns_swing_default(self):
        settings = get_settings_for_horizon("unknown_horizon")
        assert settings["interval_seconds"] == 300
        assert settings["trailing_stop"] == 0.05
