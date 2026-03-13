"""E2E tests for realtime WebSocket monitoring (SchedulerMixin methods).

Covers: _on_realtime_update, _check_sell_targets, _send_surge_alert,
        _send_sell_guide, job_scalp_close_reminder, job_short_term_review.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Correct module path
_MIXIN_MODULE = "kstock.bot.mixins.scheduler"


def _get_mixin_class():
    import importlib
    mod = importlib.import_module(_MIXIN_MODULE)
    return mod.SchedulerMixin


def _make_data(*, change_pct=1.0, price=10000, pressure="중립"):
    return SimpleNamespace(change_pct=change_pct, price=price, pressure=pressure)


# ===========================================================================
# 1. Surge detection
# ===========================================================================

class TestOnRealtimeUpdateSurge:
    """change_pct >= 3.0 during market hours triggers _send_surge_alert."""

    def test_on_realtime_update_surge(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj._SURGE_THRESHOLD_PCT = 3.0
        obj._SURGE_COOLDOWN_SEC = 1800
        obj._SELL_TARGET_COOLDOWN_SEC = 3600
        obj._surge_cooldown = {}
        obj._holdings_index = {}

        data = _make_data(change_pct=3.5, price=11000)

        # Mock market hours (10:30 KST)
        mock_now = MagicMock()
        mock_now.hour = 10
        mock_now.minute = 30

        with patch(f"{_MIXIN_MODULE}.datetime") as mock_dt, \
             patch(f"{_MIXIN_MODULE}._time") as mock_time, \
             patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("asyncio.ensure_future") as mock_ensure:
            mock_dt.now.return_value = mock_now
            mock_time.time.return_value = 100000.0  # Well past any cooldown

            Mixin._on_realtime_update(obj, "price", "005930", data)

        # _send_surge_alert should have been called via ensure_future
        assert mock_ensure.called or obj._send_surge_alert.called


class TestOnRealtimeUpdateCooldown:
    """Second surge within 30 min is suppressed."""

    def test_on_realtime_update_cooldown(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj._SURGE_THRESHOLD_PCT = 3.0
        obj._SURGE_COOLDOWN_SEC = 1800
        obj._SELL_TARGET_COOLDOWN_SEC = 3600
        # Already alerted 10 minutes ago
        obj._surge_cooldown = {"surge:005930": 990.0}
        obj._holdings_index = {}

        data = _make_data(change_pct=4.0, price=11000)
        mock_now = MagicMock()
        mock_now.hour = 10
        mock_now.minute = 30

        with patch(f"{_MIXIN_MODULE}.datetime") as mock_dt, \
             patch(f"{_MIXIN_MODULE}._time") as mock_time, \
             patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("asyncio.ensure_future") as mock_ensure:
            mock_dt.now.return_value = mock_now
            mock_time.time.return_value = 1000.0  # Only 10s after cooldown set

            Mixin._on_realtime_update(obj, "price", "005930", data)

        # Should NOT have triggered because cooldown hasn't expired
        mock_ensure.assert_not_called()


# ===========================================================================
# 2. Sell target / stop loss
# ===========================================================================

class TestSellTargetReached:
    """Swing holding at +5.1% triggers target alert."""

    def test_sell_target_reached(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj._SELL_TARGET_COOLDOWN_SEC = 3600
        obj._surge_cooldown = {}
        obj._muted_tickers = {}
        obj._holdings_index = {
            "005930": {
                "ticker": "005930",
                "name": "Samsung",
                "buy_price": 10000,
                "holding_type": "swing",
            }
        }
        obj.__init_scheduler_state__ = MagicMock()

        data = _make_data(price=10510)  # +5.1%
        now = _time.time()

        with patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("asyncio.ensure_future") as mock_ensure:
            Mixin._check_sell_targets(obj, "005930", data, now, None)

        # _send_sell_guide should have been scheduled
        assert mock_ensure.called


class TestStopLossReached:
    """Swing holding at -3.1% triggers stop alert."""

    def test_stop_loss_reached(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj._SELL_TARGET_COOLDOWN_SEC = 3600
        obj._surge_cooldown = {}
        obj._muted_tickers = {}
        obj._holdings_index = {
            "005930": {
                "ticker": "005930",
                "name": "Samsung",
                "buy_price": 10000,
                "holding_type": "swing",
            }
        }
        obj.__init_scheduler_state__ = MagicMock()

        data = _make_data(price=9690)  # -3.1%
        now = _time.time()

        with patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("asyncio.ensure_future") as mock_ensure:
            Mixin._check_sell_targets(obj, "005930", data, now, None)

        assert mock_ensure.called


# ===========================================================================
# 3. Scalp close reminder (14:30)
# ===========================================================================

class TestScalpCloseReminder:
    @pytest.mark.asyncio
    async def test_scalp_close_reminder(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj.chat_id = 12345
        obj.db = MagicMock()
        obj.db.get_active_holdings.return_value = [
            {
                "ticker": "005930", "name": "Samsung",
                "buy_price": 10000, "holding_type": "scalp",
            },
        ]
        ws_price = MagicMock()
        ws_price.price = 10500
        obj.ws = MagicMock()
        obj.ws.is_connected = True
        obj.ws.get_price.return_value = ws_price

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await Mixin.job_scalp_close_reminder(obj, context)

        context.bot.send_message.assert_called_once()
        text = context.bot.send_message.call_args.kwargs["text"]
        assert "Samsung" in text or "초단기" in text


# ===========================================================================
# 4. Short-term review (3+ days, < 3%)
# ===========================================================================

class TestSwingReview3Days:
    @pytest.mark.asyncio
    async def test_swing_review_3days(self):
        Mixin = _get_mixin_class()

        four_days_ago = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        obj = MagicMock()
        obj.chat_id = 12345
        obj.db = MagicMock()
        obj.db.get_active_holdings.return_value = [
            {
                "ticker": "005930", "name": "Samsung",
                "buy_price": 10000, "holding_type": "swing",
                "buy_date": four_days_ago,
                "created_at": four_days_ago,
            },
        ]
        ws_price = MagicMock()
        ws_price.price = 10200  # +2% < 3%
        obj.ws = MagicMock()
        obj.ws.is_connected = True
        obj.ws.get_price.return_value = ws_price

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await Mixin.job_short_term_review(obj, context)

        context.bot.send_message.assert_called_once()
        text = context.bot.send_message.call_args.kwargs["text"]
        assert "Samsung" in text or "단기" in text


class TestWsConnectWindow:
    @pytest.mark.asyncio
    async def test_ws_connect_skips_outside_session(self):
        Mixin = _get_mixin_class()

        obj = MagicMock()
        obj.ws = MagicMock()
        obj.ws.is_connected = False

        mock_now = MagicMock()
        mock_now.hour = 1
        mock_now.minute = 5
        mock_now.date.return_value = datetime.now().date()

        with patch(f"{_MIXIN_MODULE}.datetime") as mock_dt, \
             patch(f"{_MIXIN_MODULE}.is_kr_market_open", return_value=True):
            mock_dt.now.return_value = mock_now
            await Mixin.job_ws_connect(obj, MagicMock())

        obj.ws.connect.assert_not_called()


class TestRealtimeTickerNameResolution:
    @pytest.mark.asyncio
    async def test_resolve_ticker_name_async_uses_data_router_for_unknown_ticker(self):
        Mixin = _get_mixin_class()

        obj = Mixin.__new__(Mixin)
        obj._ticker_name_cache = {}
        obj._holdings_index = {}
        obj.all_tickers = []
        obj.db = MagicMock()
        obj.db.get_holding_by_ticker.return_value = None
        obj.db.get_watchlist.return_value = []
        obj.data_router = MagicMock()
        obj.data_router.get_stock_info = AsyncMock(
            return_value={"ticker": "039200", "name": "오스코텍", "current_price": 58300},
        )

        name = await Mixin._resolve_ticker_name_async(obj, "039200", "039200")

        assert name == "오스코텍"
        assert obj._ticker_name_cache["039200"] == "오스코텍"

    @pytest.mark.asyncio
    async def test_resolve_ticker_name_async_falls_back_to_naver_when_router_returns_code(self):
        Mixin = _get_mixin_class()

        obj = Mixin.__new__(Mixin)
        obj._ticker_name_cache = {}
        obj._holdings_index = {}
        obj.all_tickers = []
        obj.db = MagicMock()
        obj.db.get_holding_by_ticker.return_value = None
        obj.db.get_watchlist.return_value = []
        obj.data_router = MagicMock()
        obj.data_router.get_stock_info = AsyncMock(
            return_value={"ticker": "067160", "name": "067160", "current_price": 65000},
        )

        with patch(
            "kstock.ingest.naver_finance.NaverFinanceClient.get_stock_info",
            new=AsyncMock(return_value={"ticker": "067160", "name": "SOOP", "current_price": 65000}),
        ):
            name = await Mixin._resolve_ticker_name_async(obj, "067160", "067160")

        assert name == "SOOP"
        assert obj._ticker_name_cache["067160"] == "SOOP"

    @pytest.mark.asyncio
    async def test_send_surge_alert_uses_resolved_name_and_short_favorite_callback(self):
        Mixin = _get_mixin_class()

        obj = Mixin.__new__(Mixin)
        obj.chat_id = 12345
        obj.db = MagicMock()
        obj.db.has_recent_alert.return_value = False
        obj.db.insert_alert = MagicMock()
        obj._allow_alert_emit = AsyncMock(return_value=True)
        obj._resolve_ticker_name_async = AsyncMock(return_value="오스코텍")
        obj._holdings_index = {}
        obj._last_scan_results = []
        obj._application = MagicMock()
        obj._application.bot.send_message = AsyncMock()

        await Mixin._send_surge_alert(obj, "039200", _make_data(change_pct=21.3, price=58300, pressure="강한 매도세"))

        kwargs = obj._application.bot.send_message.call_args.kwargs
        assert "오스코텍 (039200)" in kwargs["text"]
        keyboard = kwargs["reply_markup"].inline_keyboard
        assert keyboard[0][1].callback_data == "fav:add:039200:오스코텍"
