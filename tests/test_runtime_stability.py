from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_safe_edit_or_reply_skips_duplicate_reply_on_not_modified():
    from kstock.bot.bot_imports import safe_edit_or_reply

    query = MagicMock()
    query.edit_message_text = AsyncMock(
        side_effect=Exception("Message is not modified"),
    )
    query.message.reply_text = AsyncMock()

    await safe_edit_or_reply(query, "same text")

    query.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_safe_edit_or_reply_falls_back_when_edit_is_unavailable():
    from kstock.bot.bot_imports import safe_edit_or_reply

    query = MagicMock()
    query.edit_message_text = AsyncMock(
        side_effect=Exception("message can't be edited"),
    )
    query.message.reply_text = AsyncMock()

    await safe_edit_or_reply(query, "fallback text")

    query.message.reply_text.assert_called_once()


def test_ai_router_budget_guard_downgrades_expensive_calls(monkeypatch):
    from kstock.bot.ai_router import AIRouter

    class DummyDB:
        @staticmethod
        def get_daily_api_usage(_date: str) -> dict:
            return {"total_cost": 1.2}

        @staticmethod
        def get_monthly_api_usage(_year_month: str) -> dict:
            return {"total_cost": 12.0}

    monkeypatch.setenv("AI_DAILY_SOFT_BUDGET_USD", "1.0")
    monkeypatch.setenv("AI_MONTHLY_SOFT_BUDGET_USD", "20.0")

    with patch("kstock.bot.ai_router.get_db", return_value=DummyDB()):
        router = AIRouter()
        tier, max_tokens = router._apply_cost_guard(
            "sector_analysis", "gpt", "standard", 1200,
        )

    assert tier == "fast"
    assert max_tokens == 450
