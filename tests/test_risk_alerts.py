from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kstock.bot.mixins.scheduler import _is_kr_live_session, _remember_profit_alert, _should_send_profit_alert
from kstock.bot.smart_alerts import build_holding_alert
from kstock.store.sqlite import SQLiteStore


def test_is_kr_live_session_false_on_weekend():
    sunday = datetime(2026, 3, 15, 11, 0)
    assert _is_kr_live_session(sunday) is False


def test_is_kr_live_session_false_before_open():
    monday_premarket = datetime(2026, 3, 16, 8, 45)
    assert _is_kr_live_session(monday_premarket) is False


def test_profit_alert_repeat_suppressed_without_worsening(tmp_path):
    db = SQLiteStore(Path(tmp_path / "test.db"))
    now = datetime(2026, 3, 16, 13, 45)
    _remember_profit_alert(
        db, ticker="105840", alert_type="stop_loss", pnl_pct=-5.2, now=now,
    )
    should_send = _should_send_profit_alert(
        db,
        ticker="105840",
        alert_type="stop_loss",
        pnl_pct=-5.3,
        now=datetime(2026, 3, 16, 17, 45),
        cooldown_hours=24,
        min_pnl_delta=1.5,
    )
    assert should_send is False


def test_profit_alert_repeat_allowed_when_loss_worsens_materially(tmp_path):
    db = SQLiteStore(Path(tmp_path / "test.db"))
    now = datetime(2026, 3, 16, 13, 45)
    _remember_profit_alert(
        db, ticker="105840", alert_type="stop_loss", pnl_pct=-5.2, now=now,
    )
    should_send = _should_send_profit_alert(
        db,
        ticker="105840",
        alert_type="stop_loss",
        pnl_pct=-7.0,
        now=datetime(2026, 3, 16, 17, 45),
        cooldown_hours=24,
        min_pnl_delta=1.5,
    )
    assert should_send is True


def test_build_holding_alert_softens_near_stop_language():
    msg = build_holding_alert(
        name="우진",
        ticker="105840",
        pnl_pct=-5.2,
        buy_price=27950,
        current_price=26500,
        holding_type="swing",
        hold_days=6,
        market_regime="normal",
    )
    assert msg is not None
    assert "손절선 점검" in msg
    assert "전량" not in msg
    assert "종가 회복 여부" in msg
