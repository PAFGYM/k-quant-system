"""E2E tests for SchedulerMixin.job_morning_briefing flow.

Verifies the full chain: macro_client.get_snapshot() -> detect_regime() ->
_generate_morning_briefing_v2() -> format_claude_briefing() -> send_message().
Also tests the fallback path and job-run recording.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_macro_snapshot(
    vix: float = 18.5,
    vix_change_pct: float = -1.2,
    spx_change_pct: float = 0.45,
    nasdaq_change_pct: float = 0.72,
    usdkrw: float = 1320.0,
    usdkrw_change_pct: float = -0.3,
    btc_price: float = 62500.0,
    btc_change_pct: float = 1.5,
    gold_price: float = 2350.0,
    gold_change_pct: float = 0.2,
    regime: str = "normal",
) -> MagicMock:
    snap = MagicMock()
    snap.vix = vix
    snap.vix_change_pct = vix_change_pct
    snap.spx_change_pct = spx_change_pct
    snap.nasdaq_change_pct = nasdaq_change_pct
    snap.usdkrw = usdkrw
    snap.usdkrw_change_pct = usdkrw_change_pct
    snap.btc_price = btc_price
    snap.btc_change_pct = btc_change_pct
    snap.gold_price = gold_price
    snap.gold_change_pct = gold_change_pct
    snap.regime = regime
    return snap


def _make_regime_result(
    mode: str = "normal",
    emoji: str = "🟢",
    label: str = "정상",
    message: str = "시장이 안정적입니다",
    allocations: dict | None = None,
) -> MagicMock:
    result = MagicMock()
    result.mode = mode
    result.emoji = emoji
    result.label = label
    result.message = message
    result.allocations = allocations or {"stock": 70, "bond": 20, "cash": 10}
    return result


def _make_holding(
    ticker: str = "005930",
    name: str = "삼성전자",
    buy_price: float = 72000,
    current_price: float = 76000,
    pnl_pct: float = 5.56,
    quantity: int = 10,
    horizon: str = "swing",
    holding_type: str = "stock",
    market: str = "KOSPI",
    buy_date: str = "2026-02-20",
    created_at: str = "2026-02-20 09:00:00",
) -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "buy_price": buy_price,
        "current_price": current_price,
        "pnl_pct": pnl_pct,
        "quantity": quantity,
        "horizon": horizon,
        "holding_type": holding_type,
        "market": market,
        "buy_date": buy_date,
        "created_at": created_at,
    }


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _attach_mixin_attrs(
    mixin,
    *,
    holdings: list[dict] | None = None,
    anthropic_key: str | None = "test-api-key",
    briefing_v2_return: str | None = "좋은 아침입니다 주호님!",
    briefing_v2_side_effect: Exception | None = None,
) -> None:
    """Populate a bare SchedulerMixin instance with mocked collaborators."""
    mixin.chat_id = 6247622742
    mixin.db = MagicMock()
    mixin.db.get_active_holdings = MagicMock(return_value=holdings or [])
    mixin.db.upsert_job_run = MagicMock()
    mixin.macro_client = MagicMock()
    mixin.macro_client.get_snapshot = AsyncMock(return_value=_make_macro_snapshot())
    mixin.anthropic_key = anthropic_key
    if briefing_v2_side_effect:
        mixin._generate_morning_briefing_v2 = AsyncMock(
            side_effect=briefing_v2_side_effect,
        )
    else:
        mixin._generate_morning_briefing_v2 = AsyncMock(
            return_value=briefing_v2_return,
        )


# The functions imported via bot_imports land in the scheduler module namespace.
_PATCH_PREFIX = "kstock.bot.mixins.scheduler"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMorningBriefingSendsMessage:
    """job_morning_briefing should compose a briefing and send it."""

    @pytest.mark.asyncio
    async def test_morning_briefing_sends_message(self) -> None:
        regime = _make_regime_result()
        formatted = "포맷된 브리핑 텍스트입니다."
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}.detect_regime", return_value=regime), \
             patch(f"{_PATCH_PREFIX}.format_claude_briefing", return_value=formatted), \
             patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-25"):
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(mixin, holdings=[_make_holding()])
            await mixin.job_morning_briefing(context)

        context.bot.send_message.assert_called_once()
        call_kw = context.bot.send_message.call_args.kwargs
        assert call_kw["chat_id"] == 6247622742
        assert call_kw["text"] == formatted


class TestMorningBriefingNoHoldings:
    """Briefing should still be sent even if the user has zero holdings."""

    @pytest.mark.asyncio
    async def test_morning_briefing_no_holdings(self) -> None:
        regime = _make_regime_result()
        formatted = "보유종목 없음 브리핑"
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}.detect_regime", return_value=regime), \
             patch(f"{_PATCH_PREFIX}.format_claude_briefing", return_value=formatted), \
             patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-25"):
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(mixin, holdings=[])
            await mixin.job_morning_briefing(context)

        context.bot.send_message.assert_called_once()
        # The mixin should still have queried holdings during briefing generation
        assert context.bot.send_message.call_args.kwargs["text"] == formatted


class TestMorningBriefingWeekendNotSkipped:
    """job_morning_briefing has no internal weekend guard -- it always runs.
    (Weekday filtering is handled by schedule_jobs.)"""

    @pytest.mark.asyncio
    async def test_morning_briefing_runs_on_any_day(self) -> None:
        regime = _make_regime_result()
        formatted = "주말 브리핑 테스트"
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}.detect_regime", return_value=regime), \
             patch(f"{_PATCH_PREFIX}.format_claude_briefing", return_value=formatted), \
             patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-22"):  # Sunday
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(mixin, holdings=[_make_holding()])
            await mixin.job_morning_briefing(context)

        # No weekend skip -- message is still sent
        context.bot.send_message.assert_called_once()


class TestMorningBriefingApiFailure:
    """When _generate_morning_briefing_v2 returns None (no API key) or raises,
    the method should fall back to format_market_status."""

    @pytest.mark.asyncio
    async def test_fallback_when_briefing_v2_returns_none(self) -> None:
        """anthropic_key is None -> _generate_morning_briefing_v2 returns None
        -> falls back to format_market_status."""
        regime = _make_regime_result()
        fallback = "☀️ 오전 브리핑\n\nS&P500 +0.45%"
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}.detect_regime", return_value=regime), \
             patch(f"{_PATCH_PREFIX}.format_market_status", return_value="S&P500 +0.45%") as mock_fms, \
             patch(f"{_PATCH_PREFIX}.format_claude_briefing") as mock_fcb, \
             patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-25"):
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(
                mixin, anthropic_key=None, briefing_v2_return=None,
            )
            await mixin.job_morning_briefing(context)

        # format_claude_briefing should NOT have been called (briefing_text is falsy)
        mock_fcb.assert_not_called()
        # format_market_status should have been used instead
        mock_fms.assert_called_once()
        # Message was still sent
        context.bot.send_message.assert_called_once()
        sent = context.bot.send_message.call_args.kwargs["text"]
        assert "오전 브리핑" in sent

    @pytest.mark.asyncio
    async def test_fallback_when_exception_raised(self) -> None:
        """If get_snapshot or detect_regime raises, the except block
        records an error job run."""
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-25"):
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(mixin)
            # Force get_snapshot to explode
            mixin.macro_client.get_snapshot = AsyncMock(
                side_effect=RuntimeError("network down"),
            )
            await mixin.job_morning_briefing(context)

        # On exception the job is recorded with error status
        mixin.db.upsert_job_run.assert_called_once()
        args = mixin.db.upsert_job_run.call_args
        assert args.args[0] == "morning_briefing"
        assert args.kwargs.get("status") == "error" or args.args[2] == "error"


class TestMorningBriefingRecordsJobRun:
    """upsert_job_run must be called with 'morning_briefing' on success."""

    @pytest.mark.asyncio
    async def test_records_success(self) -> None:
        regime = _make_regime_result()
        formatted = "정상 브리핑"
        context = _make_context()

        with patch(f"{_PATCH_PREFIX}.detect_regime", return_value=regime), \
             patch(f"{_PATCH_PREFIX}.format_claude_briefing", return_value=formatted), \
             patch(f"{_PATCH_PREFIX}._today", return_value="2026-02-25"):
            from kstock.bot.mixins.scheduler import SchedulerMixin

            mixin = SchedulerMixin.__new__(SchedulerMixin)
            _attach_mixin_attrs(mixin, holdings=[_make_holding()])
            await mixin.job_morning_briefing(context)

        mixin.db.upsert_job_run.assert_called_once()
        args = mixin.db.upsert_job_run.call_args
        assert args.args[0] == "morning_briefing"
        assert args.args[1] == "2026-02-25"
        # status should be "success"
        assert args.kwargs.get("status") == "success" or (
            len(args.args) > 2 and args.args[2] == "success"
        )
