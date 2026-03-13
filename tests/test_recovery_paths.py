from __future__ import annotations

from datetime import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _DummyCoreHandlers:
    def __getattr__(self, name):
        stub = AsyncMock()
        setattr(self, name, stub)
        return stub


@pytest.mark.asyncio
async def test_handle_menu_text_routes_stats_alias() -> None:
    from kstock.bot.mixins.core_handlers import CoreHandlersMixin

    class Dummy(CoreHandlersMixin, _DummyCoreHandlers):
        pass

    mixin = Dummy.__new__(Dummy)
    mixin._persist_chat_id = MagicMock()
    mixin.cmd_stats = AsyncMock()

    update = SimpleNamespace(message=SimpleNamespace(text="통계", reply_text=AsyncMock()))
    context = SimpleNamespace(user_data={})

    await mixin.handle_menu_text(update, context)

    mixin.cmd_stats.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_stats_falls_back_to_ops_brief_when_strategy_stats_empty() -> None:
    from kstock.bot.mixins.commands import CommandsMixin

    mixin = CommandsMixin.__new__(CommandsMixin)
    mixin._persist_chat_id = MagicMock()
    mixin.db = MagicMock()
    mixin.db.get_strategy_stats.return_value = []
    mixin.db.get_strategy_performance.return_value = {
        "F": {"win_rate": 60, "avg_pnl": 4.2, "total": 5},
        "summary": {"execution_rate": 80, "avg_hold_days": 6.0, "stop_compliance": 75},
    }
    mixin.db.get_prediction_accuracy.return_value = {"total": 12, "accuracy_pct": 58.3}
    mixin.db.get_ml_performance.return_value = [
        {"model_version": "ensemble-v13", "val_score": 0.62, "features_used": 48}
    ]
    mixin.db.get_recent_alerts.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=6247622742),
    )
    context = SimpleNamespace(bot=MagicMock())

    with patch("kstock.bot.mixins.commands.send_long_bot_message", new=AsyncMock()) as mock_send, \
         patch("kstock.bot.mixins.commands.get_reply_markup", return_value=None):
        await mixin.cmd_stats(update, context)

    mock_send.assert_awaited_once()
    sent_text = mock_send.await_args.args[2]
    assert "운영 통계 브리프" in sent_text
    assert "최근 알림: 3건" in sent_text
    assert "정확도 58.3%" in sent_text


@pytest.mark.asyncio
async def test_quiet_status_brief_sends_when_system_is_silent() -> None:
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.chat_id = 6247622742
    mixin.db = MagicMock()
    mixin.db.get_meta.return_value = None
    mixin.db.get_recent_alerts.return_value = []
    mixin.db.get_active_holdings.return_value = []
    mixin.db.set_meta = MagicMock()
    mixin.db.upsert_job_run = MagicMock()
    mixin._last_scan_results = [
        SimpleNamespace(
            name="한화에어로스페이스",
            ticker="012450",
            score=SimpleNamespace(signal="BUY", composite=88),
            info=SimpleNamespace(change_pct=2.4),
        )
    ]
    mixin.macro_client = MagicMock()
    mixin.macro_client.get_snapshot = AsyncMock(
        return_value=SimpleNamespace(vix=21.5, usdkrw=1450.0)
    )
    mixin._market_signal = MagicMock(return_value=("🟡", "경계"))

    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    class _FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 3, 13, 10, 0, 0, tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return real_datetime(2026, 3, 13, 1, 0, 0)

        @classmethod
        def fromisoformat(cls, date_string: str):
            return real_datetime.fromisoformat(date_string)

    with patch("kstock.bot.mixins.scheduler.datetime", _FakeDateTime), \
         patch("kstock.bot.mixins.scheduler.is_kr_market_open", return_value=True), \
         patch("kstock.bot.mixins.scheduler._today", return_value="2026-03-13"):
        await mixin.job_quiet_status_brief(context)

    context.bot.send_message.assert_awaited_once()
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "상태 브리프" in sent_text
    assert "한화에어로스페이스" in sent_text
