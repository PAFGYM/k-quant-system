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


def test_ai_router_disables_claude_prompt_cache_for_high_churn_tasks():
    from kstock.bot.ai_router import AIRouter

    assert AIRouter._should_use_claude_prompt_cache(
        "morning_briefing",
        "S" * 400,
        "P" * 1200,
    ) is False
    assert AIRouter._should_use_claude_prompt_cache(
        "strategy_synthesis",
        "S" * 400,
        "P" * 1200,
    ) is True


def test_split_long_text_paginates_without_loss():
    from kstock.bot.bot_imports import split_long_text

    text = "\n".join(f"line {i}" for i in range(1200))
    pages = split_long_text(text, page_size=300)

    assert len(pages) > 1
    assert all(len(page) <= 300 for page in pages)
    merged = "\n".join(pages)
    assert "line 0" in merged
    assert "line 1199" in merged


def test_ai_router_budget_guard_respects_global_hard_cap(monkeypatch):
    from kstock.bot.ai_router import AIRouter

    class DummyDB:
        @staticmethod
        def get_daily_api_usage(_date: str) -> dict:
            return {"total_cost": 2.1}

        @staticmethod
        def get_monthly_api_usage(_year_month: str) -> dict:
            return {"total_cost": 12.0}

    monkeypatch.setenv("KQ_GLOBAL_DAILY_HARD_BUDGET_USD", "2.0")

    with patch("kstock.bot.ai_router.get_db", return_value=DummyDB()):
        router = AIRouter()
        tier, max_tokens = router._apply_cost_guard(
            "deep_analysis", "claude", "standard", 1500,
        )

    assert tier == "fast"
    assert max_tokens == 500


def test_track_usage_skips_zero_token_success_logs():
    from kstock.core.token_tracker import track_usage

    db = MagicMock()

    track_usage(
        db=db,
        provider="gemini",
        model="gemini-2.0-flash",
        function_name="youtube_summary_flash",
        input_tokens=0,
        output_tokens=0,
    )

    db.log_api_usage.assert_not_called()
