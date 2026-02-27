"""E2E tests for TradingMixin buy-cart flow (_action_buy_plan callbacks).

The buy cart lifecycle:
  bp:yes -> user enters amount -> bp:view:{horizon} -> bp:add:{ticker}:{horizon}
  -> bp:done -> bp:confirm
Also: bp:no, bp:cancel, bp:ai, bp:dismiss

_action_buy_plan(self, query, context, payload) receives the portion
after "bp:" as `payload`.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_query() -> MagicMock:
    """Create a mock CallbackQuery."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    query.message.chat_id = 6247622742
    return query


def _make_context(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _make_pick(
    ticker: str = "005930",
    name: str = "삼성전자",
    price: float = 76000,
    score: float = 85.0,
    reason: str = "AI 추천",
    horizon: str = "scalp",
    amount: int = 760000,
) -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "price": price,
        "score": score,
        "reason": reason,
        "horizon": horizon,
        "amount": amount,
    }


def _attach_trading_attrs(mixin, *, holdings=None) -> None:
    """Populate a bare TradingMixin with mocked collaborators."""
    mixin.chat_id = 6247622742
    mixin.db = MagicMock()
    mixin.db.get_active_holdings = MagicMock(return_value=holdings or [])
    mixin.db.add_holding = MagicMock()
    mixin._show_cart_menu = AsyncMock()
    mixin._show_horizon_picks = AsyncMock()
    mixin._add_to_cart = AsyncMock()
    mixin._show_cart_summary = AsyncMock()
    mixin._confirm_cart = AsyncMock()
    mixin._show_ai_recommendation = AsyncMock()


# Patch prefix for functions imported via bot_imports wildcard
_PATCH_PREFIX = "kstock.bot.mixins.trading"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuyCartStartPromptsAmount:
    """payload='yes' should show amount selection buttons (v5.2+)."""

    @pytest.mark.asyncio
    async def test_yes_prompts_budget_input(self) -> None:
        query = _make_query()
        context = _make_context()

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "yes")

        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "금액" in sent, f"Expected '금액' in prompt, got: {sent!r}"
        # v5.2: shows amount buttons with reply_markup, not awaiting_buy_amount
        kwargs = query.edit_message_text.call_args.kwargs
        assert "reply_markup" in kwargs, "Expected InlineKeyboard with amount buttons"

    @pytest.mark.asyncio
    async def test_yes_custom_amount_sets_awaiting_flag(self) -> None:
        """payload='amt:custom' sets awaiting_buy_amount for text input."""
        query = _make_query()
        context = _make_context()

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "amt:custom")

        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "만원" in sent
        assert context.user_data.get("awaiting_buy_amount") is True

    @pytest.mark.asyncio
    async def test_start_also_prompts_budget_input(self) -> None:
        """payload='start' follows the same path as 'yes'."""
        query = _make_query()
        context = _make_context()

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "start")

        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "금액" in sent

    @pytest.mark.asyncio
    async def test_amt_button_shows_type_selection(self) -> None:
        """payload='amt:100' should show investment type buttons."""
        query = _make_query()
        context = _make_context()

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "amt:100")

        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "100만원" in sent
        assert "투자 타입" in sent
        kwargs = query.edit_message_text.call_args.kwargs
        assert "reply_markup" in kwargs


class TestBuyCartViewHorizon:
    """payload='view:scalp' delegates to _show_horizon_picks."""

    @pytest.mark.asyncio
    async def test_view_scalp_shows_picks(self) -> None:
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {"budget": 1000000, "remaining": 1000000, "items": []},
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "view:scalp")

        mixin._show_horizon_picks.assert_awaited_once_with(query, context, "scalp")


class TestBuyCartAddItem:
    """payload='add:005930:scalp' delegates to _add_to_cart."""

    @pytest.mark.asyncio
    async def test_add_delegates_to_add_to_cart(self) -> None:
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {"budget": 1000000, "remaining": 1000000, "items": []},
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "add:005930:scalp")

        mixin._add_to_cart.assert_awaited_once_with(query, context, "005930", "scalp")


