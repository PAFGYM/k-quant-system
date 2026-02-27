"""Tests for kstock.bot.chat_handler, context_builder, and chat_memory."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kstock.bot.chat_handler import (
    format_ai_greeting,
    handle_ai_question,
)
from kstock.bot.context_builder import (
    build_system_prompt,
    get_market_context,
    get_portfolio_context,
    get_recommendation_context,
)
from kstock.bot.chat_memory import ChatMemory


# ---------------------------------------------------------------------------
# Mock DB helpers
# ---------------------------------------------------------------------------


class _MockDB:
    """Minimal mock of SQLiteStore for chat tests."""

    def __init__(self, snapshot=None, recs=None, chat_messages=None):
        self._snapshot = snapshot
        self._recs = recs or []
        self._chat_messages = list(chat_messages or [])
        self._usage = 0

    def get_latest_screenshot(self):
        return self._snapshot

    def get_active_holdings(self):
        return []

    def get_active_recommendations(self):
        return self._recs

    def get_chat_usage_count(self, date_str):
        return self._usage

    def increment_chat_usage(self, date_str):
        self._usage += 1

    def add_chat_message(self, role, content):
        self._chat_messages.append({"role": role, "content": content})

    def get_recent_chat_messages(self, limit=10):
        return self._chat_messages[-limit:]

    def cleanup_old_chat_messages(self, hours=24):
        return 0

    def clear_chat_history(self):
        self._chat_messages.clear()


# ===========================================================================
# chat_handler tests
# ===========================================================================


class TestFormatAiGreeting:
    def test_contains_juho(self) -> None:
        greeting = format_ai_greeting()
        assert "주호님" in greeting

    def test_contains_example_questions(self) -> None:
        greeting = format_ai_greeting()
        assert "예시" in greeting
        assert "에코프로" in greeting

    def test_no_bold(self) -> None:
        greeting = format_ai_greeting()
        assert "**" not in greeting


class TestHandleAiQuestion:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_error(self) -> None:
        """When ANTHROPIC_API_KEY is empty, return user-friendly error."""
        db = _MockDB()
        memory = ChatMemory(db)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = await handle_ai_question("테스트 질문", {}, db, memory)
        assert "주호님" in result
        assert "ANTHROPIC_API_KEY" in result


# ===========================================================================
# context_builder tests
# ===========================================================================


class TestBuildSystemPrompt:
    def test_contains_juho(self) -> None:
        prompt = build_system_prompt({})
        assert "주호님" in prompt

    def test_contains_context_sections(self) -> None:
        prompt = build_system_prompt({})
        assert "보유 종목" in prompt
        assert "오늘의 시장" in prompt
        assert "최근 추천" in prompt

    def test_no_bold(self) -> None:
        prompt = build_system_prompt({})
        assert "**" not in prompt

    def test_fills_in_portfolio_data(self) -> None:
        ctx = {"portfolio": "삼성전자: +10%"}
        prompt = build_system_prompt(ctx)
        assert "삼성전자: +10%" in prompt

    def test_missing_keys_use_defaults(self) -> None:
        prompt = build_system_prompt({})
        assert "보유 종목 정보 없음" in prompt
        assert "시장 데이터 없음" in prompt


class TestGetMarketContext:
    def test_with_full_data(self) -> None:
        data = {
            "sp500": 0.69,
            "nasdaq": 0.90,
            "usdkrw": 1444.0,
            "vix": 19.1,
            "btc_price": 64957,
            "gold_price": 5190,
            "us10y": 4.09,
        }
        result = get_market_context(data)
        assert "원/달러" in result
        assert "S&P500" in result
        assert "나스닥" in result

    def test_empty_data(self) -> None:
        result = get_market_context(None)
        assert "시장 데이터 없음" in result

    def test_empty_dict(self) -> None:
        result = get_market_context({})
        assert "시장 데이터 없음" in result

    def test_partial_data(self) -> None:
        result = get_market_context({"sp500": 0.5})
        assert "S&P500" in result
        assert "나스닥" not in result


class TestGetPortfolioContext:
    def test_with_none_snapshot(self) -> None:
        db = _MockDB(snapshot=None)
        result = get_portfolio_context(db)
        assert "보유 종목 정보 없음" in result

    def test_with_valid_snapshot(self) -> None:
        holdings = [
            {
                "name": "삼성전자",
                "avg_price": 70000,
                "current_price": 75000,
                "profit_pct": 7.14,
                "quantity": 10,
            }
        ]
        snapshot = {"holdings_json": json.dumps(holdings)}
        db = _MockDB(snapshot=snapshot)
        result = get_portfolio_context(db)
        assert "삼성전자" in result
        assert "10주" in result

    def test_with_empty_holdings(self) -> None:
        snapshot = {"holdings_json": "[]"}
        db = _MockDB(snapshot=snapshot)
        result = get_portfolio_context(db)
        assert "보유 종목 정보 없음" in result


class TestGetRecommendationContext:
    def test_with_no_recs(self) -> None:
        db = _MockDB(recs=[])
        result = get_recommendation_context(db)
        assert "최근 추천 없음" in result

    def test_with_recs(self) -> None:
        recs = [
            {"name": "에코프로", "rec_price": 90000, "pnl_pct": 12.5, "rec_date": "2026-02-20"},
        ]
        db = _MockDB(recs=recs)
        result = get_recommendation_context(db)
        assert "에코프로" in result
        assert "90,000" in result

    def test_limits_output(self) -> None:
        recs = [
            {"name": f"종목{i}", "rec_price": 10000, "pnl_pct": 0, "rec_date": "2026-02-20"}
            for i in range(10)
        ]
        db = _MockDB(recs=recs)
        result = get_recommendation_context(db, limit=3)
        # Should only have 3 lines (each starts with "- ")
        lines = [l for l in result.splitlines() if l.startswith("- ")]
        assert len(lines) == 3


# ===========================================================================
# chat_memory tests
# ===========================================================================


class TestChatMemory:
    def test_init(self) -> None:
        db = _MockDB()
        memory = ChatMemory(db)
        assert memory.db is db

    def test_add_and_get_recent(self) -> None:
        db = _MockDB()
        memory = ChatMemory(db)
        memory.add("user", "안녕하세요")
        memory.add("assistant", "주호님, 안녕하세요!")
        msgs = memory.get_recent(limit=10)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_clear(self) -> None:
        db = _MockDB()
        memory = ChatMemory(db)
        memory.add("user", "테스트")
        memory.clear()
        assert memory.message_count() == 0

    def test_message_count(self) -> None:
        db = _MockDB()
        memory = ChatMemory(db)
        memory.add("user", "1")
        memory.add("assistant", "2")
        memory.add("user", "3")
        assert memory.message_count() == 3

    def test_methods_exist(self) -> None:
        db = _MockDB()
        memory = ChatMemory(db)
        assert callable(getattr(memory, "add", None))
        assert callable(getattr(memory, "get_recent", None))
        assert callable(getattr(memory, "cleanup", None))
        assert callable(getattr(memory, "clear", None))
        assert callable(getattr(memory, "message_count", None))
