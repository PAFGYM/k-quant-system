from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handle_screenshot_rejects_all_zero_parse_without_saving():
    from kstock.bot.mixins.core_handlers import CoreHandlersMixin

    mixin = CoreHandlersMixin.__new__(CoreHandlersMixin)
    mixin.anthropic_key = "test-anthropic-key"
    mixin.db = MagicMock()
    mixin.db.add_screenshot = MagicMock()
    mixin.db.get_last_screenshot = MagicMock(return_value=None)

    update = MagicMock()
    update.message = MagicMock()
    update.message.caption = ""
    update.message.photo = [SimpleNamespace(file_id="photo-1")]
    update.message.reply_text = AsyncMock()

    file_obj = MagicMock()
    file_obj.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"\x89PNG\r\n\x1a\nfake-image"),
    )

    context = MagicMock()
    context.bot.get_file = AsyncMock(return_value=file_obj)
    context.user_data = {}

    parsed = {
        "holdings": [],
        "summary": {
            "total_eval": 0,
            "total_profit": 0,
            "total_profit_pct": 0.0,
            "cash": 0,
        },
    }

    with patch(
        "kstock.bot.mixins.core_handlers.parse_account_screenshot",
        new=AsyncMock(return_value=parsed),
    ), patch(
        "kstock.bot.mixins.core_handlers.get_reply_markup",
        return_value=None,
    ):
        await CoreHandlersMixin.handle_screenshot(mixin, update, context)

    mixin.db.add_screenshot.assert_not_called()
    assert update.message.reply_text.await_count == 2
    failure_text = update.message.reply_text.await_args_list[-1].args[0]
    assert "저장하지 않았습니다" in failure_text
    assert context.user_data.get("pending_screenshot_id") is None