class TestBuyCartConfirm:
    """payload='confirm' delegates to _confirm_cart which registers holdings."""

    @pytest.mark.asyncio
    async def test_confirm_delegates_to_confirm_cart(self) -> None:
        cart_items = [
            {"ticker": "005930", "name": "삼성전자", "price": 76000,
             "quantity": 13, "horizon": "scalp", "market": "KOSPI",
             "amount": 988000},
        ]
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {
                "budget": 1000000, "remaining": 12000,
                "items": cart_items, "active": True,
            },
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "confirm")

        mixin._confirm_cart.assert_awaited_once_with(query, context)

    @pytest.mark.asyncio
    async def test_done_shows_summary_before_confirm(self) -> None:
        """payload='done' shows the cart summary (pre-confirm review)."""
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {
                "budget": 1000000, "remaining": 240000,
                "items": [{"ticker": "005930", "name": "삼성전자"}],
            },
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "done")

        mixin._show_cart_summary.assert_awaited_once_with(query, context)


class TestBuyCartCancel:
    """payload='cancel' should clear buy_cart and related keys from user_data."""

    @pytest.mark.asyncio
    async def test_cancel_clears_cart(self) -> None:
        user_data: dict = {
            "buy_cart": {
                "budget": 1000000, "remaining": 240000,
                "items": [{"ticker": "005930"}],
            },
            "_horizon_picks": [{"ticker": "005930"}],
            "_ai_picks": [{"ticker": "000660"}],
        }
        query = _make_query()
        context = _make_context(user_data=user_data)

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "cancel")

        # buy_cart should be removed
        assert "buy_cart" not in context.user_data
        # Related state should also be cleaned
        assert "_horizon_picks" not in context.user_data
        assert "_ai_picks" not in context.user_data
        # Confirmation message
        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "취소" in sent

    @pytest.mark.asyncio
    async def test_no_dismisses_politely(self) -> None:
        """payload='no' just sends a polite dismissal, no cart cleanup needed."""
        query = _make_query()
        context = _make_context()

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "no")

        query.edit_message_text.assert_called_once()
        sent = query.edit_message_text.call_args.args[0]
        assert "주호님" in sent


class TestBuyCartBudgetOverflow:
    """Adding an item that exceeds remaining budget should be handled by
    _add_to_cart (the budget check lives there, not in _action_buy_plan).
    We verify the delegation happens with the correct arguments."""

    @pytest.mark.asyncio
    async def test_add_with_tight_budget_still_delegates(self) -> None:
        """Even if budget is nearly exhausted, _action_buy_plan delegates to
        _add_to_cart which is responsible for the overflow check."""
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {
                "budget": 500000,
                "remaining": 10000,  # only 1만원 left
                "items": [
                    {"ticker": "000660", "name": "SK하이닉스", "price": 180000,
                     "quantity": 2, "horizon": "scalp", "amount": 360000},
                ],
            },
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "add:005930:scalp")

        # _add_to_cart is called regardless -- it handles the budget logic
        mixin._add_to_cart.assert_awaited_once_with(query, context, "005930", "scalp")

    @pytest.mark.asyncio
    async def test_addall_skips_items_exceeding_remaining(self) -> None:
        """payload='addall' adds AI picks but skips those exceeding remaining budget."""
        ai_picks = [
            _make_pick(ticker="005930", name="삼성전자", amount=760000),
            _make_pick(ticker="000660", name="SK하이닉스", amount=900000),
        ]
        query = _make_query()
        context = _make_context(user_data={
            "buy_cart": {
                "budget": 1000000, "remaining": 800000,
                "items": [], "active": True,
            },
            "_ai_picks": ai_picks,
        })

        from kstock.bot.mixins.trading import TradingMixin

        mixin = TradingMixin.__new__(TradingMixin)
        _attach_trading_attrs(mixin)

        await mixin._action_buy_plan(query, context, "addall")

        cart = context.user_data["buy_cart"]
        # Only the first pick (760,000) fits within 800,000 remaining
        assert len(cart["items"]) == 1
        assert cart["items"][0]["ticker"] == "005930"
        # Remaining should be updated
        assert cart["remaining"] == 800000 - 760000
        # AI picks should be cleaned up
        assert "_ai_picks" not in context.user_data
        # Confirmation shown + cart menu displayed
        query.edit_message_text.assert_called_once()
        mixin._show_cart_menu.assert_awaited_once()
